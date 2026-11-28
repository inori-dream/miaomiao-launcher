"""Validate decoded captures and import one UID without replacing existing saves."""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
import uuid
import zipfile
from contextlib import closing

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from capture_zip import (
    archive_sha256,
    captured_uid,
    find_capture_prefix,
    open_secure_zip,
    read_capture_entries,
    read_capture_summary,
    snapshot_archive,
    uint32,
    validate_archive_contents,
)
from privacy_check import redact_result, redact_value, reject_secrets, safe_error, scan_text

BASE = Path(__file__).resolve().parent
SCHEMA = json.loads((BASE / 'model_schema.json').read_text(encoding='utf-8'))
ENUMS = SCHEMA['enums']
_IDENTIFIER = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
MAX_INDEX_FILE_BYTES = 32 * 1024 * 1024
AREA_STATES = {'Close': 0, 'AreaState_Close': 0, 'Unlock': 1, 'AreaState_Unlock': 1}
AREA_COLLECT_FIELDS = (
    'CollectFileID', 'CollectItemID', 'CollectBookID',
    'CollectTapeID', 'CollectPortalID', 'CollectDataID',
)


def normalize(value):
    return re.sub('[^a-z0-9]', '', value.lower())


PLACEHOLDER = re.compile(r'^(field_\d+|_bytes|unknown_field.*)$')


def is_placeholder(value):
    """A dict carrying only protobuf fallback fields means the message was not decoded."""
    if not isinstance(value, dict) or not value:
        return False
    return all(isinstance(key, str) and PLACEHOLDER.match(key) for key in value)


def placeholder_hits(value, budget=None):
    """Count fallback fields, failing closed if the traversal budget is exhausted."""
    if budget is None:
        budget = [100_000]
    if budget[0] <= 0:
        raise ValueError('Capture content is too complex to validate safely.')
    budget[0] -= 1
    if isinstance(value, dict):
        total = sum(1 for key in value if isinstance(key, str) and PLACEHOLDER.match(key))
        return total + sum(placeholder_hits(item, budget) for item in value.values())
    if isinstance(value, list):
        return sum(placeholder_hits(item, budget) for item in value)
    return 0


def validate_field_conflicts(value) -> None:
    """Reject aliases that normalize to one field instead of silently taking the last."""
    stack = [value]
    nodes = 0
    while stack:
        current = stack.pop()
        nodes += 1
        if nodes > 200_000:
            raise ValueError('Capture content is too complex to validate safely.')
        if isinstance(current, dict):
            seen = {}
            for key, item in current.items():
                if not isinstance(key, str):
                    continue
                normalized = normalize(key)
                # Non-ASCII protobuf map keys are opaque; do not collapse them.
                if normalized and re.search(r'[a-z0-9]', key, re.I):
                    if normalized in seen and seen[normalized] != key:
                        raise ValueError('Capture contains conflicting field names.')
                    seen[normalized] = key
                stack.append(item)
        elif isinstance(current, list):
            stack.extend(current)


