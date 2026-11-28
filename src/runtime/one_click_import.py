"""One-click universal importer supporting v1.4.x, v1.5+, raw protobuf, Android, garden, and DB packs.

Missing modules (characters, bag, weapons, armor, posters, garden, exploration)
are cleanly left empty rather than rejecting partial or cross-version archives.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import csv
import datetime as dt
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import sqlite3
import sys
import tempfile
import unicodedata
import uuid
import zipfile
import zlib

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

import capture_import  # noqa: E402
from capture_zip import (  # noqa: E402
    COPY_CHUNK,
    MAX_ARCHIVE_BYTES,
    MAX_COMPRESSION_RATIO,
    MAX_MEMBER_UNCOMPRESSED,
    MAX_MEMBERS,
    MAX_TOTAL_UNCOMPRESSED,
    open_secure_zip,
    read_member,
    uint32,
)
from privacy_check import redact_result, redact_value, safe_error, scan_text, sensitive_key  # noqa: E402
import schema_restore  # noqa: E402

_S2C_JSON_NAME = re.compile(r'_s2c_(\d+)_([A-Za-z0-9_]+)\.json$', re.I)
_S2C_BIN_NAME = re.compile(r'_s2c_(\d+)_([A-Za-z0-9_]+)\.bin$', re.I)
_RAW_CAPTURE_SUFFIXES = {'.etl', '.pcap', '.pcapng', '.cap'}
_SQLITE_MAGIC = b'SQLite format 3\x00'
_MAX_NESTED_ZIP_DEPTH = 2
_MAX_NESTED_ZIP_BYTES = 64 * 1024 * 1024
_FILENAME_UID_RE = re.compile(r'(?:^|_)(\d{4,10})(?:_|$|\.)')


def _publish_file(source: Path, destination: Path, *, overwrite: bool = False) -> None:
    """Publish staged file atomically, falling back across exFAT/FAT32/cross-volume."""
    source = Path(source)
    destination = Path(destination)
    if overwrite:
        try:
            os.replace(source, destination)
            return
        except OSError:
            shutil.copy2(source, destination)
            source.unlink(missing_ok=True)
            return
    try:
        os.link(source, destination)
        source.unlink(missing_ok=True)
    except OSError:
        if destination.exists():
            raise FileExistsError(destination)
        temporary = destination.with_name(f'.{destination.name}.{uuid.uuid4().hex}.tmp')
        try:
            shutil.copy2(source, temporary)
            if destination.exists():
                raise FileExistsError(destination)
            os.replace(temporary, destination)
            source.unlink(missing_ok=True)
        finally:
            temporary.unlink(missing_ok=True)


def _tolerant_json_loads(raw: bytes | str):
    """Parse UTF-8 JSON allowing duplicate keys (last wins) and standard NaN tolerance."""
    if isinstance(raw, bytes):
        text = raw.decode('utf-8-sig', errors='replace')
    else:
        text = raw
    value = json.loads(text)
    return _strip_secrets(value)


def _strip_secrets(value, _parent_key: str = ''):
    """Remove sensitive credential values while keeping game save structures intact."""
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if not isinstance(key, str):
                continue
            low = capture_import.normalize(key)
            # Garden snapshots legitimately store a public room password field (usually empty).
            if _parent_key == 'scene_garden_data' and low == 'password':
                cleaned[key] = ''
                continue
            if sensitive_key(key):
                continue
            cleaned[key] = _strip_secrets(item, low)
        return cleaned
    if isinstance(value, list):
        return [_strip_secrets(item, _parent_key) for item in value]
    if isinstance(value, str) and scan_text(value):
        return ''
    return value


def _safe_int(value, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != value or value in (float('inf'), float('-inf')):
            return default
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped in capture_import.ENUMS:
            return int(capture_import.ENUMS[stripped])
        if re.fullmatch(r'-?\d+', stripped):
            try:
                return int(stripped)
            except ValueError:
                return default
    return default


def _safe_uint(value, default: int = 0) -> int:
    num = _safe_int(value, default)
    return num if num >= 0 else default


def _tolerant_model(kind: str, data):
    """Version-tolerant model converter matching Lolo JSON shapes without strict range failures."""
    if data is None:
        return None
    if kind.startswith('*'):
        return _tolerant_model(kind[1:], data)
    if kind.startswith('[]'):
        items = capture_import.as_list(data)
        return [_tolerant_model(kind[2:], value) for value in items]
    if kind.startswith('map['):
        if isinstance(data, list):
            return {str(idx): _tolerant_model(kind.split(']', 1)[1], item) for idx, item in enumerate(data)}
        if not isinstance(data, dict):
            return {}
        nested = kind.split(']', 1)[1]
        return {str(key): _tolerant_model(nested, value) for key, value in data.items()}
    if kind in capture_import.SCHEMA['models'] and isinstance(data, dict):
        normalized = {}
        for key in data:
            if isinstance(key, str):
                normalized[capture_import.normalize(key)] = key
        result = {}
        for field in capture_import.SCHEMA['models'][kind]:
            if 'embedded' in field:
                embedded = _tolerant_model(field['embedded'], data)
                if isinstance(embedded, dict):
                    result.update(embedded)
                continue
            lookup = capture_import.normalize(field['key'])
            if lookup in normalized:
                result[field['key']] = _tolerant_model(field['type'], data[normalized[lookup]])
        return result
    if kind.startswith(('uint', 'int', 'proto.')):
        return _safe_int(data, 0)
    if kind == 'bool':
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
            if lowered in capture_import.ENUMS:
                return bool(capture_import.ENUMS[lowered])
        return bool(data)
    if kind == 'RandomPropertys':
        return [_tolerant_model('RandomProperty', value) for value in capture_import.as_list(data)]
    if isinstance(data, dict):
        return {key: _tolerant_model('', value) for key, value in data.items() if isinstance(key, str)}
    if isinstance(data, list):
        return [_tolerant_model('', value) for value in data]
    return capture_import.ENUMS.get(data, data) if isinstance(data, str) else data


def _tolerant_restore(main: dict, entries: dict) -> int:
    """Restore proven fallback field_N names in place without failing on conflicts/malformed items."""
    unresolved = 0
    for character in capture_import.as_list(main.get('characters')):
        if not isinstance(character, dict):
            continue
        for preset in capture_import.as_list(character.get('equipment_presets')):
            if not isinstance(preset, dict):
                continue
            for source_key, schema_name in (('armors', 'armorInfo'), ('posters', 'posterInfo')):
                items = capture_import.as_list(preset.get(source_key))
                cleaned = []
                for entry in items:
                    if isinstance(entry, dict):
                        try:
                            fixed, extra = schema_restore.restore(entry, schema_name)
                            unresolved += extra
                            cleaned.append(fixed)
                            continue
                        except schema_restore.FieldConflictError:
                            pass
                    cleaned.append(entry)
                if source_key in preset:
                    preset[source_key] = cleaned
    for packet in capture_import.as_list(entries.get('PackNotice')):
        if not isinstance(packet, dict):
            continue
        for detail in capture_import.as_list(packet.get('items')):
            if not isinstance(detail, dict):
                continue
            main_item = detail.get('main_item')
            if isinstance(main_item, dict):
                try:
                    detail['main_item'], extra = schema_restore.restore_item(main_item)
                    unresolved += extra
                except schema_restore.FieldConflictError:
                    pass
    return unresolved


def _load_proto_decoder():
    """Lazily load bundled protobuf definitions to decode raw/*.bin files when JSON is missing."""
    candidates = (
        BASE / 'backup-tool',
        BASE.parent / 'backup-tool',
    )
    for tool_dir in candidates:
        proto_dir = tool_dir / 'proto_gen'
        if not (proto_dir / 'net_pb2.py').is_file():
            continue
        try:
            if str(proto_dir) not in sys.path:
                sys.path.insert(0, str(proto_dir))
            import net_pb2  # type: ignore
            from google.protobuf import json_format  # type: ignore

            cmd_map = {}
            msg_id_txt = proto_dir / 'msg_id.txt'
            if msg_id_txt.is_file():
                for line in msg_id_txt.read_text(encoding='utf-8', errors='ignore').splitlines():
                    match = re.match(r'^\s*([A-Za-z0-9_]+)\s*=\s*(\d+)\s*$', line)
                    if match:
                        cmd_map[int(match.group(2))] = match.group(1)
            cmdid_csv = proto_dir / 'cmdid.csv'
            if cmdid_csv.is_file():
                with cmdid_csv.open('r', encoding='utf-8', errors='ignore', newline='') as handle:
                    for row in csv.reader(handle):
                        if len(row) >= 2 and row[1].strip().isdigit():
                            cmd_map[int(row[1].strip())] = row[0].strip()
            return net_pb2, json_format, cmd_map
        except Exception:
            continue
    return None, None, {}


def _decode_raw_bin(name: str, msg_id: int | None, raw_bytes: bytes, proto_bundle):
    net_pb2, json_format, cmd_map = proto_bundle
    if net_pb2 is None or json_format is None:
        return None, None
    resolved_name = name or (cmd_map.get(msg_id) if msg_id is not None else None)
    if not resolved_name:
        return None, None
    cls = getattr(net_pb2, resolved_name, None)
    if cls is None:
        return None, None
    try:
        message = cls()
        message.ParseFromString(raw_bytes)
        parsed = json_format.MessageToDict(message, preserving_proto_field_name=True)
        return resolved_name, _strip_secrets(parsed)
    except Exception:
        return None, None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(COPY_CHUNK), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _dir_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(p for p in path.rglob('*') if p.is_file()):
        digest.update(item.relative_to(path).as_posix().encode('utf-8', errors='replace'))
        digest.update(_file_sha256(item).encode('ascii'))
    return digest.hexdigest()


def _validate_zip_structure_only(path: Path) -> zipfile.ZipFile:
    """Validate ZIP safety bounds (bombs, path traversal, symlinks) without rejecting 1.4/raw/c2s."""
    if not path.is_file() or path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError('找不到可导入的归档文件，或文件超过 512 MB 限制。')
    with path.open('rb') as stream:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(max(0, size - 65_557))
        tail = stream.read()
        end = tail.rfind(b'PK\x05\x06')
        if end >= 0 and end + 22 <= len(tail):
            eocd = tail[end:end + 22]
            count = int.from_bytes(eocd[10:12], 'little')
            central_size = int.from_bytes(eocd[12:16], 'little')
            central_offset = int.from_bytes(eocd[16:20], 'little')
            if count != 0xFFFF and central_size != 0xFFFFFFFF and central_offset + central_size <= size:
                stream.seek(central_offset)
                for _ in range(min(count, MAX_MEMBERS + 1)):
                    hdr = stream.read(46)
                    if len(hdr) != 46 or not hdr.startswith(b'PK\x01\x02'):
                        break
                    nl, el, cl = int.from_bytes(hdr[28:30], 'little'), int.from_bytes(hdr[30:32], 'little'), int.from_bytes(hdr[32:34], 'little')
                    raw_name = stream.read(nl)
                    if any(b in raw_name for b in (0, 0x5C, 0x3A)):
                        raise ValueError('归档中包含不安全路径，已拒绝读取。')
                    stream.seek(el + cl, os.SEEK_CUR)
    archive = zipfile.ZipFile(path)
    try:
        infos = archive.infolist()
        if len(infos) > MAX_MEMBERS:
            raise ValueError('归档文件条目过多，已超过安全上限。')
        total = 0
        for item in infos:
            name = item.filename
            if not name or '\x00' in name or '\\' in name or ':' in name:
                raise ValueError('归档中包含不安全路径，已拒绝读取。')
            posix = PurePosixPath(name)
            windows = PureWindowsPath(name)
            if posix.is_absolute() or windows.is_absolute() or windows.drive or '..' in posix.parts:
                raise ValueError('归档中包含路径穿越条目，已拒绝读取。')
            mode = item.external_attr >> 16
            if (mode & 0o170000) == 0o120000:
                raise ValueError('归档不允许包含符号链接。')
            if item.flag_bits & 1:
                raise ValueError('暂不支持加密 ZIP 归档。')
            if not item.is_dir():
                if item.file_size > MAX_MEMBER_UNCOMPRESSED:
                    raise ValueError('归档中存在单个过大的成员。')
                if item.file_size > MAX_COMPRESSION_RATIO * item.compress_size + COPY_CHUNK:
                    raise ValueError('归档压缩比异常，已拒绝读取。')
                total += item.file_size
                if total > MAX_TOTAL_UNCOMPRESSED:
                    raise ValueError('归档解压总大小超过 512 MB 上限。')
        return archive
    except Exception:
        archive.close()
        raise


def _android_extract(obj, entries: dict, hints: dict) -> None:
    """Normalize Android v3.1 JSON snapshots (player-*.json / responses / pairs / timeline)."""
    if not isinstance(obj, dict):
        return
    if 'player_id' in obj or 'characters' in obj or 'player_label' in obj:
        if 'status' not in obj:
            obj = dict(obj, status=1)
        entries.setdefault('PlayerMainDataRsp', []).append(obj)
    for list_key in ('responses', 'pairs', 'timeline', 'packets', 'messages'):
        rows = obj.get(list_key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            direction = str(row.get('direction') or row.get('dir') or '').lower()
            if 'c2s' in direction or 'req' == direction:
                continue
            name = row.get('msg_name') or row.get('name') or row.get('cmd_name') or row.get('message') or ''
            payload = row.get('decoded') if isinstance(row.get('decoded'), dict) else (
                row.get('payload') if isinstance(row.get('payload'), dict) else (
                    row.get('data') if isinstance(row.get('data'), dict) else row.get('body')
                )
            )
            if isinstance(name, str) and (name.endswith('Rsp') or name.endswith('Notice')):
                if isinstance(payload, dict):
                    entries.setdefault(name, []).append(payload)
            elif isinstance(payload, dict) and ('player_id' in payload or 'player_label' in payload):
                entries.setdefault('PlayerMainDataRsp', []).append(payload)
    player_obj = obj.get('player')
    if isinstance(player_obj, dict):
        _android_extract(player_obj, entries, hints)
    for uid_key in ('uid', 'player_uid', 'player_id', 'player_label', 'owner_uid'):
        val = _safe_uint(obj.get(uid_key))
        if val > 0 and not hints.get('uid'):
            hints['uid'] = val
    for name_key in ('player_name', 'nick_name', 'owner_name', 'name'):
        val = obj.get(name_key)
        if isinstance(val, str) and val.strip() and not hints.get('name'):
            hints['name'] = val.strip()


class _CollectedPackage:
    def __init__(self):
        self.entries: dict[str, list[dict]] = {}
        self.summary: dict = {}
        self.saved_game: dict | None = None
        self.direct_garden: dict | None = None
        self.format_tags: set[str] = set()
        self.hints: dict = {'uid': 0, 'name': '', 'level': 1}
        self.warnings: list[str] = []
        self.raw_bin_only: list[tuple[str, str, int | None, bytes]] = []
        self.has_json_s2c = False
        self.skipped_c2s = 0
        self.skipped_raw = 0
        self.duplicate_keys_recovered = False
        self.sha256 = ''

    def add_entry(self, msg_name: str, payload: dict) -> None:
        if not isinstance(payload, dict):
            return
        self.entries.setdefault(msg_name, []).append(payload)
        if 'status' not in payload and msg_name == 'PlayerMainDataRsp':
            payload['status'] = 1


def _ingest_member(pkg: _CollectedPackage, name: str, data: bytes, depth: int = 0) -> None:
    posix_lower = name.lower()
    base = posix_lower.rsplit('/', 1)[-1]
    suffix = Path(base).suffix.lower()

    if suffix in _RAW_CAPTURE_SUFFIXES:
        pkg.skipped_raw += 1
        pkg.format_tags.add('raw-capture-skipped')
        return
    if suffix == '.zip' and depth < _MAX_NESTED_ZIP_DEPTH and len(data) <= _MAX_NESTED_ZIP_BYTES:
        import io
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as nested:
                if len(nested.infolist()) <= 2000:
                    pkg.format_tags.add('nested-zip')
                    for sub in nested.infolist():
                        if sub.is_dir() or sub.file_size > MAX_MEMBER_UNCOMPRESSED:
                            continue
                        sub_name = sub.filename.replace('\\', '/')
                        if '..' in PurePosixPath(sub_name).parts:
                            continue
                        _ingest_member(pkg, sub_name, nested.read(sub), depth + 1)
        except zipfile.BadZipFile:
            pass
        return

    if suffix == '.bin':
        m_bin = _S2C_BIN_NAME.search(name)
        if m_bin:
            pkg.raw_bin_only.append((name, m_bin.group(2), int(m_bin.group(1)), data))
            return
        if base == 'scene_9999.bin' or base.startswith('garden_'):
            pkg.raw_bin_only.append((name, 'SceneDataNotice', 1016, data))
        return

    if suffix != '.json':
        return
    if '_c2s_' in posix_lower or posix_lower.startswith('requests/') or '/requests/' in posix_lower:
        pkg.skipped_c2s += 1
        pkg.format_tags.add('v1.4-or-c2s-filtered')
        return

    try:
        parsed = _tolerant_json_loads(data)
    except (ValueError, UnicodeError):
        pkg.warnings.append(f'已跳过无法解析的 JSON：{base}')
        return
    if not isinstance(parsed, dict):
        return

    # 1. Standard _s2c_ response JSON anywhere in tree (handles v1.5, v1.4, <prefix>/json/, 1/json/)
    m_json = _S2C_JSON_NAME.search(name)
    if m_json:
        pkg.has_json_s2c = True
        if name.lower().startswith(('export/json/', 'json/')):
            pkg.format_tags.add('capture-json')
        else:
            pkg.format_tags.add('prefixed-capture-json')
        pkg.add_entry(m_json.group(2), parsed)
        return

    # 2. Launcher metadata / summary.json
    if base == 'summary.json':
        pkg.summary.update(parsed)
        uids = [u for u in (_safe_uint(x) for x in capture_import.as_list(parsed.get('player_uids'))) if u > 0]
        if uids and not pkg.hints.get('uid'):
            pkg.hints['uid'] = uids[0]
        for hl in capture_import.as_list(parsed.get('player_highlights')):
            if isinstance(hl, dict):
                uid = _safe_uint(hl.get('uid') or hl.get('player_label') or hl.get('player_id'))
                if uid and not pkg.hints.get('uid'):
                    pkg.hints['uid'] = uid
                if hl.get('name') and not pkg.hints.get('name'):
                    pkg.hints['name'] = str(hl['name'])
                if hl.get('level'):
                    pkg.hints['level'] = max(_safe_int(hl.get('level'), 1), pkg.hints.get('level', 1))
        return

    # 3. Launcher accounts/<uid>/main-data.json & captured-responses.json
    if base == 'main-data.json':
        pkg.format_tags.add('account-backup-json')
        pkg.add_entry('PlayerMainDataRsp', parsed)
        return
    if base == 'captured-responses.json':
        pkg.format_tags.add('account-backup-json')
        for msg_name, items in parsed.items():
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        pkg.add_entry(str(msg_name), item)
        return

    # 4. Already built of_game save JSON
    if 'character' in parsed and 'item' in parsed and isinstance(parsed.get('item'), dict):
        pkg.format_tags.add('of-game-json')
        pkg.saved_game = parsed
        if parsed.get('uid'):
            pkg.hints['uid'] = _safe_uint(parsed.get('uid'))
        return

    # 5. Garden-only / my-scenes exports
    if base == 'index.json' and 'scenes' in parsed:
        pkg.format_tags.add('my-scenes-pack')
        if _safe_uint(parsed.get('owner_uid')) and not pkg.hints.get('uid'):
            pkg.hints['uid'] = _safe_uint(parsed.get('owner_uid'))
        if parsed.get('owner_name') and not pkg.hints.get('name'):
            pkg.hints['name'] = str(parsed.get('owner_name'))
        return
    if 'scene_id' in parsed and ('scene_garden_data' in parsed or 'collections' in parsed or 'areas' in parsed):
        pkg.format_tags.add('scene-pack')
        pkg.add_entry('SceneDataNotice', {'status': 1, 'data': parsed})
        sid = _safe_int(parsed.get('scene_id'))
        if sid == 9999:
            owner = _safe_uint(parsed.get('channel_label') or parsed.get('channel_id'))
            if owner > 1 and not pkg.hints.get('uid'):
                pkg.hints['uid'] = owner
        return
    if base.startswith('garden_') and ('_meta' in parsed or 'garden_furniture_info_map' in parsed):
        pkg.format_tags.add('garden-pack')
        meta = parsed.get('_meta') if isinstance(parsed.get('_meta'), dict) else {}
        owner = _safe_uint(meta.get('owner_uid') or meta.get('channel_label') or meta.get('channel_id'))
        if owner > 1 and not pkg.hints.get('uid'):
            pkg.hints['uid'] = owner
        if isinstance(parsed.get('garden_furniture_info_map'), dict):
            pkg.add_entry('SceneDataNotice', {
                'status': 1,
                'data': {
                    'scene_id': 9999,
                    'channel_id': owner or pkg.hints.get('uid') or 1,
                    'channel_label': owner or pkg.hints.get('uid') or 1,
                    'scene_garden_data': parsed,
                },
            })
        return

    # 6. Android APK v3.1 backups & generic response snapshots
    if base.startswith('player-') or '/responses/' in posix_lower or posix_lower.startswith('responses/') \
            or '/pairs/' in posix_lower or posix_lower.startswith('pairs/') or 'responses' in parsed or 'pairs' in parsed:
        pkg.format_tags.add('android-v31-backup')
        _android_extract(parsed, pkg.entries, pkg.hints)
        return

    # 7. Loose single-response JSON (e.g. direct PlayerMainDataRsp.json)
    if 'player_id' in parsed or 'player_label' in parsed or 'characters' in parsed:
        pkg.format_tags.add('loose-player-json')
        pkg.add_entry('PlayerMainDataRsp', parsed)
    elif 'weapons' in parsed:
        pkg.add_entry('GetWeaponRsp', parsed)
    elif 'armors' in parsed:
        pkg.add_entry('GetArmorRsp', parsed)
    elif 'posters' in parsed:
        pkg.add_entry('GetPosterRsp', parsed)
    elif 'items' in parsed:
        pkg.add_entry('PackNotice', parsed)


def _finalize_collected(pkg: _CollectedPackage, source_path: Path, uid_hint: int = 0) -> None:
    # Decode raw/*.bin messages only when corresponding _s2c_ JSON wasn't already loaded.
    if pkg.raw_bin_only and (not pkg.has_json_s2c or 'PlayerMainDataRsp' not in pkg.entries):
        proto_bundle = _load_proto_decoder()
        if proto_bundle[0] is not None:
            decoded_any = 0
            for _fname, name, msg_id, raw_bytes in pkg.raw_bin_only:
                if name in pkg.entries and pkg.has_json_s2c:
                    continue
                msg_name, decoded = _decode_raw_bin(name, msg_id, raw_bytes, proto_bundle)
                if msg_name and isinstance(decoded, dict):
                    pkg.add_entry(msg_name, decoded)
                    decoded_any += 1
            if decoded_any:
                pkg.format_tags.add('raw-protobuf-decoded')

    # Infer UID from filename if nothing inside payload specified one.
    if not pkg.hints.get('uid'):
        match = _FILENAME_UID_RE.search(source_path.stem)
        if match:
            candidate = _safe_uint(match.group(1))
            if candidate > 0:
                pkg.hints['uid'] = candidate
                pkg.hints['uidSource'] = 'filename'
    if uid_hint > 0 and not pkg.hints.get('uid'):
        pkg.hints['uid'] = int(uid_hint)
        pkg.hints['uidSource'] = 'fallback'


def _read_sqlite_source(path: Path, uid_hint: int = 0) -> dict:
    """Read an existing/older lolo.db save (e.g. 1.4.9 -> 1.5.0) read-only."""
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)) as con:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'of_game' not in tables and 'of_game_basic' not in tables:
            raise ValueError('SQLite 数据库中没有找到 of_game / of_game_basic 账号存档表。')
        candidates = []
        if 'of_game' in tables:
            candidates = [int(r[0]) for r in con.execute('SELECT user_id FROM of_game ORDER BY user_id')]
        elif 'of_game_basic' in tables:
            candidates = [int(r[0]) for r in con.execute('SELECT user_id FROM of_game_basic ORDER BY user_id')]
        if not candidates:
            raise ValueError('该 SQLite 数据库中没有任何已保存的角色存档。')
        chosen = int(uid_hint) if uid_hint and int(uid_hint) in candidates else None
        if chosen is None:
            if len(candidates) == 1:
                chosen = candidates[0]
            elif uid_hint:
                raise ValueError(f'数据库中未找到 UID {uid_hint}（现有：{candidates[:10]}）。')
            else:
                chosen = candidates[0]
        basic_row = {}
        if 'of_game_basic' in tables:
            cols = [r[1] for r in con.execute('PRAGMA table_info(of_game_basic)')]
            row = con.execute('SELECT * FROM of_game_basic WHERE user_id=?', (chosen,)).fetchone()
            if row:
                basic_row = dict(zip(cols, row))
        saved_game = {}
        if 'of_game' in tables:
            row = con.execute('SELECT bin_data FROM of_game WHERE user_id=?', (chosen,)).fetchone()
            if row and row[0]:
                raw = row[0]
                if raw[:2] == b'\x1f\x8b':
                    raw = gzip.decompress(raw)
                loaded = _tolerant_json_loads(raw)
                if isinstance(loaded, dict):
                    saved_game = loaded
        garden = None
        if 'of_home' in tables:
            hcols = [r[1] for r in con.execute('PRAGMA table_info(of_home)')]
            hrow = con.execute('SELECT * FROM of_home WHERE user_id=? ORDER BY id DESC LIMIT 1', (chosen,)).fetchone()
            if hrow:
                hm = dict(zip(hcols, hrow))
                furn_raw = hm.get('garden_furniture_info_map') or b'{}'
                placed_raw = hm.get('placed_character_map') or b'[]'
                if isinstance(furn_raw, bytes):
                    furn_raw = furn_raw.decode('utf-8', errors='replace')
                if isinstance(placed_raw, bytes):
                    placed_raw = placed_raw.decode('utf-8', errors='replace')
                furn = json.loads(furn_raw) if isinstance(furn_raw, str) and furn_raw.strip() else {}
                placed = json.loads(placed_raw) if isinstance(placed_raw, str) and placed_raw.strip() else []
                garden = {
                    'scene_id': _safe_int(hm.get('scene_id'), 9999) or 9999,
                    'garden_name': str(hm.get('garden_name') or ''),
                    'likes_num': _safe_int(hm.get('likes_num')),
                    'access_player_num': _safe_int(hm.get('access_player_num')),
                    'left_like_num': _safe_int(hm.get('left_like_num')),
                    'is_open': 1 if hm.get('is_open') else 0,
                    'password': '',
                    'password_expire_time': _safe_int(hm.get('password_expire_time')),
                    'furniture': furn if isinstance(furn, dict) else {},
                    'placed': placed if isinstance(placed, list) else [],
                }
        return {
            'kind': 'sqlite-db',
            'uid': chosen,
            'uidSource': 'sqlite',
            'availableUids': candidates,
            'basicRow': basic_row,
            'savedGame': saved_game,
            'garden': garden,
            'sha256': _file_sha256(path),
        }


def _is_sqlite_file(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.suffix.lower() in ('.db', '.sqlite', '.sqlite3'):
        return True
    try:
        with path.open('rb') as stream:
            return stream.read(16) == _SQLITE_MAGIC
    except OSError:
        return False


def _collect_source(path: Path, uid_hint: int = 0) -> tuple[_CollectedPackage, dict | None]:
    path = Path(path)
    if not path.exists():
        raise ValueError('所选导入路径不存在。')
    if _is_sqlite_file(path):
        return _CollectedPackage(), _read_sqlite_source(path, uid_hint=uid_hint)

    pkg = _CollectedPackage()
    if path.is_dir():
        pkg.format_tags.add('directory')
        files = sorted(p for p in path.rglob('*') if p.is_file())
        if len(files) > MAX_MEMBERS:
            raise ValueError('文件夹中的文件数量超过安全上限。')
        for item in files:
            if item.stat().st_size > MAX_MEMBER_UNCOMPRESSED:
                continue
            rel = item.relative_to(path).as_posix()
            _ingest_member(pkg, rel, item.read_bytes(), depth=0)
        pkg.sha256 = _dir_sha256(path)
    elif path.is_file() and path.suffix.lower() == '.json':
        pkg.format_tags.add('single-json')
        _ingest_member(pkg, path.name, path.read_bytes(), depth=0)
        # Auto-pick sibling captured-responses.json inside launcher accounts/<uid>/ folder.
        sibling = path.with_name('captured-responses.json')
        if path.name.lower() == 'main-data.json' and sibling.is_file():
            _ingest_member(pkg, 'captured-responses.json', sibling.read_bytes(), depth=0)
        pkg.sha256 = _file_sha256(path)
    elif path.is_file() and zipfile.is_zipfile(path):
        with _validate_zip_structure_only(path) as archive:
            for item in archive.infolist():
                if item.is_dir():
                    continue
                _ingest_member(pkg, item.filename, read_member(archive, item), depth=0)
        pkg.sha256 = _file_sha256(path)
    else:
        raise ValueError('不支持的导入格式；请选择 .zip / .json / .db 或解包文件夹。')

    _finalize_collected(pkg, path, uid_hint=uid_hint)
    return pkg, None


def _select_tolerant_player(pkg: _CollectedPackage, uid_override: int = 0) -> tuple[dict, int, str]:
    """Select the primary player packet or build a valid empty shell when missing."""
    candidates = [p for p in pkg.entries.get('PlayerMainDataRsp', []) if isinstance(p, dict)]
    if candidates:
        # Prefer last packet that carries a valid status & UID.
        selected = None
        for pkt in reversed(candidates):
            status = pkt.get('status')
            if status in (None, 1, 'StatusCode_OK') and (_safe_uint(pkt.get('player_label')) or _safe_uint(pkt.get('player_id'))):
                selected = dict(pkt)
                break
        if selected is None:
            selected = dict(candidates[-1])
        uid = _safe_uint(selected.get('player_label')) or _safe_uint(selected.get('player_id'))
        if uid > 0:
            return selected, uid, 'capture'
        if uid_override > 0:
            selected['player_id'] = uid_override
            selected['player_label'] = uid_override
            return selected, int(uid_override), 'fallback'
        if pkg.hints.get('uid'):
            inferred = int(pkg.hints['uid'])
            selected['player_id'] = inferred
            selected['player_label'] = inferred
            return selected, inferred, pkg.hints.get('uidSource') or 'metadata'
        return selected, 0, 'none'

    # Synthesize minimal empty PlayerMainDataRsp when package omits it (e.g. garden/equipment/scene pack).
    uid = int(uid_override or pkg.hints.get('uid') or 0)
    source = 'fallback' if uid_override else (pkg.hints.get('uidSource') or 'metadata' if pkg.hints.get('uid') else 'none')
    pkg.format_tags.add('partial-empty-shell')
    shell = {
        'status': 1,
        'player_id': uid,
        'player_label': uid,
        'player_name': pkg.hints.get('name') or (f'UID {uid}' if uid else ''),
        'level': _safe_int(pkg.hints.get('level'), 1) or 1,
        'exp': 0,
        'characters': [],
        'team': {},
    }
    return shell, uid, source


def _tolerant_build_player(main: dict, entries: dict, *, resource_dir: Path | None = None) -> tuple[dict, dict]:
    """Assemble full Lolo bin_data payload tolerantly, leaving missing modules empty."""
    _tolerant_restore(main, entries)
    character_map = {}
    for captured in capture_import.as_list(main.get('characters')):
        if not isinstance(captured, dict):
            continue
        cid = _safe_uint(captured.get('character_id'))
        if cid <= 0:
            continue
        data = dict(captured)
        for source_key, canonical_key in (
            ('character_appearance', 'characterAppearance'),
            ('character_skill_list', 'characterSkillList'),
            ('equipment_presets', 'equipmentPresets'),
            ('outfit_presets', 'outfitPresets'),
        ):
            if source_key in data and canonical_key not in data:
                data[canonical_key] = data[source_key]
            data.pop(source_key, None)
        data.update(item_id=cid, num=1, item_type='EBagItemTag_Card', pack_type=0)
        appearance = captured.get('characterAppearance', captured.get('appearance', captured.get('character_appearance')))
        data['characterAppearance'] = appearance if isinstance(appearance, dict) and not capture_import.is_placeholder(appearance) else {'badge': 5000}
        skills = captured.get('skill_list', captured.get('skills', captured.get('characterSkillList', captured.get('character_skill_list'))))
        skill_map = {}
        for value in capture_import.as_list(skills):
            if isinstance(value, dict) and 'skill_id' in value:
                skill_map[str(_safe_int(value['skill_id']))] = value
        data['characterSkillList'] = skill_map
        presets_out = {}
        for index, preset in enumerate(capture_import.as_list(captured.get('equipment_presets', captured.get('equipmentPresets')))):
            if not isinstance(preset, dict):
                continue
            armors = {}
            for value in capture_import.as_list(preset.get('armors')):
                if not isinstance(value, dict) or capture_import.is_placeholder(value):
                    continue
                inst = _safe_uint(value.get('armor_id'))
                if inst <= 0:
                    continue
                eq_type = _safe_int(value.get('equip_type'))
                armors[str(eq_type)] = {'equipType': eq_type, 'instanceId': inst}
            posters = {}
            for value in capture_import.as_list(preset.get('posters')):
                if not isinstance(value, dict) or capture_import.is_placeholder(value):
                    continue
                inst = _safe_uint(value.get('poster_id'))
                if inst <= 0:
                    continue
                p_idx = _safe_int(value.get('poster_index'))
                posters[str(p_idx)] = {'posterIndex': p_idx, 'instanceId': inst}
            presets_out[str(index)] = {
                'presetIndex': _safe_int(preset.get('preset_index'), index),
                'weaponInstanceId': _safe_uint(preset.get('weapon', preset.get('weaponInstanceId'))),
                'armors': armors,
                'posters': posters,
            }
        data['equipmentPresets'] = presets_out
        outfits = {}
        for value in capture_import.as_list(captured.get('outfit_presets', captured.get('outfitPresets'))):
            if isinstance(value, dict):
                outfits[str(_safe_int(value.get('preset_index'), len(outfits)))] = value
        data['outfitPresets'] = outfits
        character_map[str(cid)] = _tolerant_model('CharacterInfo', data) or {}

    items = {name: {} for name in (
        'itemBaseInfo', 'itemWeaponMap', 'itemArmorMap', 'itemPosterMap',
        'itemFashionMap', 'itemInscriptionMap', 'itemPetInfoMap', 'itemHeadMap', 'furnitureItemMap',
    )}
    greatest = 0
    for packet in capture_import.as_list(entries.get('PackNotice')):
        if not isinstance(packet, dict):
            continue
        for detail in capture_import.as_list(packet.get('items')):
            if not isinstance(detail, dict):
                continue
            item = detail.get('main_item') if isinstance(detail.get('main_item'), dict) else detail
            if not isinstance(item, dict):
                continue
            base = item.get('base_item')
            if isinstance(base, dict) and 'item_id' in base:
                item_id = _safe_uint(base.get('item_id'))
                if item_id > 0:
                    data = dict(base, item_type=_safe_int(item.get('item_tag')), pack_type=_safe_int(detail.get('pack_type')))
                    built = _tolerant_model('ItemBaseInfo', data)
                    if isinstance(built, dict):
                        items['itemBaseInfo'][str(item_id)] = built
            fashion = item.get('outfit')
            if isinstance(fashion, dict) and not capture_import.is_placeholder(fashion):
                outfit_id = _safe_uint(fashion.get('outfit_id') or item.get('item_id'))
                if outfit_id > 0:
                    data = dict(fashion, item_id=item.get('item_id') or outfit_id, outfit_id=outfit_id,
                                item_type=_safe_int(item.get('item_tag')), num=1)
                    schemes = {}
                    for value in capture_import.as_list(fashion.get('dye_schemes', fashion.get('dyeSchemes'))):
                        if isinstance(value, dict):
                            schemes[str(_safe_int(value.get('scheme_index'), len(schemes)))] = value
                    data['dyeSchemes'] = schemes
                    data.pop('dye_schemes', None)
                    built = _tolerant_model('ItemFashionInfo', data)
                    if isinstance(built, dict):
                        items['itemFashionMap'][str(outfit_id)] = built

    for packet_kind, collection, target, kind, id_key, tag in [
        ('GetWeaponRsp', 'weapons', 'itemWeaponMap', 'ItemWeaponInfo', 'weapon_id', 'EBagItemTag_Weapon'),
        ('GetArmorRsp', 'armors', 'itemArmorMap', 'ItemArmorInfo', 'armor_id', 'EBagItemTag_Armor'),
        ('GetPosterRsp', 'posters', 'itemPosterMap', 'ItemPosterInfo', 'poster_id', 'EBagItemTag_Poster'),
    ]:
        for packet in capture_import.as_list(entries.get(packet_kind)):
            if not isinstance(packet, dict):
                continue
            for captured in capture_import.as_list(packet.get(collection)):
                if not isinstance(captured, dict):
                    continue
                instance = _safe_uint(captured.get('instance_id')) or _safe_uint(captured.get(id_key))
                if instance <= 0:
                    continue
                greatest = max(greatest, instance)
                data = dict(captured, item_id=captured.get(id_key) or instance, instance_id=instance, item_type=tag, num=1, pack_type=0)
                built = _tolerant_model(kind, data)
                if isinstance(built, dict):
                    items[target][str(instance)] = built

    for entry in capture_import.as_list(main.get('furniture_item_info')):
        if not isinstance(entry, dict):
            continue
        item_id = _safe_uint(entry.get('item_id'))
        if item_id <= 0:
            continue
        items['furnitureItemMap'][str(item_id)] = {'itemId': item_id, 'num': max(1, _safe_int(entry.get('num'), 1))}

    variants = [
        ('weapon', 'itemWeaponMap', 'ItemWeaponInfo', 'weapon_id'),
        ('armor', 'itemArmorMap', 'ItemArmorInfo', 'armor_id'),
        ('poster', 'itemPosterMap', 'ItemPosterInfo', 'poster_id'),
        ('inscription', 'itemInscriptionMap', 'ItemInscriptionInfo', 'inscription_id'),
    ]
    for packet in capture_import.as_list(entries.get('PackNotice')):
        if not isinstance(packet, dict):
            continue
        for detail in capture_import.as_list(packet.get('items')):
            if not isinstance(detail, dict):
                continue
            main_item = detail.get('main_item') if isinstance(detail.get('main_item'), dict) else detail
            if not isinstance(main_item, dict):
                continue
            for source, target, kind, id_key in variants:
                value = main_item.get(source)
                if not isinstance(value, dict) or capture_import.is_placeholder(value):
                    continue
                key = _safe_uint(value.get('instance_id')) or _safe_uint(value.get(id_key))
                if key <= 0:
                    continue
                if 'instance_id' in value:
                    greatest = max(greatest, key)
                data = dict(value, item_id=value.get(id_key) or main_item.get('item_id') or key,
                            item_type=_safe_int(main_item.get('item_tag')),
                            pack_type=_safe_int(detail.get('pack_type')), num=1)
                built = _tolerant_model(kind, data) or {}
                merged = dict(items[target].get(str(key), {}))
                merged.update(built)
                items[target][str(key)] = merged

    items['instanceIndex'] = greatest + 1
    team = capture_import.as_mapping(main.get('team'))
    scene_map = {}
    try:
        scene_map = capture_import.build_scene_map(main, entries, resource_dir=resource_dir) or {}
    except Exception:
        scene_map = {}

    scene_data = {
        'worldLevel': _safe_int(main.get('world_level')),
        'cur_pet_instance_id': _safe_uint(main.get('pet_instance_id')),
    }
    if scene_map:
        scene_data['sceneMap'] = scene_map
    saved = {
        'instanceIndex': greatest + 1,
        'character': {'characterMap': character_map, 'placedCharacters': capture_import.as_list(main.get('placed_characters'))},
        'item': items,
        'team': {'TeamInfo': {
            'Char1': _safe_int(team.get('char1'), 101001),
            'Char2': _safe_int(team.get('char2')),
            'Char3': _safe_int(team.get('char3')),
        }},
        'scene': scene_data,
    }
    return saved, items


def _tolerant_build_garden(main: dict, entries: dict, uid: int) -> dict | None:
    try:
        temp_main = dict(main)
        if uid and not temp_main.get('player_label'):
            temp_main['player_label'] = uid
        garden = capture_import.build_garden(temp_main, entries)
        if garden is not None:
            return garden
    except Exception:
        pass
    # Fallback for standalone garden_1_* packs where channel=1 differed from final uid:
    for notice in capture_import.as_list(entries.get('SceneDataNotice')):
        data = (notice or {}).get('data') if isinstance(notice, dict) else None
        if not isinstance(data, dict) or _safe_int(data.get('scene_id')) != 9999:
            continue
        garden = data.get('scene_garden_data')
        if isinstance(garden, dict) and garden:
            furniture = capture_import.as_mapping(garden.get('garden_furniture_info_map'))
            items = {}
            for key, detail in furniture.items():
                if not isinstance(detail, dict):
                    continue
                fid = _safe_uint(detail.get('furniture_id'), _safe_uint(key))
                if fid <= 0:
                    continue
                stored = {
                    'furnitureId': fid,
                    'furnitureItemId': _safe_int(detail.get('furniture_item_id')),
                    'pos': capture_import.garden_vector(detail.get('pos')),
                    'rotation': capture_import.garden_vector(detail.get('rotation')),
                }
                layer = _safe_int(detail.get('layer_num'))
                if layer:
                    stored['layerNum'] = layer
                items[str(fid)] = stored
            return {
                'scene_id': 9999,
                'garden_name': str(garden.get('garden_name') or ''),
                'likes_num': _safe_int(garden.get('likes_num')),
                'access_player_num': _safe_int(garden.get('access_player_num')),
                'left_like_num': _safe_int(garden.get('left_like_num')),
                'is_open': 1 if garden.get('is_open') else 0,
                'password': '',
                'password_expire_time': _safe_int(garden.get('password_expire_time')),
                'furniture': items,
                'placed': [c for c in capture_import.as_list(data.get('placed_characters')) if isinstance(c, dict)],
            }
    return None


def _summarize_sections(saved: dict, garden: dict | None) -> tuple[list[str], list[str], dict]:
    char_map = (saved.get('character') or {}).get('characterMap') or {}
    item = saved.get('item') or {}
    bag = item.get('itemBaseInfo') or {}
    weapons = item.get('itemWeaponMap') or {}
    armors = item.get('itemArmorMap') or {}
    posters = item.get('itemPosterMap') or {}
    outfits = item.get('itemFashionMap') or {}
    scene_map = (saved.get('scene') or {}).get('sceneMap') or {}
    checks = [
        ('characters', bool(char_map), len(char_map)),
        ('bag', bool(bag), len(bag)),
        ('weapons', bool(weapons), len(weapons)),
        ('armor', bool(armors), len(armors)),
        ('posters', bool(posters), len(posters)),
        ('outfits', bool(outfits), len(outfits)),
        ('garden', garden is not None, len((garden or {}).get('furniture') or {})),
        ('exploration', bool(scene_map), len(scene_map)),
    ]
    present = [name for name, has, _cnt in checks if has]
    empty = [name for name, has, _cnt in checks if not has]
    counts = {name: cnt for name, _has, cnt in checks}
    return present, empty, counts


def _ensure_database(profile: Path | None, database: Path | None) -> Path | None:
    if database is not None:
        db = Path(database).resolve()
    elif profile is not None:
        db = Path(profile).resolve() / 'db' / 'lolo.db'
    else:
        return None
    if not db.is_file():
        template_candidates = (
            BASE.parent / 'server-kit' / 'db' / 'lolo.db',
            BASE / 'server-kit' / 'db' / 'lolo.db',
        )
        for template in template_candidates:
            if template.is_file():
                capture_import.schema_database(template, db)
                break
    return db


def inspect_package(path, uid_hint: int = 0) -> dict:
    """Inspect any supported archive, directory, JSON, or SQLite DB without writing anything."""
    path = Path(path)
    uid_hint = uint32(uid_hint, allow_zero=True, label='UID') if uid_hint else 0
    pkg, sqlite_data = _collect_source(path, uid_hint=uid_hint)
    if sqlite_data is not None:
        saved = sqlite_data['savedGame']
        garden = sqlite_data['garden']
        basic = sqlite_data['basicRow']
        present, empty, counts = _summarize_sections(saved, garden)
        return {
            'uid': sqlite_data['uid'],
            'uidSource': 'sqlite',
            'needsUid': False,
            'name': str(basic.get('nick_name') or ''),
            'level': _safe_int(basic.get('level'), 1) or 1,
            'characters': counts['characters'],
            'equipment': counts['weapons'] + counts['armor'] + counts['posters'],
            'bagItems': counts['bag'],
            'gardenFurniture': counts['garden'],
            'explorationScenes': counts['exploration'],
            'capturedMessages': 0,
            'sourceFormat': 'sqlite-db',
            'versionHint': 'SQLite lolo.db 存档',
            'presentSections': present,
            'emptySections': empty,
            'availableUids': sqlite_data['availableUids'],
            'warnings': [],
            'sha256': sqlite_data['sha256'],
        }

    main, uid, uid_source = _select_tolerant_player(pkg, uid_override=uid_hint)
    if pkg.saved_game is not None:
        saved = pkg.saved_game
        garden = _tolerant_build_garden(main, pkg.entries, uid)
    else:
        saved, _items = _tolerant_build_player(main, pkg.entries)
        garden = _tolerant_build_garden(main, pkg.entries, uid)
    present, empty, counts = _summarize_sections(saved, garden)
    tags = sorted(pkg.format_tags)
    if 'v1.4-or-c2s-filtered' in pkg.format_tags or 'prefixed-capture-json' in pkg.format_tags:
        ver_hint = 'v1.4.x / 兼容扩展包'
    elif 'android-v31-backup' in pkg.format_tags:
        ver_hint = '安卓 3.1 第三方备份'
    elif 'raw-protobuf-decoded' in pkg.format_tags:
        ver_hint = '原始 Protobuf (.bin) 包'
    elif 'garden-pack' in pkg.format_tags or 'my-scenes-pack' in pkg.format_tags:
        ver_hint = '家园 / 场景独立包'
    elif 'account-backup-json' in pkg.format_tags or 'of-game-json' in pkg.format_tags:
        ver_hint = '账号 JSON 存档'
    else:
        ver_hint = 'v1.5+ 标准抓包 ZIP'
    return {
        'uid': uid,
        'uidSource': uid_source,
        'needsUid': uid <= 0,
        'name': str(main.get('player_name') or ''),
        'level': _safe_int(main.get('level'), 1) or 1,
        'characters': counts['characters'],
        'equipment': counts['weapons'] + counts['armor'] + counts['posters'],
        'bagItems': counts['bag'],
        'gardenFurniture': counts['garden'],
        'explorationScenes': counts['exploration'],
        'capturedMessages': sum(len(v) for v in pkg.entries.values()),
        'sourceFormat': '+'.join(tags) if tags else 'unknown',
        'versionHint': ver_hint,
        'presentSections': present,
        'emptySections': empty,
        'skippedRequests': pkg.skipped_c2s,
        'skippedRawCaptures': pkg.skipped_raw,
        'warnings': pkg.warnings[:8],
        'sha256': pkg.sha256,
    }


def plan_package(path, profile: Path | None = None, database: Path | None = None, uid_hint: int = 0) -> dict:
    db = _ensure_database(profile, database)
    info = inspect_package(path, uid_hint=uid_hint)
    uid = info['uid']
    conflict = False
    existing = []
    if db is not None and db.is_file():
        with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)) as con:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'of_user' in tables:
                existing = [int(r[0]) for r in con.execute('SELECT user_id FROM of_user ORDER BY user_id')]
            if uid > 0:
                has_game = ('of_game' in tables) and bool(con.execute('SELECT 1 FROM of_game WHERE user_id=?', (uid,)).fetchone())
                has_basic = ('of_game_basic' in tables) and bool(con.execute('SELECT 1 FROM of_game_basic WHERE user_id=?', (uid,)).fetchone())
                conflict = has_game or has_basic
    if profile is not None and uid > 0:
        conflict = conflict or (Path(profile) / 'accounts' / str(uid)).exists()
    info.update(
        database=str(db) if db is not None else '',
        conflict=conflict,
        conflictWith=f'UID {uid}' if conflict else '',
        existingUids=existing,
    )
    return info


def import_package(path, profile: Path, uid: int = 0, *, allow_overwrite: bool = False,
                   database: Path | None = None) -> dict:
    """Import any supported archive, directory, JSON, or DB into profile cleanly."""
    path = Path(path).resolve()
    profile = Path(profile).resolve()
    uid_override = uint32(uid, allow_zero=True, label='UID') if uid else 0
    db = _ensure_database(profile, database)
    if db is None or not db.is_file():
        raise ValueError('找不到可用的服务端数据库（请先准备运行模式或检查 server-kit）。')

    pkg, sqlite_data = _collect_source(path, uid_hint=uid_override)
    if sqlite_data is not None:
        final_uid = uid_override or sqlite_data['uid']
        main = {
            'status': 1, 'player_id': final_uid, 'player_label': final_uid,
            'player_name': str(sqlite_data['basicRow'].get('nick_name') or f'UID {final_uid}'),
            'level': _safe_int(sqlite_data['basicRow'].get('level'), 1) or 1,
            'characters': [],
        }
        entries = {}
        saved = sqlite_data['savedGame'] or {
            'instanceIndex': 1,
            'character': {'characterMap': {}, 'placedCharacters': []},
            'item': dict({k: {} for k in ('itemBaseInfo', 'itemWeaponMap', 'itemArmorMap', 'itemPosterMap',
                                           'itemFashionMap', 'itemInscriptionMap', 'itemPetInfoMap', 'itemHeadMap', 'furnitureItemMap')},
                         instanceIndex=1),
            'team': {'TeamInfo': {'Char1': 101001, 'Char2': 0, 'Char3': 0}},
            'scene': {'worldLevel': 0, 'cur_pet_instance_id': 0},
        }
        garden = sqlite_data['garden']
        version_hint = 'SQLite lolo.db 存档'
        source_format = 'sqlite-db'
        source_hash = sqlite_data['sha256']
        warnings = []
    else:
        main, detected_uid, _src = _select_tolerant_player(pkg, uid_override=uid_override)
        final_uid = uid_override or detected_uid
        if not final_uid or final_uid <= 0:
            raise ValueError('这个资源包中没有包含 UID；请在界面填写 UID 后一键导入。')
        entries = pkg.entries
        if pkg.saved_game is not None:
            saved = pkg.saved_game
        else:
            saved, _items = _tolerant_build_player(main, entries, resource_dir=profile / 'Resource')
        garden = _tolerant_build_garden(main, entries, final_uid)
        tags = sorted(pkg.format_tags)
        source_format = '+'.join(tags) if tags else 'compatible-pack'
        version_hint = inspect_package(path, uid_hint=final_uid)['versionHint']
        source_hash = pkg.sha256
        warnings = list(pkg.warnings)

    final_uid = uint32(final_uid, label='UID')
    present, empty, counts = _summarize_sections(saved, garden)
    had_existing = capture_import._preflight(profile, db, final_uid, allow_overwrite=allow_overwrite)

    payload = gzip.compress(json.dumps(
        saved, ensure_ascii=False, separators=(',', ':'), allow_nan=False,
    ).encode('utf-8'), mtime=0)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    created_raw = _safe_uint(main.get('create_time'))
    if created_raw > 253402300799:
        created_raw = 0
    created = dt.datetime.fromtimestamp(created_raw, dt.timezone.utc).isoformat()

    if sqlite_data is not None and sqlite_data['basicRow']:
        br = sqlite_data['basicRow']
        basic = {
            'user_id': final_uid,
            'nick_name': str(br.get('nick_name') or f'UID {final_uid}'),
            'level': max(0, _safe_int(br.get('level'), 1)),
            'exp': _safe_int(br.get('exp')),
            'head': _safe_int(br.get('head'), 41101),
            'sex': _safe_int(br.get('sex')),
            'phone_background': _safe_int(br.get('phone_background'), 8000),
            'sign': str(br.get('sign') or ''),
            'created_at': str(br.get('created_at') or created),
            'updated_at': now,
            'birthday': str(br.get('birthday') or ''),
            'is_hide_birthday': bool(br.get('is_hide_birthday', False)),
            'avatar_frame': _safe_int(br.get('avatar_frame')),
            'pendant': _safe_int(br.get('pendant')),
            'character_id': _safe_int(br.get('character_id'), 101001),
            'hide_value': _safe_int(br.get('hide_value')),
        }
    else:
        appearance = main.get('appearance')
        appearance = appearance if isinstance(appearance, dict) and not capture_import.is_placeholder(appearance) else {}
        team = capture_import.as_mapping(main.get('team'))
        raw_sex = main.get('sex')
        sex_val = capture_import.ENUMS.get(raw_sex, raw_sex) if isinstance(raw_sex, (str, int)) else 0
        basic = {
            'user_id': final_uid,
            'nick_name': str(main.get('player_name') or f'UID {final_uid}'),
            'level': max(0, _safe_int(main.get('level'), 1)),
            'exp': _safe_int(main.get('exp')),
            'head': _safe_int(main.get('head'), 41101),
            'sex': _safe_int(sex_val, 0),
            'phone_background': _safe_int(main.get('phone_background'), 8000),
            'sign': str(main.get('sign') or ''),
            'created_at': created,
            'updated_at': now,
            'birthday': str(main.get('birthday') or ''),
            'is_hide_birthday': bool(main.get('is_hide_birthday', False)),
            'avatar_frame': _safe_int(appearance.get('avatar_frame')),
            'pendant': _safe_int(appearance.get('pendant')),
            'character_id': _safe_int(team.get('char1'), 101001),
            'hide_value': _safe_int(main.get('hide_value')),
        }

    task = capture_import._new_task(profile)
    try:
        stamp = dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        backup = profile / 'backups' / f'before-import-{stamp}.db'
        backup.parent.mkdir(parents=True, exist_ok=True)
        temp_backup = task / 'backup.db'
        with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)) as origin:
            with closing(sqlite3.connect(temp_backup)) as target:
                origin.backup(target)
                if target.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                    raise ValueError('SQLite 备份完整性校验失败。')
        _publish_file(temp_backup, backup, overwrite=False)

        staged = task / 'account'
        staged.mkdir()
        (staged / 'main-data.json').write_text(json.dumps(main, ensure_ascii=False, indent=2), encoding='utf-8')
        (staged / 'captured-responses.json').write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding='utf-8')
        scene_map = (saved.get('scene') or {}).get('sceneMap') or {}
        result = {
            'uid': final_uid,
            'internalId': _safe_uint(main.get('player_id')) or final_uid,
            'name': basic['nick_name'],
            'level': basic['level'],
            'characters': counts['characters'],
            'capturedMessages': sum(len(v) for v in entries.values()),
            'sha256': source_hash,
            'database': str(db),
            'backup': str(backup),
            'overwroteExisting': bool(had_existing),
            'sourceFormat': source_format,
            'versionHint': version_hint,
            'presentSections': present,
            'emptySections': empty,
            'warnings': warnings[:8],
            'restored': present or ['basic'],
            'preservedOnly': ['quest state', 'activities', 'bless tree', 'other modules not persisted by this Lolo version'],
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
        if garden is not None:
            result['garden'] = {'sceneId': 9999, 'furniture': len(garden['furniture']),
                                'placedCharacters': len(garden['placed'])}
        (staged / 'import-report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')

        capture_import._write_database(db, final_uid, created, now, payload, basic, garden, allow_overwrite=allow_overwrite)

        destination = profile / 'accounts' / str(final_uid)
        destination.parent.mkdir(parents=True, exist_ok=True)
        previous_account = None
        if destination.exists():
            previous_account = profile / 'backups' / f'account-{final_uid}-before-import-{stamp}'
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


def _win_pick_package_via_powershell():
    if os.name != 'nt':
        return None
    import subprocess
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$d = New-Object System.Windows.Forms.OpenFileDialog; "
        "$d.Title = '一键导入：选择 1.4/1.5/安卓 ZIP、JSON 或 lolo.db'; "
        "$d.Filter = '支持导入包 (*.zip;*.json;*.db;*.sqlite)|*.zip;*.json;*.db;*.sqlite|所有文件 (*.*)|*.*'; "
        "if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Out.Write($d.FileName) }"
    )
    res = subprocess.run(
        ['powershell.exe', '-NoProfile', '-STA', '-Command', ps],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
    )
    picked = (res.stdout or '').strip()
    return Path(picked) if picked else None


def _run_interactive_gui(initial_path: Path | None = None) -> int:
    install_root = BASE.parent if BASE.name.lower() in ('app', 'backup-tool') else BASE
    profile_dir = install_root / 'profiles' / 'solo'
    for candidate_mode in ('solo', 'steam', 'host'):
        if (install_root / 'profiles' / candidate_mode / 'db' / 'lolo.db').is_file():
            profile_dir = install_root / 'profiles' / candidate_mode
            break

    try:
        try:
            import tkinter as tk
            from tkinter import filedialog, messagebox, simpledialog
            has_tk = True
        except ImportError:
            has_tk = False

        if has_tk:
            root = tk.Tk()
            root.withdraw()
            target = initial_path
            if target is None:
                choice = filedialog.askopenfilename(
                    parent=root,
                    title='一键导入工具：选择资源包 / 账号ZIP / JSON / lolo.db',
                    filetypes=[('所有支持包', '*.zip *.json *.db *.sqlite'), ('所有文件', '*.*')],
                )
                if choice:
                    target = Path(choice)
                else:
                    folder = filedialog.askdirectory(parent=root, title='或选择一个已解包的资源文件夹', mustexist=True)
                    if folder:
                        target = Path(folder)
            if target is None:
                root.destroy()
                return 1
            plan = plan_package(target, profile=profile_dir)
            uid = int(plan.get('uid') or 0)
            if uid <= 0:
                entered = simpledialog.askinteger('指定目标 UID', '此资源包未包含 UID，请输入要导入的本地 UID（1 以上整数）：',
                                                  parent=root, minvalue=1, maxvalue=0xFFFFFFFF, initialvalue=1000000)
                if not entered:
                    root.destroy()
                    return 1
                uid = int(entered)
                plan = plan_package(target, profile=profile_dir, uid_hint=uid)
            present_txt = '、'.join(plan['presentSections']) or '无'
            empty_txt = '、'.join(plan['emptySections']) or '无'
            prompt = (
                f"格式：{plan['versionHint']}\n"
                f"目标模式：{profile_dir.name}\n"
                f"目标 UID：{uid} ({plan.get('name') or '未命名'})\n"
                f"包含模块：{present_txt}\n"
                f"缺失留空：{empty_txt}\n\n"
                + ("检测到已有 UID 存档；继续将先自动备份后覆盖。" if plan.get('conflict') else "确认一键导入到本地存档库？")
            )
            if not messagebox.askokcancel('确认一键导入', prompt, parent=root):
                root.destroy()
                return 1
            result = import_package(target, profile_dir, uid=uid, allow_overwrite=bool(plan.get('conflict')))
            messagebox.showinfo('一键导入完成', f"已导入 UID {result['uid']}\n数据库：{result['database']}", parent=root)
            root.destroy()
            return 0

        target = initial_path or _win_pick_package_via_powershell()
        if target is None:
            raw = input('请输入要导入的资源包 / 文件夹路径（可直接拖入窗口）：').strip().strip('"')
            if not raw:
                return 1
            target = Path(raw)
        plan = plan_package(target, profile=profile_dir)
        uid = int(plan.get('uid') or 0)
        if uid <= 0:
            entered = input('资源包缺少 UID，请输入要写入的本地 UID (默认 1000000): ').strip()
            uid = int(entered) if entered.isdigit() and int(entered) > 0 else 1000000
            plan = plan_package(target, profile=profile_dir, uid_hint=uid)
        print(f"格式: {plan['versionHint']} | UID: {uid} | 包含: {plan['presentSections']} | 留空: {plan['emptySections']}")
        ans = input('确认导入? [Y/n]: ').strip().lower()
        if ans and ans not in ('y', 'yes'):
            return 1
        result = import_package(target, profile_dir, uid=uid, allow_overwrite=bool(plan.get('conflict')))
        print(f"导入成功！UID={result['uid']} -> {result['database']}")
        return 0
    except Exception as exc:
        print(json.dumps({'error': safe_error(exc)}, ensure_ascii=False))
        return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', choices=['inspect', 'plan', 'import', 'gui'], default='inspect')
    parser.add_argument('positional_file', nargs='?', type=Path)
    parser.add_argument('--file', type=Path)
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--database', type=Path)
    parser.add_argument('--uid', type=int, default=0)
    parser.add_argument('--allow-overwrite', action='store_true')
    parser.add_argument('--gui', action='store_true')
    args = parser.parse_args(argv)
    target = args.file or args.positional_file
    if args.gui or args.action == 'gui':
        return _run_interactive_gui(target)
    if target is None:
        print(json.dumps({'error': '请通过 --file 指定要导入的资源包路径。'}, ensure_ascii=False))
        return 1
    try:
        if args.action == 'inspect':
            result = inspect_package(target, uid_hint=args.uid)
        elif args.action == 'plan':
            result = plan_package(target, profile=args.profile, database=args.database, uid_hint=args.uid)
        else:
            profile = args.profile or (args.database.resolve().parent.parent if args.database else None)
            if profile is None:
                raise ValueError('请通过 --profile 或 --database 指定目标存档目录。')
            result = import_package(
                target, profile, uid=args.uid,
                allow_overwrite=args.allow_overwrite, database=args.database,
            )
        print(json.dumps(redact_result(result), ensure_ascii=False))
        return 0
    except (ValueError, OSError, zipfile.BadZipFile, sqlite3.Error, zlib.error) as error:
        print(json.dumps({'error': safe_error(error)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
