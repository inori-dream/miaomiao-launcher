"""Fail-closed Windows update path policy (no game, profile or runtime assets)."""
from pathlib import Path
import re
import stat

MAX_FILES = 10000
MAX_FILE_SIZE = 256 * 1024 * 1024
MAX_TOTAL_SIZE = 768 * 1024 * 1024
_RESERVED = re.compile(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', re.I)


def validate_path(name):
    if not isinstance(name, str) or not name or len(name) > 240:
        raise ValueError('invalid path')
    if '\\' in name or ':' in name or name.startswith('/'):
        raise ValueError('absolute, backslash or ADS path')
    for part in name.split('/'):
        if part in ('', '.', '..') or part.endswith((' ', '.')) or _RESERVED.match(part):
            raise ValueError('unsafe Windows path component')
        if any(ord(c) < 32 or c in '<>"|?*' for c in part):
            raise ValueError('invalid Windows path character')
    return name


def is_managed(name):
    validate_path(name)
    p = name.lower()
    parts = p.split('/')
    if any(x.endswith(('.db', '.sqlite', '.sqlite3', '.wal', '.shm', '-wal', '-shm')) for x in parts):
        return False
    if len(parts) == 1:
        if p.startswith('launcher.settings'):
            return False
        return (p.endswith('.py') or p in {
            'overfieldlauncher.exe', 'overfieldlauncher-en.exe', 'localuidsdk.dll',
            'launcher.ico', 'launcher.manifest', 'model_schema.json', 'proto_fields.json',
            'installation-manifest.json', 'readme.md', 'release-notes.md',
            'release-checklist.md', 'requirements.txt', 'world-control.ps1',
            'lan-firewall.ps1', 'make_junction.ps1'})
    if parts[0] in ('model_schema', 'proto_fields'):
        return p.endswith(('.json', '.py', '.txt'))
    if parts[0] == 'docs':
        return p.endswith(('.md', '.txt', '.json'))
    if parts[0] == 'backup-tool':
        if any(x in ('tools', 'runtime-python', 'data', 'resource', 'config', '__pycache__') for x in parts[1:]):
            return False
        return (p.endswith(('.py', '.ps1', '.cmd', '.bat')) or
                p in {'backup-tool/proto_gen/cmdid.csv', 'backup-tool/proto_gen/msg_id.txt',
                      'backup-tool/proto_gen/lolo-license.txt', 'backup-tool/proto_gen/of-ps-license.txt',
                      'backup-tool/version', 'backup-tool/license', 'backup-tool/license.txt',
                      'backup-tool/readme.md', 'backup-tool/exploration_catalog.json',
                      'backup-tool/release-notes.txt', 'backup-tool/third-party.txt',
                      'backup-tool/requirements.txt', 'backup-tool/使用说明.txt'})
    # Server, game/client, runtime and profiles intentionally never managed in v1.
    return False


def require_managed(name):
    if not is_managed(name):
        raise ValueError('protected or unapproved update path: ' + name)
    return name


def assert_no_reparse(path):
    """Check lexical path and every existing ancestor, without resolving links away."""
    p = Path(path).absolute()
    for node in (p, *p.parents):
        try:
            s = node.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(s.st_mode) or getattr(s, 'st_file_attributes', 0) & 0x400:
            raise ValueError('reparse/symlink path: ' + str(node))
    return p


def validate_files(files):
    if not isinstance(files, dict) or not files or len(files) > MAX_FILES:
        raise ValueError('invalid file count')
    seen = set()
    total = 0
    for name, meta in files.items():
        require_managed(name)
        if name.casefold() in seen:
            raise ValueError('case-colliding paths')
        seen.add(name.casefold())
        if not isinstance(meta, dict) or set(meta) != {'size', 'sha256'}:
            raise ValueError('invalid file metadata')
        size, digest = meta['size'], meta['sha256']
        if type(size) is not int or not 0 <= size <= MAX_FILE_SIZE:
            raise ValueError('invalid file size')
        if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('invalid SHA256')
        total += size
    if total > MAX_TOTAL_SIZE:
        raise ValueError('expanded size budget exceeded')
    # A file cannot also be the ancestor directory of another member.
    for name in seen:
        if any('/'.join(name.split('/')[:i]) in seen for i in range(1, len(name.split('/')))):
            raise ValueError('file/directory collision')
    return files