def as_list(value):
    """Accept a list, a single object, a protobuf map or nothing."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        if is_placeholder(value):
            return []
        if value and all(isinstance(item, dict) for item in value.values()):
            return list(value.values())
        return [value]
    return []


def as_mapping(value):
    """Accept a protobuf map or a one-element list; return a plain dict."""
    if isinstance(value, dict):
        return value
    values = as_list(value)
    return values[0] if len(values) == 1 and isinstance(values[0], dict) else {}


def as_int(value, default=0):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
        return ENUMS.get(stripped, default)
    return default


def _equipment_instance(value, label):
    """Normalize an equipment reference without leaking container TypeErrors."""
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f'{label} must be an integer.') from error


def _same_value(first, second) -> bool:
    if type(first) is not type(second):
        return False
    if isinstance(first, dict):
        return first.keys() == second.keys() and all(_same_value(first[key], second[key]) for key in first)
    if isinstance(first, list):
        return len(first) == len(second) and all(_same_value(a, b) for a, b in zip(first, second))
    return first == second


def _put_unique(mapping, key, value, label='entries') -> None:
    key = str(key)
    if key in mapping and not _same_value(mapping[key], value):
        raise ValueError(f'Capture contains conflicting {label}.')
    mapping[key] = value


def _merge_unique(mapping, key, value, label='entries') -> None:
    key = str(key)
    if key not in mapping:
        mapping[key] = value
        return
    merged = dict(mapping[key])
    for field, item in value.items():
        if field in merged and not _same_value(merged[field], item):
            raise ValueError(f'Capture contains conflicting {label}.')
        merged[field] = item
    mapping[key] = merged


def model(kind, data):
    if data is None:
        return None
    if kind.startswith('*'):
        return model(kind[1:], data)
    if kind.startswith('[]'):
        if not isinstance(data, list):
            raise ValueError('Capture contains a malformed repeated field.')
        return [model(kind[2:], value) for value in data]
    if kind.startswith('map['):
        if not isinstance(data, dict):
            raise ValueError('Capture contains a malformed map field.')
        nested = kind.split(']', 1)[1]
        result = {}
        for key, value in data.items():
            _put_unique(result, key, model(nested, value), 'map fields')
        return result
    if kind in SCHEMA['models'] and isinstance(data, dict):
        normalized = {}
        for key, value in data.items():
            if not isinstance(key, str):
                raise ValueError('Capture contains an invalid field name.')
            lookup = normalize(key)
            if lookup in normalized and normalized[lookup] != key:
                raise ValueError('Capture contains conflicting field names.')
            normalized[lookup] = key
        result = {}
        for field in SCHEMA['models'][kind]:
            if 'embedded' in field:
                embedded = model(field['embedded'], data)
                for key, value in embedded.items():
                    if key in result:
                        raise ValueError('Capture contains conflicting embedded fields.')
                    result[key] = value
                continue
            lookup = normalize(field['key'])
            if lookup in normalized:
                key = normalized[lookup]
                result[field['key']] = model(field['type'], data[key])
        return result
    if kind.startswith(('uint', 'int', 'proto.')):
        if isinstance(data, bool) or not isinstance(data, (int, str)):
            raise ValueError('Capture contains an invalid numeric field.')
        if isinstance(data, str):
            if data in ENUMS:
                return ENUMS[data]
            if not re.fullmatch(r'-?\d+', data.strip()):
                raise ValueError('Capture contains an invalid numeric field.')
            value = int(data)
        else:
            value = data
        if kind.startswith('uint') and kind[4:].isdigit():
            bits = int(kind[4:])
            if value < 0 or value > (1 << bits) - 1:
                raise ValueError(f'Capture contains a {kind} field outside its supported range.')
        elif kind.startswith('int') and kind[3:].isdigit():
            bits = int(kind[3:])
            if value < -(1 << (bits - 1)) or value > (1 << (bits - 1)) - 1:
                raise ValueError(f'Capture contains an {kind} field outside its supported range.')
        elif kind.startswith('proto.') and (value < -(1 << 31) or value > (1 << 31) - 1):
            raise ValueError('Capture contains an enum field outside its supported range.')
        return value
    if kind == 'bool':
        # The server stores these as real JSON booleans; a number fails GPB
        # deserialization with "mismatch type bool with number at index ...".
        if isinstance(data, bool):
            return data
        if isinstance(data, (int, float)):
            return bool(data)
        if isinstance(data, str):
            lowered = data.strip().lower()
            if lowered in ('true', '1'):
                return True
            if lowered in ('false', '0', ''):
                return False
            if lowered in ENUMS:
                return bool(ENUMS[lowered])
        return bool(data)
    if kind == 'RandomPropertys':
        if not isinstance(data, list):
            raise ValueError('Capture contains malformed random properties.')
        return [model('RandomProperty', value) for value in data]
    if isinstance(data, dict):
        return {key: model('', value) for key, value in data.items()}
    if isinstance(data, list):
        return [model('', value) for value in data]
    return ENUMS.get(data, data) if isinstance(data, str) else data


def _select_player(entries, allow_missing_uid: bool):
    main = entries.get('PlayerMainDataRsp', [])
    if not main:
        raise ValueError('Capture must contain PlayerMainDataRsp.')
    identities = set()
    for value in main:
        if not isinstance(value, dict):
            raise ValueError('Capture contains malformed player data.')
        if 'player_id' not in value and 'player_label' not in value:
            identities.add(None)
            continue
        internal = uint32(value.get('player_id') or value.get('player_label') or 0,
                          allow_zero=True, label='Internal player ID')
        public = uint32(value.get('player_label') or internal or 0,
                        allow_zero=True, label='Account UID')
        identities.add((internal, public))
    if len(identities) != 1:
        raise ValueError('Capture must contain one unambiguous player identity.')
    identity = next(iter(identities))
    if identity is None and not allow_missing_uid:
        raise ValueError('Capture must contain a verifiable UID.')
    player = main[-1]
    if player.get('status') not in (1, 'StatusCode_OK'):
        raise ValueError('PlayerMainDataRsp was not successful.')
    return player


def read_capture_archive(archive: zipfile.ZipFile, allow_missing_uid: bool = False):
    prefix = find_capture_prefix(archive)
    entries = read_capture_entries(archive, prefix)
    player = _select_player(entries, allow_missing_uid)
    validate_field_conflicts([player, entries])
    return player, entries


def read_capture(path, allow_missing_uid: bool = False):
    with open_secure_zip(path) as archive:
        validate_archive_contents(archive)
        return read_capture_archive(archive, allow_missing_uid)


def inspect(path):
    path = Path(path)
    player, entries = read_capture(path)
    uid = captured_uid(player)
    if uid <= 0:
        raise ValueError('Capture must contain a verifiable UID.')
    quests = as_list(as_mapping(player.get('quest_detail')).get('quests'))
    return {
        'uid': uid,
        'internalId': uint32(player.get('player_id') or uid, allow_zero=True, label='Internal player ID'),
        'name': player.get('player_name', ''),
        'level': player.get('level', 1),
        'characters': len(as_list(player.get('characters'))),
        'quests': len(quests),
        'capturedMessages': sum(len(value) for value in entries.values()),
        'sha256': archive_sha256(path),
    }


def _quoted_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _unsafe_schema_sql(sql: str) -> bool:
    # A view may defer an unsafe function until first use, after publication.
    # Strip comments before checking so whitespace/comments cannot hide calls.
    stripped = re.sub(r'/\*.*?\*/', ' ', sql, flags=re.S)
    stripped = re.sub(r'--[^\r\n]*', ' ', stripped)
    return bool(
        re.search(r'\b(?:load_extension|readfile|writefile)\s*\(', stripped, re.I)
        or re.search(r'\bATTACH\s+DATABASE\b|\bDETACH\s+DATABASE\b|\bPRAGMA\b|\bVACUUM\s+INTO\b',
                     stripped, re.I)
    )


def _no_link(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, 'is_junction', lambda: False)())


def _publish_temp_file(temporary: Path, destination: Path) -> None:
    """Hardlink a validated temporary file, falling back cleanly on exFAT/FAT32."""
    try:
        os.link(temporary, destination)
        temporary.unlink()
        return
    except OSError:
        if destination.exists():
            raise FileExistsError(destination)
        staged = destination.with_name(f'.{destination.name}.{uuid.uuid4().hex}.tmp')
        try:
            shutil.copy2(temporary, staged)
            if destination.exists():
                raise FileExistsError(destination)
            os.replace(staged, destination)
            temporary.unlink(missing_ok=True)
        finally:
            staged.unlink(missing_ok=True)


def schema_database(source, target):
    """Publish an empty, validated schema while retaining migration objects."""
    source = Path(source).resolve()
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or _no_link(target) or _no_link(target.parent):
        raise FileExistsError(target)
    task_root = target.parent / '.schema-tasks'
    task_root.mkdir(exist_ok=True)
    if _no_link(task_root):
        raise ValueError('Unsafe SQLite schema task directory.')
    task = Path(tempfile.mkdtemp(prefix='task-', dir=task_root))
    temporary = task / f'.{target.name}.{uuid.uuid4().hex}.tmp'
    try:
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as origin:
            origin.execute('PRAGMA query_only=ON')
            origin.execute('PRAGMA trusted_schema=OFF')
            if origin.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise ValueError('SQLite source validation failed.')
            table_list = origin.execute('PRAGMA table_list').fetchall()
            if any(len(row) >= 3 and row[2] in ('shadow', 'virtual') for row in table_list):
                raise ValueError('SQLite schema contains unsupported virtual or shadow tables.')
            user_version = origin.execute('PRAGMA user_version').fetchone()[0]
            application_id = origin.execute('PRAGMA application_id').fetchone()[0]
            objects = []
            for object_type in ('table', 'index', 'view', 'trigger'):
                for name, sql in origin.execute(
                    "SELECT name, sql FROM sqlite_master WHERE type=? AND sql IS NOT NULL ORDER BY name",
                    (object_type,),
                ):
                    if object_type == 'table' and name.startswith('sqlite_'):
                        continue
                    if scan_text(sql) or _unsafe_schema_sql(sql):
                        raise ValueError('SQLite schema contains sensitive or unsafe SQL.')
                    objects.append((object_type, name, sql))
            with closing(sqlite3.connect(temporary)) as database:
                database.execute('PRAGMA foreign_keys=ON')
                database.execute('PRAGMA trusted_schema=OFF')
                with database:
                    for _object_type, _name, sql in objects:
                        try:
                            database.execute(sql)
                        except sqlite3.Error as error:
                            raise ValueError('SQLite schema contains unsupported or unsafe objects.') from error
                    database.execute(f'PRAGMA user_version={int(user_version)}')
                    database.execute(f'PRAGMA application_id={int(application_id)}')
                published = {
                    (row[0], row[1]): row[2]
                    for row in database.execute(
                        "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
                    )
                }
                expected = {(kind, name): sql for kind, name, sql in objects}
                if published != expected:
                    raise ValueError('SQLite schema migration validation failed.')
                if any(len(row) >= 3 and row[2] in ('shadow', 'virtual')
                       for row in database.execute('PRAGMA table_list')):
                    raise ValueError('SQLite schema migration created virtual or shadow tables.')
                for kind, name, _sql in objects:
                    if kind == 'table':
                        count = database.execute(
                            f'SELECT COUNT(*) FROM {_quoted_identifier(name)}'
                        ).fetchone()[0]
                        if count:
                            raise ValueError('SQLite schema migration would publish account data.')
                if database.execute('PRAGMA foreign_key_check').fetchone() is not None:
                    raise ValueError('SQLite schema contains invalid foreign keys.')
                tables = {name for kind, name, _sql in objects if kind == 'table'}
                for table in tables:
                    for foreign_key in database.execute(
                        f'PRAGMA foreign_key_list({_quoted_identifier(table)})'
                    ):
                        if foreign_key[2] not in tables:
                            raise ValueError('SQLite schema contains an invalid foreign key target.')
                if database.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                    raise ValueError('SQLite schema validation failed.')
                if database.execute('PRAGMA user_version').fetchone()[0] != user_version:
                    raise ValueError('SQLite user_version migration failed.')
                if database.execute('PRAGMA application_id').fetchone()[0] != application_id:
                    raise ValueError('SQLite application_id migration failed.')
        _publish_temp_file(temporary, target)
    except Exception:
        for suffix in ('', '-journal', '-wal', '-shm'):
            Path(str(temporary) + suffix).unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(task, ignore_errors=True)
        try:
            task_root.rmdir()
        except OSError:
            pass


def ensure_uid(database, uid):
    uid = uint32(uid, label='UID')
    with closing(sqlite3.connect(database, timeout=10)) as connection:
        connection.execute('PRAGMA trusted_schema=OFF')
        connection.execute('BEGIN IMMEDIATE')
        try:
            row = connection.execute('SELECT sdk_uid FROM of_user WHERE user_id=?', (uid,)).fetchone()
            if row:
                connection.commit()
                return int(row[0]) if row[0] is not None else uid
            if connection.execute('SELECT 1 FROM of_user WHERE sdk_uid=?', (uid,)).fetchone():
                raise ValueError('UID conflicts with an existing account mapping.')
            connection.execute('INSERT INTO of_user(user_id,sdk_uid,ban) VALUES(?,?,0)', (uid, uid))
            connection.commit()
            return uid
        except Exception:
            connection.rollback()
            raise


def garden_vector(value):
    """The server stores Vector3 as plain {"x","y","z"} int32 (no omitempty)."""
    value = value if isinstance(value, dict) else {}
    return {axis: as_int(value.get(axis)) for axis in ('x', 'y', 'z')}


def build_garden(main, entries):
    """Build the of_home row the Lolo server reads when a player enters their garden.

    The server keeps the garden in its own table: of_home.garden_furniture_info_map
    holds a sonic-serialised map[int64]*FurnitureDetailsInfo, and
    placed_character_map a serialised []*ScenePlacedCharacter. Skipping this table
    made every imported account show an empty garden no matter how much furniture
    the capture carried, so the layout is rebuilt here from the player's own 9999
    snapshot.
    """
    uid = as_int(main.get('player_label')) or as_int(main.get('player_id'))
    internal = as_int(main.get('player_id')) or uid
    if not uid:
        return None

    # The player's own garden is the 9999 snapshot whose channel is this account.
    best = None
    for notice in as_list(entries.get('SceneDataNotice')):
        data = (notice or {}).get('data') if isinstance(notice, dict) else None
        if not isinstance(data, dict) or as_int(data.get('scene_id')) != 9999:
            continue
        channel = as_int(data.get('channel_id'))
        label = as_int(data.get('channel_label'))
        if uid not in (channel, label) and internal not in (channel, label):
            continue
        garden = data.get('scene_garden_data')
        garden = garden if isinstance(garden, dict) else {}
        furniture = as_mapping(garden.get('garden_furniture_info_map'))
        if best is None or len(furniture) > len(best[1]):
            best = (data, furniture, garden)

    if best is None:
        return None
    data, furniture, garden = best

    items = {}
    for key, detail in furniture.items():
        if not isinstance(detail, dict):
            continue
        furniture_id = as_int(detail.get('furniture_id'), as_int(key))
        if furniture_id <= 0:
            continue
        stored = {
            'furnitureId': furniture_id,
            'furnitureItemId': as_int(detail.get('furniture_item_id')),
            'pos': garden_vector(detail.get('pos')),
            'rotation': garden_vector(detail.get('rotation')),
        }
        layer = as_int(detail.get('layer_num'))
        if layer:
            stored['layerNum'] = layer
        items[str(furniture_id)] = stored

    placed = [character for character in as_list(data.get('placed_characters'))
              if isinstance(character, dict)]

    return {
        'scene_id': 9999,
        'garden_name': garden.get('garden_name') or '',
        'likes_num': as_int(garden.get('likes_num')),
        'access_player_num': as_int(garden.get('access_player_num')),
        'left_like_num': as_int(garden.get('left_like_num')),
        'is_open': 1 if garden.get('is_open') else 0,
        'password': garden.get('password') or '',
        'password_expire_time': as_int(garden.get('password_expire_time')),
        'furniture': items,
        'placed': placed,
    }


def _read_bounded_json(path: Path, limit: int | None = None):
    path = Path(path)
    if _no_link(path) or not path.is_file():
        return None
    cap = MAX_INDEX_FILE_BYTES if limit is None else limit
    if path.stat().st_size > cap:
        raise ValueError(f'Index file exceeds size limit: {path.name}')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def _locate_exploration_catalog() -> Path | None:
    candidates = (
        BASE / 'exploration_catalog.json',
        BASE / 'backup-tool' / 'exploration_catalog.json',
        BASE.parent / 'backup-tool' / 'exploration_catalog.json',
    )
    for candidate in candidates:
        if _no_link(candidate):
            raise ValueError('Unsafe exploration catalog path.')
        if candidate.is_file():
            if candidate.parent != BASE and _no_link(candidate.parent):
                raise ValueError('Unsafe exploration catalog directory.')
            return candidate
    return None


def _load_exploration_index(resource_dir: Path | None = None) -> dict:
    """Load scene/collection/area/box lookup indexes from static and server tables."""
    item_to_scene = {}
    scene_boxes = {}
    catalog_path = _locate_exploration_catalog()
    if catalog_path is not None:
        catalog = _read_bounded_json(catalog_path)
        if isinstance(catalog, dict):
            for scene_key, row in as_mapping(catalog.get('maps')).items():
                if not str(scene_key).isdigit() or not isinstance(row, dict):
                    continue
                scene_id = uint32(int(scene_key), label='Catalog scene ID')
                for kind_key, item_ids in as_mapping(row.get('collections')).items():
                    if not str(kind_key).isdigit():
                        continue
                    kind = uint32(int(kind_key), label='Collection type')
                    for raw_id in as_list(item_ids):
                        item_id = as_int(raw_id)
                        if item_id > 0:
                            item_to_scene.setdefault(item_id, (scene_id, kind))
                boxes = {}
                for index_key, box_id in as_mapping(row.get('boxes')).items():
                    if str(index_key).isdigit() and as_int(box_id) > 0:
                        boxes[int(index_key)] = int(box_id)
                if boxes:
                    scene_boxes[scene_id] = boxes

    scene_areas = {}
    if resource_dir is not None:
        resource_dir = Path(resource_dir)
        excel_dir = resource_dir / 'Excel'
        if (resource_dir.is_dir() and not _no_link(resource_dir)
                and excel_dir.is_dir() and not _no_link(excel_dir)):
            scene_json = _read_bounded_json(excel_dir / 'Scene.json')
            area_json = _read_bounded_json(excel_dir / 'Area.json')
            collect_json = _read_bounded_json(excel_dir / 'CollectionItem.json')
            collect_types = {}
            if isinstance(collect_json, dict):
                collect_types = {
                    as_int(row.get('ID')): as_int(row.get('NewCollectionType'))
                    for row in as_list(as_mapping(collect_json.get('CollectionItem')).get('Datas'))
                    if isinstance(row, dict) and as_int(row.get('ID')) > 0 and 1 <= as_int(row.get('NewCollectionType')) <= 9
                }
            if isinstance(scene_json, dict) and isinstance(area_json, dict):
                area_by_group = {
                    as_int(row.get('ID')): row.get('AreaDataGroup', [])
                    for row in as_list(as_mapping(area_json.get('AreaData')).get('Datas'))
                    if isinstance(row, dict) and as_int(row.get('ID')) > 0
                }
                rel_by_pool = {
                    as_int(row.get('ID')): row.get('AreaRelationDataGroup', [])
                    for row in as_list(as_mapping(area_json.get('AreaRelationData')).get('Datas'))
                    if isinstance(row, dict) and as_int(row.get('ID')) > 0
                }
                for row in as_list(as_mapping(scene_json.get('Scene')).get('Datas')):
                    if not isinstance(row, dict):
                        continue
                    scene_id = as_int(row.get('ID'))
                    area_group_id = as_int(row.get('AreaID'))
                    if scene_id <= 0 or area_group_id <= 0:
                        continue
                    mapping = {}
                    for group in as_list(area_by_group.get(area_group_id)):
                        if not isinstance(group, dict):
                            continue
                        area_id = as_int(group.get('AreaID'))
                        pool_id = as_int(group.get('RelevancePoolID'))
                        if area_id <= 0:
                            continue
                        collects = mapping.setdefault(area_id, set())
                        for rel in as_list(rel_by_pool.get(pool_id)):
                            if not isinstance(rel, dict):
                                continue
                            for field in AREA_COLLECT_FIELDS:
                                value = as_int(rel.get(field))
                                if value > 0:
                                    collects.add(value)
                                    kind = collect_types.get(value)
                                    if kind:
                                        item_to_scene.setdefault(value, (scene_id, kind))
                    if mapping:
                        scene_areas[scene_id] = mapping

    return {
        'item_to_scene': item_to_scene,
        'scene_boxes': scene_boxes,
        'scene_areas': scene_areas,
    }


def _area_state_value(value) -> int:
    if isinstance(value, str) and value in AREA_STATES:
        return AREA_STATES[value]
    return 1 if as_int(value) > 0 else 0


def _merge_collection_item(collections: dict, kind: int, item_id: int, status: int = 0) -> None:
    if kind <= 0 or item_id <= 0:
        return
    group = collections.setdefault(str(kind), {'type': kind, 'itemMap': {}})
    item_map = group.setdefault('itemMap', {})
    existing = item_map.get(str(item_id))
    best_status = max(existing.get('status', 0) if isinstance(existing, dict) else 0, 1 if status > 0 else 0)
    item_map[str(item_id)] = {'itemId': item_id, 'status': best_status}


def build_scene_map(main, entries, resource_dir: Path | None = None) -> dict:
    """Build Lolo SceneModel.sceneMap entries from decoded capture exploration responses."""
    index = _load_exploration_index(resource_dir)
    item_to_scene = index['item_to_scene']
    scene_boxes = index['scene_boxes']
    scene_areas = index['scene_areas']
    uid = as_int(main.get('player_label')) or as_int(main.get('player_id'))
    internal = as_int(main.get('player_id')) or uid

    scenes = {}
    observed_collection_scenes = set()
    explicit_snapshot_scenes = set()
    collected_ids = set()

    def ensure_scene(scene_id: int) -> dict:
        key = str(scene_id)
        if key not in scenes:
            scenes[key] = {'sceneId': scene_id}
        return scenes[key]

    def merge_collection_group(scene_id: int, group: dict) -> None:
        if not isinstance(group, dict):
            return
        kind = uint32(as_int(group.get('type')), allow_zero=True, label='Collection type')
        if kind <= 0:
            return
        scene = ensure_scene(scene_id)
        collections = scene.setdefault('collections', {})
        target = collections.setdefault(str(kind), {'type': kind, 'itemMap': {}})
        level = as_int(group.get('level'))
        exp = as_int(group.get('exp'))
        if level > as_int(target.get('level')):
            target['level'] = level
        if exp > as_int(target.get('exp')):
            target['exp'] = exp
        raw_items = group.get('item_map') if 'item_map' in group else group.get('itemMap')
        for item_key, item_value in as_mapping(raw_items).items():
            if not isinstance(item_value, dict):
                continue
            item_id = uint32(as_int(item_value.get('item_id', item_value.get('itemId')), as_int(item_key)),
                             allow_zero=True, label='Collection item ID')
            if item_id <= 0:
                continue
            status = as_int(item_value.get('status'))
            _merge_collection_item(collections, kind, item_id, status)
            collected_ids.add(item_id)
            observed_collection_scenes.add(scene_id)
        raw_moons = as_list(group.get('collected_moon_ids', group.get('collectedMoonIds')))
        if raw_moons:
            moons = set(target.get('collectedMoonIds', []))
            for value in raw_moons:
                moon_id = uint32(as_int(value), allow_zero=True, label='Collected moon ID')
                if moon_id > 0:
                    moons.add(moon_id)
            if moons:
                target['collectedMoonIds'] = sorted(moons)

    for packet in as_list(entries.get('GetCollectItemIdsRsp')):
        if not isinstance(packet, dict):
            continue
        if packet.get('status') not in (None, 1, 'StatusCode_OK'):
            continue
        for raw_id in as_list(packet.get('item_ids')):
            item_id = as_int(raw_id)
            if item_id <= 0:
                continue
            location = item_to_scene.get(item_id)
            if location is None:
                continue
            scene_id, kind = location
            scene = ensure_scene(scene_id)
            collections = scene.setdefault('collections', {})
            _merge_collection_item(collections, kind, item_id, 0)
            collected_ids.add(item_id)
            observed_collection_scenes.add(scene_id)

    for packet in as_list(entries.get('CollectingRsp')):
        if not isinstance(packet, dict):
            continue
        if packet.get('status') not in (None, 1, 'StatusCode_OK'):
            continue
        for group in as_list(packet.get('collections')):
            if not isinstance(group, dict):
                continue
            raw_items = group.get('item_map') if 'item_map' in group else group.get('itemMap')
            for item_key, item_value in as_mapping(raw_items).items():
                if not isinstance(item_value, dict):
                    continue
                item_id = as_int(item_value.get('item_id', item_value.get('itemId')), as_int(item_key))
                location = item_to_scene.get(item_id)
                if location is None:
                    continue
                scene_id, kind = location
                scene = ensure_scene(scene_id)
                collections = scene.setdefault('collections', {})
                _merge_collection_item(collections, kind, item_id, as_int(item_value.get('status')))
                collected_ids.add(item_id)
                observed_collection_scenes.add(scene_id)

    for packet in as_list(entries.get('CollectionRewardRsp')):
        if not isinstance(packet, dict):
            continue
        if packet.get('status') not in (None, 1, 'StatusCode_OK'):
            continue
        reward = packet.get('collection_reward_data')
        if not isinstance(reward, dict):
            continue
        item_id = as_int(reward.get('item_id', reward.get('itemId')))
        location = item_to_scene.get(item_id)
        if location is None:
            continue
        scene_id, kind = location
        scene = ensure_scene(scene_id)
        collections = scene.setdefault('collections', {})
        _merge_collection_item(collections, kind, item_id, 1)
        collected_ids.add(item_id)
        observed_collection_scenes.add(scene_id)

    for notice in as_list(entries.get('SceneDataNotice')):
        data = notice.get('data') if isinstance(notice, dict) else None
        if not isinstance(data, dict):
            continue
        scene_id = as_int(data.get('scene_id'))
        if scene_id <= 0:
            continue
        garden = data.get('scene_garden_data')
        has_garden = isinstance(garden, dict) and any(
            garden.get(k) for k in ('garden_furniture_info_map', 'garden_name', 'likes_num')
        )
        if scene_id == 9999 or has_garden:
            channel = as_int(data.get('channel_id'))
            label = as_int(data.get('channel_label'))
            if not uid or (uid not in (channel, label) and internal not in (channel, label)):
                continue
        for group in as_list(data.get('collections')):
            merge_collection_group(scene_id, group)
        if scene_id != 9999 and not has_garden:
            explicit_snapshot_scenes.add(scene_id)
            areas = as_list(data.get('areas'))
            if areas:
                scene = ensure_scene(scene_id)
                area_map = scene.setdefault('areaDatas', {})
                for area in areas:
                    if not isinstance(area, dict):
                        continue
                    area_id = as_int(area.get('area_id', area.get('areaId')))
                    if area_id <= 0:
                        continue
                    state = _area_state_value(area.get('area_state', area.get('areaState')))
                    level = max(as_int(area.get('level')), 1 if state > 0 else 0)
                    existing = area_map.get(str(area_id))
                    area_map[str(area_id)] = {
                        'areaId': area_id,
                        'areaState': max(existing.get('areaState', 0) if existing else 0, state),
                        'level': max(existing.get('level', 0) if existing else 0, level),
                    }
            boxes = as_list(data.get('treasure_boxes'))
            if boxes:
                scene = ensure_scene(scene_id)
                box_map = scene.setdefault('treasureBoxs', {})
                configured_boxes = scene_boxes.get(scene_id, {})
                for box in boxes:
                    if not isinstance(box, dict):
                        continue
                    box_index = as_int(box.get('index'))
                    box_id = as_int(box.get('box_id', box.get('boxId')))
                    if box_id <= 0:
                        continue
                    if configured_boxes and configured_boxes.get(box_index) not in (None, box_id):
                        continue
                    state = 1 if as_int(box.get('state')) > 0 else 0
                    box_type = as_int(box.get('type'))
                    refresh = as_int(box.get('next_refresh_time', box.get('nextRefreshTime')))
                    existing = box_map.get(str(box_index))
                    box_map[str(box_index)] = {
                        'index': box_index,
                        'boxId': box_id,
                        'type': box_type,
                        'state': max(existing.get('state', 0) if existing else 0, state),
                        'nextRefreshTime': max(existing.get('nextRefreshTime', 0) if existing else 0, refresh),
                    }

    for packet in as_list(entries.get('GetCollectMoonInfoRsp')):
        if not isinstance(packet, dict):
            continue
        if packet.get('status') not in (None, 1, 'StatusCode_OK'):
            continue
        scene_id = as_int(packet.get('scene_id'))
        if scene_id <= 0:
            continue
        moon_ids = sorted({
            uint32(as_int(value), allow_zero=True, label='Collected moon ID')
            for value in as_list(packet.get('collected_moon_ids'))
            if as_int(value) > 0
        })
        if moon_ids:
            scene = ensure_scene(scene_id)
            collections = scene.setdefault('collections', {})
            moon_group = collections.setdefault('10', {'type': 10, 'itemMap': {}})
            existing_moons = set(moon_group.get('collectedMoonIds', []))
            moon_group['collectedMoonIds'] = sorted(existing_moons | set(moon_ids))
        em_ids = []
        for emotion in as_list(packet.get('emotion_moons')):
            if not isinstance(emotion, dict):
                continue
            moon_id = as_int(emotion.get('moon_id'))
            if moon_id > 0 and emotion.get('interacted'):
                em_ids.append(moon_id)
        if em_ids:
            scene = ensure_scene(scene_id)
            collections = scene.setdefault('collections', {})
            for moon_id in em_ids:
                _merge_collection_item(collections, 11, moon_id, 0)

    # Infer unlocked areas only for scenes observed with collected items or a non-home snapshot
    # so unvisited scenes that share AreaData groups in Excels stay locked.
    for scene_id in sorted(observed_collection_scenes | explicit_snapshot_scenes):
        area_relations = scene_areas.get(scene_id)
        if not area_relations:
            continue
        scene = ensure_scene(scene_id)
        area_map = scene.setdefault('areaDatas', {})
        for area_id, related_ids in area_relations.items():
            if str(area_id) in area_map:
                continue
            if related_ids & collected_ids:
                area_map[str(area_id)] = {'areaId': area_id, 'areaState': 1, 'level': 1}

    return model('map[uint32]*SceneInfo', scenes)


def _nonnegative_count(value, label: str) -> int:
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError('Capture summary contains invalid completeness metadata.')
    if isinstance(value, str):
        if not value.isascii() or not value.isdigit():
            raise ValueError('Capture summary contains invalid completeness metadata.')
        value = int(value)
    if value < 0:
        raise ValueError('Capture summary contains invalid completeness metadata.')
    return value


def capture_quality(main, entries, summary=None):
    """Report how much of the capture actually decoded into named fields."""
    from schema_restore import restore_capture
    summary = summary or {}
    schema_report = restore_capture(main, entries)
    report = {
        'placeholderFields': placeholder_hits(main), 'characters': 0,
        'characterPlaceholderFields': 0, 'equipmentResponses': 0, 'inventoryItems': 0,
        'restoredUnresolved': schema_report['restoredUnresolved'],
        'incompleteStreams': _nonnegative_count(summary.get('incomplete_stream_count'), 'streams'),
        'trailingBytes': _nonnegative_count(summary.get('trailing_bytes_count'), 'bytes'),
        'decodeFallbacks': _nonnegative_count(summary.get('decode_fallback_count'), 'fallbacks'),
    }
    incomplete_streams = summary.get('incomplete_streams')
    trailing_streams = summary.get('trailing_streams')
    if incomplete_streams is not None and not isinstance(incomplete_streams, list):
        raise ValueError('Capture summary contains invalid completeness metadata.')
    if trailing_streams is not None and not isinstance(trailing_streams, list):
        raise ValueError('Capture summary contains invalid completeness metadata.')
    if incomplete_streams:
        report['incompleteStreams'] = max(report['incompleteStreams'], len(incomplete_streams))
    if trailing_streams:
        report['trailingBytes'] = max(report['trailingBytes'], 1)
    characters = as_list(main.get('characters'))
    report['characters'] = len(characters)
    for captured in characters:
        if not isinstance(captured, dict):
            continue
        report['characterPlaceholderFields'] += placeholder_hits(captured.get('character_appearance'))
        report['characterPlaceholderFields'] += placeholder_hits(captured.get('appearance'))
        report['characterPlaceholderFields'] += sum(
            placeholder_hits(preset) for preset in as_list(captured.get('equipment_presets'))
        )
    for kind, collection in (('GetWeaponRsp','weapons'),('GetArmorRsp','armors'),('GetPosterRsp','posters')):
        for packet in entries.get(kind, []):
            report['equipmentResponses'] += len(as_list((packet or {}).get(collection)))
    for packet in entries.get('PackNotice', []):
        for detail in as_list((packet or {}).get('items')):
            item = (detail or {}).get('main_item') if isinstance(detail, dict) else None
            if isinstance(item, dict) and any(item.get(key) for key in ('base_item','weapon','armor','poster','outfit','inscription')):
                report['inventoryItems'] += 1
    report['incomplete'] = bool(
        report['placeholderFields'] or report['characterPlaceholderFields'] or
        report['restoredUnresolved'] or report['incompleteStreams'] or
        report['trailingBytes'] or report['decodeFallbacks']
    )
    report['equipmentListsMissing'] = report['equipmentResponses'] == 0

    # Characters reference equipped gear by instance id. When the capture never
    # received GetWeaponRsp/GetArmorRsp/GetPosterRsp, those instances are absent from
    # the item maps and the private server shows empty equipment slots. Counting the
    # unresolvable references here turns a silent partial import into a reported one.
    available = {'weapon': set(), 'armor': set(), 'poster': set()}
    for kind, collection, bucket in (('GetWeaponRsp', 'weapons', 'weapon'),
                                     ('GetArmorRsp', 'armors', 'armor'),
                                     ('GetPosterRsp', 'posters', 'poster')):
        for packet in entries.get(kind, []):
            for captured in as_list((packet or {}).get(collection)):
                if isinstance(captured, dict):
                    instance = _equipment_instance(captured.get('instance_id'), 'Equipment instance ID')
                    if instance > 0:
                        available[bucket].add(instance)
    for packet in entries.get('PackNotice', []):
        for detail in as_list((packet or {}).get('items')):
            item = (detail or {}).get('main_item') if isinstance(detail, dict) else None
            if not isinstance(item, dict):
                continue
            for key, bucket in (('weapon', 'weapon'), ('armor', 'armor'), ('poster', 'poster')):
                value = item.get(key)
                if isinstance(value, dict):
                    instance = _equipment_instance(value.get('instance_id'), 'Equipment instance ID')
                    if instance > 0:
                        available[bucket].add(instance)

    referenced = {'weapon': set(), 'armor': set(), 'poster': set()}
    for captured in characters:
        if not isinstance(captured, dict):
            continue
        for preset in as_list(captured.get('equipment_presets')):
            if not isinstance(preset, dict):
                continue
            weapon = _equipment_instance(preset.get('weapon'), 'Weapon instance ID')
            if weapon > 0:
                referenced['weapon'].add(weapon)
            for arm in as_list(preset.get('armors')):
                if isinstance(arm, dict):
                    instance = _equipment_instance(arm.get('armor_id'), 'Armor instance ID')
                    if instance > 0:
                        referenced['armor'].add(instance)
            for pos in as_list(preset.get('posters')):
                if isinstance(pos, dict):
                    instance = _equipment_instance(pos.get('poster_id'), 'Poster instance ID')
                    if instance > 0:
                        referenced['poster'].add(instance)

    report['equippedReferences'] = {key: len(value) for key, value in referenced.items()}
    report['unresolvedEquipment'] = {
        key: len(referenced[key] - available[key]) for key in referenced}
    report['missingEquipmentLists'] = [
        name for name, kind in (('GetWeaponRsp', 'weapon'), ('GetArmorRsp', 'armor'),
                                ('GetPosterRsp', 'poster'))
        if not entries.get(name)]
    return report


def build_player(main, entries, report=None, *, resource_dir: Path | None = None):
    report = report if report is not None else {}
    if 'schemaRestore' not in report:
        from schema_restore import restore_capture
        report['schemaRestore'] = restore_capture(main, entries)
    skipped = report.setdefault('skippedEntries', 0)
    character_map = {}
    for captured in as_list(main.get('characters')):
        if not isinstance(captured, dict) or 'character_id' not in captured:
            skipped += 1
            continue
        character_id = uint32(captured['character_id'], label='Character ID')
        data = dict(captured)
        for source_key, canonical_key in (
            ('character_appearance', 'characterAppearance'),
            ('character_skill_list', 'characterSkillList'),
            ('equipment_presets', 'equipmentPresets'),
            ('outfit_presets', 'outfitPresets'),
        ):
            if source_key in data and canonical_key in data:
                raise ValueError('Capture contains conflicting character fields.')
            data.pop(source_key, None)
        data.update(item_id=character_id, num=1, item_type='EBagItemTag_Card', pack_type=0)
        appearance = captured.get('characterAppearance', captured.get('appearance'))
        if not isinstance(appearance, dict) or is_placeholder(appearance):
            appearance = captured.get('character_appearance')
        data['characterAppearance'] = appearance if isinstance(appearance, dict) and not is_placeholder(appearance) else {'badge': 5000}
        skills = captured.get('skill_list', captured.get('skills', captured.get('character_skill_list')))
        skill_map = {}
        for value in as_list(skills):
            if isinstance(value, dict) and 'skill_id' in value:
                _put_unique(skill_map, as_int(value['skill_id']), value, 'character skills')
        data['characterSkillList'] = skill_map
        data['equipmentPresets'] = {}
        for index, preset in enumerate(as_list(captured.get('equipment_presets'))):
            if not isinstance(preset, dict):
                skipped += 1
                continue
            armors = {}
            for value in as_list(preset.get('armors')):
                if not isinstance(value, dict) or is_placeholder(value):
                    skipped += 1
                    continue
                instance = as_int(value.get('armor_id'))
                if instance <= 0:
                    skipped += 1
                    continue
                equip_type = as_int(value.get('equip_type'))
                _put_unique(armors, equip_type, {'equipType': equip_type, 'instanceId': instance}, 'armor slots')
            posters = {}
            for value in as_list(preset.get('posters')):
                if not isinstance(value, dict) or is_placeholder(value):
                    skipped += 1
                    continue
                instance = as_int(value.get('poster_id'))
                if instance <= 0:
                    skipped += 1
                    continue
                poster_index = as_int(value.get('poster_index'))
                _put_unique(posters, poster_index, {'posterIndex': poster_index, 'instanceId': instance}, 'poster slots')
            _put_unique(data['equipmentPresets'], index, {
                'presetIndex': as_int(preset.get('preset_index'), index),
                'weaponInstanceId': as_int(preset.get('weapon')),
                'armors': armors, 'posters': posters,
            }, 'equipment presets')
        outfits = {}
        for value in as_list(captured.get('outfit_presets')):
            if isinstance(value, dict):
                _put_unique(outfits, as_int(value.get('preset_index'), len(outfits)), value, 'outfit presets')
        data['outfitPresets'] = outfits
        _put_unique(character_map, character_id, model('CharacterInfo', data), 'characters')
    items = {name: {} for name in (
        'itemBaseInfo','itemWeaponMap','itemArmorMap','itemPosterMap','itemFashionMap',
        'itemInscriptionMap','itemPetInfoMap','itemHeadMap','furnitureItemMap')}
    for packet in entries.get('PackNotice', []):
        for detail in as_list((packet or {}).get('items')):
            if not isinstance(detail, dict):
                skipped += 1
                continue
            item = detail.get('main_item')
            if not isinstance(item, dict):
                skipped += 1
                continue
            base = item.get('base_item')
            if isinstance(base, dict) and 'item_id' in base:
                item_id = uint32(base['item_id'], label='Item ID')
                if 'item_id' in item and uint32(item['item_id'], label='Item ID') != item_id:
                    raise ValueError('Capture contains mismatched main and base item IDs.')
                data = dict(base, item_type=as_int(item.get('item_tag')), pack_type=as_int(detail.get('pack_type')))
                _put_unique(items['itemBaseInfo'], item_id, model('ItemBaseInfo', data), 'base items')
            fashion = item.get('outfit')
            if isinstance(fashion, dict) and not is_placeholder(fashion):
                outfit_id = uint32(fashion.get('outfit_id', item.get('item_id')), label='Outfit ID')
                data = dict(fashion, item_id=item.get('item_id'), outfit_id=outfit_id,
                            item_type=as_int(item.get('item_tag')), num=1)
                schemes = {}
                raw_schemes = fashion.get('dye_schemes')
                if isinstance(raw_schemes, dict) and not is_placeholder(raw_schemes) and not all(isinstance(v, dict) for v in raw_schemes.values()):
                    skipped += 1
                for value in as_list(raw_schemes):
                    if isinstance(value, dict):
                        _put_unique(schemes, as_int(value.get('scheme_index'), len(schemes)), value, 'dye schemes')
                    else:
                        skipped += 1
                data['dyeSchemes'] = schemes
                if 'dye_schemes' in data and 'dyeSchemes' in fashion:
                    raise ValueError('Capture contains conflicting outfit fields.')
                data.pop('dye_schemes', None)
                _put_unique(items['itemFashionMap'], outfit_id, model('ItemFashionInfo', data), 'outfits')
    greatest = 0
    for packet_kind, collection, target, kind, id_key, tag in [
        ('GetWeaponRsp','weapons','itemWeaponMap','ItemWeaponInfo','weapon_id','EBagItemTag_Weapon'),
        ('GetArmorRsp','armors','itemArmorMap','ItemArmorInfo','armor_id','EBagItemTag_Armor'),
        ('GetPosterRsp','posters','itemPosterMap','ItemPosterInfo','poster_id','EBagItemTag_Poster'),
    ]:
        for packet in entries.get(packet_kind, []):
            for captured in as_list((packet or {}).get(collection)):
                if not isinstance(captured, dict) or 'instance_id' not in captured or id_key not in captured:
                    skipped += 1
                    continue
                instance = uint32(captured['instance_id'], label='Equipment instance ID')
                greatest = max(greatest, instance)
                data = dict(captured, item_id=captured[id_key], item_type=tag, num=1, pack_type=0)
                _put_unique(items[target], instance, model(kind, data), 'equipment instances')
    items['instanceIndex'] = greatest + 1
    # Placed-furniture counts live in their own map; the server increments it via
    # AddFurnitureItem and reports it through FurnitureItemInfo(), so an import that
    # skips it makes every placed item look uncounted in the bag.
    for entry in as_list(main.get('furniture_item_info')):
        if not isinstance(entry, dict):
            skipped += 1
            continue
        item_id = as_int(entry.get('item_id'))
        if item_id <= 0:
            skipped += 1
            continue
        items['furnitureItemMap'][str(item_id)] = model('FurnitureItemInfo', {
            'itemId': item_id, 'num': as_int(entry.get('num'), 1)})
    variants = [
        ('weapon','itemWeaponMap','ItemWeaponInfo','weapon_id'),
        ('armor','itemArmorMap','ItemArmorInfo','armor_id'),
        ('poster','itemPosterMap','ItemPosterInfo','poster_id'),
        ('inscription','itemInscriptionMap','ItemInscriptionInfo','inscription_id'),
    ]
    for pack in entries.get('PackNotice', []):
        for detail in as_list((pack or {}).get('items')):
            if not isinstance(detail, dict):
                continue
            main_item = detail.get('main_item')
            if not isinstance(main_item, dict):
                continue
            for source, target, kind, id_key in variants:
                value = main_item.get(source)
                if not isinstance(value, dict) or is_placeholder(value):
                    continue
                if 'instance_id' not in value and id_key not in value:
                    continue
                key = uint32(value.get('instance_id') or value.get(id_key), label='Equipment instance ID')
                if 'instance_id' in value:
                    greatest = max(greatest, key)
                data = dict(value, item_id=value.get(id_key) or main_item.get('item_id'),
                            item_type=as_int(main_item.get('item_tag')),
                            pack_type=as_int(detail.get('pack_type')), num=1)
                _merge_unique(items[target], key, model(kind, data), 'equipment instances')
    if greatest >= 0xFFFFFFFF:
        raise ValueError('Equipment instance index exceeds the supported range.')
    items['instanceIndex'] = greatest + 1
    team = as_mapping(main.get('team'))
    scene_map = build_scene_map(main, entries, resource_dir=resource_dir)
    scene_data = {
        'worldLevel': as_int(main.get('world_level')),
        'cur_pet_instance_id': as_int(main.get('pet_instance_id')),
    }
    if scene_map:
        scene_data['sceneMap'] = scene_map
    return {
        'instanceIndex': greatest + 1,
        'character': {'characterMap': character_map, 'placedCharacters': main.get('placed_characters', [])},
        'item': items,
        'team': {'TeamInfo': {
            'Char1': as_int(team.get('char1'), 101001), 'Char2': as_int(team.get('char2')),
            'Char3': as_int(team.get('char3')),
        }},
        'scene': scene_data,
    }


def _profile_database(profile: Path) -> Path:
    profile = Path(profile).resolve()
    database = profile / 'db' / 'lolo.db'
    if not database.is_file() or _no_link(database) or _no_link(database.parent):
        raise ValueError('Prepare this profile before importing.')
    return database


def _new_task(profile: Path) -> Path:
    profile = Path(profile).resolve()
    root = profile / 'runtime' / 'imports'
    root.mkdir(parents=True, exist_ok=True)
    if _no_link(root) or _no_link(root.parent):
        raise ValueError('Unsafe profile task directory.')
    return Path(tempfile.mkdtemp(prefix='task-', dir=root))


def _validate_target_schema(connection) -> None:
    connection.execute('PRAGMA trusted_schema=OFF')
    required = {
        'of_user': {'user_id', 'sdk_uid'},
        'of_game': {'user_id', 'created_at', 'updated_at', 'bin_data'},
        'of_game_basic': {'user_id'},
    }
    for table, columns in required.items():
        row = connection.execute("SELECT type FROM sqlite_master WHERE name=?", (table,)).fetchone()
        if not row or row[0] != 'table':
            raise ValueError('Profile database schema is not import-compatible.')
        info = list(connection.execute(f'PRAGMA table_info({table})'))
        actual = {column[1] for column in info}
        primary_key = [column[1] for column in sorted(info, key=lambda column: column[5]) if column[5]]
        if not columns <= actual or primary_key != ['user_id']:
            raise ValueError('Profile database schema is not import-compatible.')


def _row_fingerprint(row) -> bytes:
    digest = hashlib.sha256()
    for value in row:
        if value is None:
            kind, data = b'N', b''
        elif isinstance(value, bytes):
            kind, data = b'B', value
        elif isinstance(value, str):
            kind, data = b'S', value.encode('utf-8', errors='surrogatepass')
        elif isinstance(value, (int, float)):
            kind, data = b'I', repr(value).encode('ascii')
        else:
            kind, data = b'R', repr(value).encode('utf-8', errors='replace')
        digest.update(kind + len(data).to_bytes(8, 'big') + data)
    return digest.digest()


def _account_state(connection) -> dict:
    state = {}
    for table in ('of_user', 'of_game', 'of_game_basic'):
        columns = [column[1] for column in connection.execute(f'PRAGMA table_info({table})')]
        ordered = ['user_id'] + [column for column in columns if column != 'user_id']
        rows = connection.execute(
            f"SELECT {','.join(_quoted_identifier(column) for column in ordered)} FROM {_quoted_identifier(table)}"
        )
        state[table] = {row[0]: _row_fingerprint(row[1:]) for row in rows}
    return state


def _has_account_triggers(connection) -> bool:
    placeholders = ','.join('?' for _ in range(3))
    return connection.execute(
        f"SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name IN ({placeholders}) LIMIT 1",
        ('of_user', 'of_game', 'of_game_basic'),
    ).fetchone() is not None


def _verify_account_state(connection, before: dict, uid: int, allow_overwrite: bool = False) -> None:
    after = _account_state(connection)
    for table, rows in before.items():
        if set(after[table]) != set(rows) | {uid}:
            raise ValueError('SQLite triggers attempted to create or remove account data.')
        for existing_uid, fingerprint in rows.items():
            if allow_overwrite and existing_uid == uid:
                continue
            if after[table][existing_uid] != fingerprint:
                raise ValueError('SQLite triggers attempted to modify another account.')
    mapping = connection.execute('SELECT sdk_uid FROM of_user WHERE user_id=?', (uid,)).fetchone()
    if not mapping or mapping[0] != uid:
        raise ValueError('SQLite triggers produced an invalid account mapping.')


def _check_existing(connection, uid: int, allow_overwrite: bool = False) -> bool:
    has_game = connection.execute('SELECT 1 FROM of_game WHERE user_id=?', (uid,)).fetchone() is not None
    has_basic = connection.execute('SELECT 1 FROM of_game_basic WHERE user_id=?', (uid,)).fetchone() is not None
    if not allow_overwrite:
        if has_game:
            raise ValueError('UID already has progress; existing saves are never overwritten.')
        if has_basic:
            raise ValueError('UID already has account data; existing saves are never overwritten.')
    row = connection.execute('SELECT sdk_uid FROM of_user WHERE user_id=?', (uid,)).fetchone()
    if row and (row[0] is None or int(row[0]) != uid):
        raise ValueError('UID conflicts with an existing account mapping.')
    if connection.execute('SELECT 1 FROM of_user WHERE sdk_uid=? AND user_id<>?', (uid, uid)).fetchone():
        raise ValueError('UID conflicts with an existing account mapping.')
    return has_game or has_basic


def _preflight(profile: Path, database: Path, uid: int, allow_overwrite: bool = False) -> bool:
    with closing(sqlite3.connect(database)) as connection:
        _validate_target_schema(connection)
        had_db_save = _check_existing(connection, uid, allow_overwrite=allow_overwrite)
    directory = profile / 'accounts' / str(uid)
    if _no_link(directory):
        raise ValueError('UID already has imported files; existing saves are never overwritten.')
    if directory.exists():
        if not allow_overwrite or not directory.is_dir():
            raise ValueError('UID already has imported files; existing saves are never overwritten.')
    if (profile / 'accounts').exists() and _no_link(profile / 'accounts'):
        raise ValueError('Unsafe profile accounts directory.')
    return had_db_save or directory.exists()


def _snapshot_database(database: Path, backup: Path, task: Path) -> None:
    temporary = task / 'backup.db'
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)) as origin:
        with closing(sqlite3.connect(temporary)) as target:
            origin.backup(target)
            if target.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise ValueError('SQLite backup validation failed.')
    _publish_temp_file(temporary, backup)


def _iso_timestamp(value) -> str:
    value = as_int(value)
    if value < 0 or value > 253402300799:
        raise ValueError('Capture contains an invalid creation timestamp.')
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat()


def _basic_values(main, uid: int, created: str, now: str) -> dict:
    appearance = main.get('appearance')
    appearance = appearance if isinstance(appearance, dict) and not is_placeholder(appearance) else {}
    team = as_mapping(main.get('team'))
    return {
        'user_id': uid,
        'nick_name': main.get('player_name', f'UID {uid}'),
        'level': uint32(main.get('level', 1), allow_zero=True, label='Player level'),
        'exp': as_int(main.get('exp', 0)),
        'head': as_int(main.get('head', 41101)),
        'sex': ENUMS.get(main.get('sex'), main.get('sex', 0)),
        'phone_background': main.get('phone_background', 8000),
        'sign': main.get('sign', ''),
        'created_at': created,
        'updated_at': now,
        'birthday': main.get('birthday', ''),
        'is_hide_birthday': main.get('is_hide_birthday', False),
        'avatar_frame': as_int(appearance.get('avatar_frame')),
        'pendant': as_int(appearance.get('pendant')),
        'character_id': as_int(team.get('char1'), 101001),
        'hide_value': main.get('hide_value', 0),
    }


def _write_database(database: Path, uid: int, created: str, now: str, payload: bytes, basic: dict,
                    garden: dict = None, allow_overwrite: bool = False) -> None:
    with closing(sqlite3.connect(database, timeout=10, isolation_level=None)) as connection:
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA trusted_schema=OFF')
        connection.execute('BEGIN IMMEDIATE')
        try:
            _validate_target_schema(connection)
            _check_existing(connection, uid, allow_overwrite=allow_overwrite)
            account_state = _account_state(connection) if _has_account_triggers(connection) else None
            mapping = connection.execute('SELECT sdk_uid FROM of_user WHERE user_id=?', (uid,)).fetchone()
            if not mapping:
                connection.execute('INSERT INTO of_user(user_id,sdk_uid,ban) VALUES(?,?,0)', (uid, uid))
            if allow_overwrite:
                connection.execute(
                    'INSERT INTO of_game(user_id,created_at,updated_at,bin_data) VALUES(?,?,?,?) '
                    'ON CONFLICT(user_id) DO UPDATE SET created_at=excluded.created_at,'
                    'updated_at=excluded.updated_at,bin_data=excluded.bin_data',
                    (uid, created, now, payload),
                )
            else:
                connection.execute(
                    'INSERT INTO of_game(user_id,created_at,updated_at,bin_data) VALUES(?,?,?,?)',
                    (uid, created, now, payload),
                )
            columns = {row[1] for row in connection.execute('PRAGMA table_info(of_game_basic)')}
            values = {key: value for key, value in basic.items() if key in columns}
            if not all(_IDENTIFIER.match(key) for key in values):
                raise ValueError('Profile database schema is not import-compatible.')
            names = list(values)
            if allow_overwrite:
                updates = [f'{name}=excluded.{name}' for name in names if name != 'user_id']
                conflict_clause = f' ON CONFLICT(user_id) DO UPDATE SET {",".join(updates)}' if updates else ' ON CONFLICT(user_id) DO NOTHING'
                connection.execute(
                    f'INSERT INTO of_game_basic({",".join(names)}) VALUES({",".join("?" for _ in names)}){conflict_clause}',
                    list(values.values()),
                )
            else:
                connection.execute(
                    f'INSERT INTO of_game_basic({",".join(names)}) VALUES({",".join("?" for _ in names)})',
                    list(values.values()),
                )
            if garden is not None:
                home_columns = {row[1] for row in connection.execute('PRAGMA table_info(of_home)')}
                if {'user_id', 'scene_id', 'garden_furniture_info_map'} <= home_columns:
                    row = {
                        'user_id': uid,
                        'scene_id': 9999,
                        'garden_name': garden['garden_name'],
                        'likes_num': garden['likes_num'],
                        'access_player_num': garden['access_player_num'],
                        'left_like_num': garden['left_like_num'],
                        'is_open': garden['is_open'],
                        'password': garden['password'],
                        'password_expire_time': garden['password_expire_time'],
                        # The server serialises with sonic and reads the column back
                        # into map[int64]*FurnitureDetailsInfo, so this must be JSON.
                        'garden_furniture_info_map': json.dumps(
                            garden['furniture'], ensure_ascii=False, separators=(',', ':'),
                        ).encode('utf-8'),
                        'placed_character_map': json.dumps(
                            garden['placed'], ensure_ascii=False, separators=(',', ':'),
                        ).encode('utf-8'),
                    }
                    row = {key: value for key, value in row.items() if key in home_columns}
                    keys = list(row)
                    assignments = ','.join(
                        f'{key}=excluded.{key}' for key in keys if key not in ('user_id', 'scene_id'))
                    connection.execute(
                        f'INSERT INTO of_home({",".join(keys)}) VALUES({",".join("?" for _ in keys)}) '
                        f'ON CONFLICT(user_id,scene_id) DO UPDATE SET {assignments}',
                        list(row.values()),
                    )
            if account_state is not None:
                _verify_account_state(connection, account_state, uid, allow_overwrite=allow_overwrite)
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def _write_staged_json(path: Path, value) -> None:
    reject_secrets(value)
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    with path.open('x', encoding='utf-8') as output:
        output.write(text)
        output.flush()
        os.fsync(output.fileno())


def import_capture(path, profile, expected_uid, replace=False, allow_incomplete=False, *,
                   _task_dir=None, _source_sha256=None, allow_overwrite=False):
    """Import one capture. ``replace`` is deprecated and ignored; ``allow_overwrite`` must be passed explicitly."""
    profile = Path(profile).resolve()
    expected_uid = uint32(expected_uid, allow_zero=True, label='Expected UID')
    database = _profile_database(profile)
    task = Path(_task_dir) if _task_dir is not None else _new_task(profile)
    try:
        copied, copied_hash = snapshot_archive(path, task)
        with open_secure_zip(copied) as archive:
            player, entries = read_capture_archive(archive, allow_missing_uid=bool(expected_uid))
            summary = read_capture_summary(archive, find_capture_prefix(archive))
        uid = captured_uid(player) or expected_uid
        if not uid:
            raise ValueError('Capture must contain a verifiable UID.')
        if expected_uid and uid != expected_uid:
            raise ValueError('Capture UID differs from selected UID.')
        quality = capture_quality(player, entries, summary)
        if quality['incomplete'] and not allow_incomplete:
            raise ValueError(
                '这个包的解码不完整（capture is incomplete or undecoded）；默认拒绝导入。'
                '请重新采集缺失数据后再导入。'
            )
        had_existing = _preflight(profile, database, uid, allow_overwrite=allow_overwrite)
        saved = build_player(main=player, entries=entries, report=quality, resource_dir=profile / 'Resource')
        reject_secrets(saved)
        payload = gzip.compress(json.dumps(
            saved, ensure_ascii=False, separators=(',', ':'), allow_nan=False,
        ).encode('utf-8'), mtime=0)
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        created = _iso_timestamp(player.get('create_time'))
        basic = _basic_values(player, uid, created, now)
        reject_secrets(basic)
        garden = build_garden(player, entries)
        stamp = dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        backup = profile / 'backups' / f'before-import-{stamp}.db'
        backup.parent.mkdir(parents=True, exist_ok=True)
        if _no_link(backup.parent):
            raise ValueError('Unsafe profile backup directory.')
        _snapshot_database(database, backup, task)
        staged = task / 'account'
        staged.mkdir()
        _write_staged_json(staged / 'main-data.json', player)
        _write_staged_json(staged / 'captured-responses.json', entries)
        scene_map = (saved.get('scene') or {}).get('sceneMap') or {}
        restored = ['basic','characters','equipment presets','outfits','team','inventory',
                    'weapons','armor','posters','inscriptions','gathering tools','world level']
        if scene_map:
            restored.append('scene exploration')
        result = {
            'uid': uid,
            'internalId': uint32(player.get('player_id') or uid, allow_zero=True, label='Internal player ID'),
            'name': player.get('player_name', ''),
            'level': player.get('level', 1),
            'characters': len(as_list(player.get('characters'))),
            'capturedMessages': sum(len(value) for value in entries.values()),
            'sha256': _source_sha256 or copied_hash,
            'database': str(database),
            'backup': str(backup),
            'overwroteExisting': bool(had_existing),
            'quality': quality,
            'restored': restored,
            'preservedOnly': ['quest state','activities','bless tree','other modules not persisted by this Lolo version'],
        }
        if scene_map:
            result['exploration'] = {
                'scenes': len(scene_map),
                'collectionItems': sum(
                    len(group.get('itemMap', {}))
                    for s in scene_map.values()
                    for group in (s.get('collections') or {}).values()
                ),
                'areas': sum(len(s.get('areaDatas') or {}) for s in scene_map.values()),
                'treasureBoxes': sum(len(s.get('treasureBoxs') or {}) for s in scene_map.values()),
                'moons': sum(
                    len(group.get('collectedMoonIds') or [])
                    for s in scene_map.values()
                    for group in (s.get('collections') or {}).values()
                ),
            }
        if quality['skippedEntries']:
            result['skippedEntries'] = quality['skippedEntries']
        if garden is not None:
            result['garden'] = {'sceneId': 9999, 'furniture': len(garden['furniture']),
                                'placedCharacters': len(garden['placed'])}
        _write_staged_json(staged / 'import-report.json', result)
        _write_database(database, uid, created, now, payload, basic, garden, allow_overwrite=allow_overwrite)
        destination = profile / 'accounts' / str(uid)
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Move any previously imported account directory into backups/ before publishing the replacement.
        previous_account = None
        if destination.exists():
            if not allow_overwrite or _no_link(destination) or not destination.is_dir():
                raise ValueError('UID already has imported files; existing saves are never overwritten.')
            previous_account = profile / 'backups' / f'account-{uid}-before-import-{stamp}'
            os.rename(destination, previous_account)
        try:
            os.rename(staged, destination)
        except Exception:
            if previous_account is not None and previous_account.exists() and not destination.exists():
                os.rename(previous_account, destination)
            raise
        return result
    finally:
        shutil.rmtree(task, ignore_errors=True)


def _public_error(error: Exception) -> dict:
    return {'error': safe_error(error)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['inspect', 'import', 'uid', 'accounts'])
    parser.add_argument('--file', type=Path)
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--uid', type=int, default=0)
    parser.add_argument('--replace', action='store_true', help='deprecated; existing saves are never replaced')
    parser.add_argument('--allow-overwrite', action='store_true')
    parser.add_argument('--allow-incomplete', action='store_true')
    args = parser.parse_args()
    try:
        if args.action == 'inspect':
            result = inspect(args.file)
        elif args.action == 'import':
            result = import_capture(
                args.file, args.profile, args.uid, args.replace,
                args.allow_incomplete, allow_overwrite=args.allow_overwrite,
            )
        elif args.action == 'uid':
            result = {'uid': args.uid, 'sdkUid': ensure_uid(args.profile / 'db/lolo.db', args.uid)}
        else:
            database = args.profile / 'db/lolo.db'
            result = []
            if database.exists():
                with closing(sqlite3.connect(database)) as connection:
                    result = [
                        {'uid': row[0], 'name': row[1] or f'UID {row[0]}', 'level': row[2] or 1}
                        for row in connection.execute(
                            'SELECT u.user_id,b.nick_name,b.level FROM of_user u '
                            'LEFT JOIN of_game_basic b ON u.user_id=b.user_id ORDER BY u.user_id'
                        )
                    ]
        print(json.dumps(redact_result(result), ensure_ascii=False))
        return 0
    except (ValueError, OSError, zipfile.BadZipFile, sqlite3.Error) as error:
        print(json.dumps(_public_error(error), ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
