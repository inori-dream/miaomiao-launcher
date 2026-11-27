"""Map the installed OverField build to its locally installed resource manifest."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import shutil
import stat
import uuid

DEFAULT_ROOT = pathlib.Path.cwd()
STAMP = re.compile(r"\d{4}(?:-\d{2}){5}")


def read_version(path: pathlib.Path) -> str:
    path = pathlib.Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError(f"Version manifest must be a regular non-linked file: {path}")
    if info.st_size > 128:
        raise ValueError(f"Version manifest is unexpectedly large: {path}")
    value = path.read_text(encoding="utf-8-sig").strip()
    if not STAMP.fullmatch(value):
        raise ValueError(f"Unexpected version in {path}: {value!r}")
    # A timestamp-shaped string is not sufficient: reject impossible dates.
    try:
        dt.datetime.strptime(value, '%Y-%m-%d-%H-%M-%S')
    except ValueError as error:
        raise ValueError(f"Invalid version timestamp in {path}: {value!r}") from error
    return value


def _linked(path: pathlib.Path) -> bool:
    try:
        return bool(path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except FileNotFoundError:
        return False


def repair(root: pathlib.Path, apply: bool) -> dict:
    client = root / "OverField Game" / "launcher_Data"
    program = read_version(client / "StreamingAssets/yoo/DefaultPackage/PackageManifest_DefaultPackage.version")
    resource = read_version(client / "yoo/DefaultPackage/ManifestFiles/PackageManifest_DefaultPackage.version")
    target = root / "LoloServer/data/ClientVersion.json"
    if _linked(target) or _linked(target.parent) or not target.is_file():
        raise ValueError(f"Version map must be a regular non-linked file: {target}")
    if target.stat().st_size > 16 * 1024 * 1024:
        raise ValueError(f"Version map is unexpectedly large: {target}")
    data = json.loads(target.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or not isinstance(data.get("VersionMap"), dict):
        raise ValueError("ClientVersion.json has no valid VersionMap.")
    mapping = data["VersionMap"]
    desired = f"{program}_{resource}"
    result = {"file": str(target), "program": program, "resource": resource,
              "previous": mapping.get(program), "desired": desired, "applied": False}
    if apply and mapping.get(program) != desired:
        backup = root / "LoloServer/backup" / f"ClientVersion-{dt.datetime.now():%Y%m%d-%H%M%S-%f}-{uuid.uuid4().hex}.json"
        backup.parent.mkdir(parents=True, exist_ok=True)
        if _linked(backup.parent):
            raise ValueError("Refusing a linked version-map backup directory.")
        shutil.copy2(target, backup)
        mapping[program] = desired
        temporary = target.with_name(target.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('x', encoding='utf-8') as stream:
                stream.write(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
                stream.flush()
                import os
                os.fsync(stream.fileno())
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        result.update(applied=True, backup=str(backup))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=pathlib.Path, default=DEFAULT_ROOT)
    parser.add_argument("--apply", action="store_true", help="Back up and update the version map")
    args = parser.parse_args()
    print(json.dumps(repair(args.root, args.apply), ensure_ascii=False, indent=2))
