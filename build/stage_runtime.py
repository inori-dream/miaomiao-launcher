"""Assemble organized edition runtimes and standalone backup-tool under dist/."""
import argparse
import json
from pathlib import Path, PurePosixPath
import shutil
import sys

if __package__:
    from .project_layout import (
        PROJECT_ROOT, RUNTIME_ROOT, english_readme,
        release_document, release_version, runtime_directory, source_file,
    )
else:
    from project_layout import (
        PROJECT_ROOT, RUNTIME_ROOT, english_readme,
        release_document, release_version, runtime_directory, source_file,
    )

sys.path.insert(0, str(RUNTIME_ROOT))

LEGACY_DOCUMENTS = {'RELEASE-NOTES.md', 'RELEASE-CHECKLIST.md'}
BLOCKED_ROOTS = {'profiles', 'runtime', 'runtime-python', 'server-kit', 'output', 'export', '.git'}
BLOCKED_SUFFIXES = {'.db', '.sqlite', '.sqlite3', '.pcap', '.pcapng', '.etl', '.bin', '.log',
                    '.exe', '.dll', '.obj', '.lib', '.exp', '.pdb', '.pem', '.key', '.pfx'}


def approved_asset(name):
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or any(part in ('..', '.', '__pycache__') for part in path.parts):
        return False
    if path.parts[0] in BLOCKED_ROOTS or path.parts[:2] == ('backup-tool', 'tools'):
        return False
    if path.name.startswith('test_') and path.suffix == '.py':
        return False
    return path.suffix.lower() not in BLOCKED_SUFFIXES and path.name != '.gitignore'


def _write_asset(path, destination, root_document=False):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == '.md':
        destination.write_bytes(release_document(path.read_bytes(), root_document=root_document))
    else:
        shutil.copy2(path, destination)


def _stage_edition(source, runtime, binaries, target_dir, binary_name, readme_bytes, allowed):
    target_dir.mkdir(parents=True, exist_ok=True)
    app_dir = target_dir / 'app'
    app_dir.mkdir(parents=True, exist_ok=True)
    copied = set()

    def copy(path, name):
        _write_asset(path, target_dir / name, root_document=False)
        copied.add(name)

    for name in allowed:
        if name == 'README.md':
            (target_dir / 'README.md').write_bytes(readme_bytes)
            copied.add('README.md')
            continue
        if name in {'docs/' + doc for doc in LEGACY_DOCUMENTS}:
            continue
        if name in LEGACY_DOCUMENTS or name.startswith('docs/'):
            path = source_file(source, name)
        else:
            path = runtime / name
        if not path.is_file():
            continue
        if name in LEGACY_DOCUMENTS:
            copy(path, 'docs/' + name)
        elif name.startswith(('docs/', 'backup-tool/')):
            copy(path, name)
        else:
            copy(path, 'app/' + name)

    launcher_binary = binaries / binary_name
    if launcher_binary.is_file():
        shutil.copy2(launcher_binary, target_dir / 'OverFieldLauncher.exe')
        copied.add('OverFieldLauncher.exe')
    sdk_binary = binaries / 'LocalUidSDK.dll'
    if sdk_binary.is_file():
        shutil.copy2(sdk_binary, app_dir / 'LocalUidSDK.dll')
        copied.add('app/LocalUidSDK.dll')
    return len(copied)


def stage_runtime(source, output, binaries=None):
    from privacy_check import RELEASE_ALLOWED

    source = Path(source).resolve()
    runtime = runtime_directory(source)
    output = Path(output).resolve()
    if output in (source, runtime) or source / 'src' in (output, *output.parents):
        raise ValueError('Staging output must be separate from the source directories.')
    if binaries is None:
        binaries = output / 'bin' if (output / 'bin').is_dir() else output
    binaries = Path(binaries).resolve()
    output.mkdir(parents=True, exist_ok=True)

    allowed = [name for name in sorted(RELEASE_ALLOWED) if approved_asset(name)]
    version = release_version(source)
    cn_readme = release_document(source_file(source, 'README.md').read_bytes())
    en_readme = english_readme(version)

    editions = {}
    for folder, binary_name, readme_bytes in (
        ('cn', 'OverFieldLauncher.exe', cn_readme),
        ('en', 'OverFieldLauncher-EN.exe', en_readme),
    ):
        editions[folder] = _stage_edition(
            source, runtime, binaries, output / folder, binary_name, readme_bytes, allowed
        )

    backup_output = output / 'backup-tool'
    backup_output.mkdir(parents=True, exist_ok=True)
    backup_files = 0
    for name in allowed:
        if not name.startswith('backup-tool/'):
            continue
        path = runtime / name
        if not path.is_file():
            continue
        _write_asset(path, output / name)
        backup_files += 1

    return {'editions': editions, 'backupToolFiles': backup_files, 'output': str(output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=PROJECT_ROOT)
    parser.add_argument('--output', type=Path, default=PROJECT_ROOT / 'dist')
    parser.add_argument('--binaries', type=Path, default=None)
    args = parser.parse_args()
    print(json.dumps(stage_runtime(args.source, args.output, args.binaries), ensure_ascii=False))


if __name__ == '__main__':
    main()
