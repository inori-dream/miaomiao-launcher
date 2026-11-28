"""Import one validated account ZIP without replacing any existing save.

The captured UID wins when present.  A login UID is only a fallback for a
capture that has no identity.  Dangerous archives and credential material are
always rejected; incomplete captures require an explicit opt-in.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import zipfile
from contextlib import closing
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import capture_import  # noqa: E402
from capture_zip import (  # noqa: E402
    captured_uid,
    find_capture_prefix,
    open_secure_zip,
    read_capture_entries,
    read_capture_summary,
    read_member,
    response_items,
    snapshot_archive,
    uint32,
    validate_archive,
    validate_archive_contents,
)
from privacy_check import redact_result, redact_value, safe_error  # noqa: E402


def open_account_zip(path: Path):
    """Return (archive, prefix) after bounded validation of the whole ZIP."""
    path = Path(path)
    archive = zipfile.ZipFile(path)
    try:
        validate_archive(archive, path)
        validate_archive_contents(archive)
        return archive, find_capture_prefix(archive)
    except Exception:
        archive.close()
        raise


def collect_entries(archive: zipfile.ZipFile, prefix: str) -> dict:
    return read_capture_entries(archive, prefix)


def _read_identity_archive(archive: zipfile.ZipFile):
    prefix = find_capture_prefix(archive)
    player, entries = capture_import.read_capture_archive(archive, allow_missing_uid=True)
    summary = read_capture_summary(archive, prefix)
    return player, entries, summary


def read_identity(path: Path) -> tuple[dict, dict]:
    """Read the captured main data and all decoded responses from an account ZIP."""
    with open_secure_zip(path) as archive:
        validate_archive_contents(archive)
        player, entries, _summary = _read_identity_archive(archive)
    return player, entries


def existing_uids(database: Path) -> list[int]:
    database = Path(database)
    if not database.is_file():
        return []
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)) as connection:
        return [row[0] for row in connection.execute('SELECT user_id FROM of_user ORDER BY user_id')]


def has_progress(database: Path, uid: int) -> bool:
    database = Path(database)
    if not database.is_file():
        return False
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)) as connection:
        if connection.execute('SELECT 1 FROM of_game WHERE user_id=?', (uid,)).fetchone():
            return True
        if connection.execute('SELECT 1 FROM of_game_basic WHERE user_id=?', (uid,)).fetchone():
            return True
    account = database.parent.parent / 'accounts' / str(uid)
    return account.exists() or account.is_symlink()


def describe(database: Path, uid: int) -> str:
    # Do not place another account's nickname or level into an error/log.
    return f'UID {uid}'


def plan(path: Path, database: Path, login_uid: int | None) -> dict:
    """Decide which UID to use and whether it conflicts, without writing anything."""
    database = Path(database)
    login_uid = uint32(login_uid, allow_zero=True, label='Login UID') if login_uid else None
    with open_secure_zip(path) as archive:
        validate_archive_contents(archive)
        player, entries, summary = _read_identity_archive(archive)
    uid = captured_uid(player)
    source = 'capture'
    if not uid:
        if not login_uid:
            raise ValueError('Backup has no UID; provide a login UID.')
        uid = login_uid
        source = 'login'
    conflict = has_progress(database, uid)
    return {
        'uid': uid,
        'uidSource': source,
        'name': player.get('player_name', ''),
        'level': player.get('level', 1),
        'characters': len(capture_import.as_list(player.get('characters'))),
        'capturedMessages': sum(len(value) for value in entries.values()),
        'database': str(database),
        'conflict': conflict,
        'conflictWith': describe(database, uid) if conflict else '',
        'existingUids': existing_uids(database),
        'quality': capture_import.capture_quality(player, entries, summary),
    }


def _flatten_validated_zip(source_archive: zipfile.ZipFile, prefix: str, destination: Path,
                           summary: dict) -> None:
    """Create a minimal import ZIP inside the current task directory."""
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as output:
        for item in response_items(source_archive, prefix):
            relative = item.filename[len(prefix):]
            output.writestr('export/' + relative, read_member(source_archive, item))
        if summary:
            output.writestr('summary.json', json.dumps(summary, ensure_ascii=False, allow_nan=False))


def import_account(path: Path, database: Path, login_uid: int | None, replace: bool = False,
                   allow_incomplete: bool = False, allow_overwrite: bool = False) -> dict:
    """Import from a stable task-local snapshot; explicit ``allow_overwrite`` replaces an existing UID after backup."""
    database = Path(database).resolve()
    reviewed = plan(path, database, login_uid)
    if reviewed['conflict'] and not allow_overwrite:
        raise ValueError(
            f"UID 冲突：{reviewed['conflictWith']} 已有存档。"
            '禁止覆盖已有存档（existing saves are never overwritten）。'
        )
    profile = database.parent.parent
    task = capture_import._new_task(profile)
    try:
        copied, digest = snapshot_archive(path, task)
        with open_secure_zip(copied) as archive:
            player, entries, summary = _read_identity_archive(archive)
            uid = captured_uid(player) or reviewed['uid']
            if uid != reviewed['uid']:
                raise ValueError('Account ZIP changed after the import preview.')
            prefix = find_capture_prefix(archive)
            flat = task / 'import.zip'
            _flatten_validated_zip(archive, prefix, flat, summary)
        result = capture_import.import_capture(
            flat, profile, uid, replace=False, allow_incomplete=allow_incomplete,
            _task_dir=task, _source_sha256=digest, allow_overwrite=allow_overwrite,
        )
        result.update(
            uid=uid,
            uidSource=reviewed['uidSource'],
            name=player.get('player_name', ''),
            level=player.get('level', 1),
            database=str(database),
        )
        return result
    finally:
        shutil.rmtree(task, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['plan', 'import'])
    parser.add_argument('--file', type=Path, required=True, help='account_*.zip from the backup tool')
    parser.add_argument('--database', type=Path, required=True, help='profiles/<mode>/db/lolo.db')
    parser.add_argument('--uid', type=int, default=0, help='UID to create when the capture has none')
    parser.add_argument('--replace', action='store_true', help='deprecated; existing saves are never replaced')
    parser.add_argument('--allow-overwrite', action='store_true')
    parser.add_argument('--allow-incomplete', action='store_true')
    args = parser.parse_args()
    database = args.database.resolve()
    try:
        if args.action == 'plan':
            result = plan(args.file.resolve(), database, args.uid or None)
        else:
            result = import_account(args.file.resolve(), database, args.uid or None,
                                    args.replace, args.allow_incomplete, args.allow_overwrite)
        print(json.dumps(redact_result(result), ensure_ascii=False))
        return 0
    except (ValueError, OSError, zipfile.BadZipFile, sqlite3.Error) as error:
        print(json.dumps({'error': safe_error(error)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
