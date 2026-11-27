"""Read-only integrity report for a decoded account ZIP."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from capture_import import (
    archive_sha256,
    capture_quality,
    captured_uid,
    find_capture_prefix,
    open_secure_zip,
    read_capture_archive,
    read_capture_summary,
    reject_secrets,
    validate_archive_contents,
)


def verify_backup(path: str | Path) -> dict:
    source = Path(path)
    with open_secure_zip(source) as archive:
        validate_archive_contents(archive)
        prefix = find_capture_prefix(archive)
        player, entries = read_capture_archive(archive)
        summary = read_capture_summary(archive, prefix)
    uid = captured_uid(player)
    if uid <= 0:
        raise ValueError('Capture must contain a verifiable UID.')
    reject_secrets(player)
    reject_secrets(entries)
    reject_secrets(summary)
    quality = capture_quality(player, entries, summary)
    return {
        'ok': True,
        'complete': not quality['incomplete'],
        'uid': uid,
        'internalId': player.get('player_id') or uid,
        'name': player.get('player_name', ''),
        'level': player.get('level', 1),
        'characters': len(player.get('characters') or []),
        'capturedMessages': sum(len(value) for value in entries.values()),
        'sha256': archive_sha256(source),
        'quality': quality,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path)
    args = parser.parse_args()
    try:
        report = verify_backup(args.file)
    except (OSError, ValueError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['complete'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
