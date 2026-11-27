"""Client identity and private-copy boundaries for channel adapters.

Never infer an active login channel solely from the presence of TapTap DLLs.
Never mutate shared official-client assets or reuse a private copy from another
installation. All fingerprints are local; no paths or account material go online.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

MARKER_SCHEMA = 2
CORE_FILES = ('launcher.exe', 'GameAssembly.dll', 'launcher_Data/app.info')
# These are the only directories shared with the installation. In particular,
# yoo/DefaultPackage/ManifestFiles is copied: resource manifests are writable.
LINK_DIRECTORIES = frozenset({
    'launcher_Data/Resources',
    'launcher_Data/il2cpp_data',
    'launcher_Data/StreamingAssets/Resources',
    'launcher_Data/StreamingAssets/il2cpp_data',
    'launcher_Data/StreamingAssets/aa',
    'launcher_Data/StreamingAssets/MonoBleedingEdge',
    'launcher_Data/StreamingAssets/Packages',
    'launcher_Data/StreamingAssets/yoo',
    'launcher_Data/yoo/DefaultPackage/CacheFiles',
})
WRITABLE_NAMES = ('cache', 'Cache', 'logs', 'Cookies*', '*.log', '*_log.txt')
SHA256 = re.compile(r'[0-9a-f]{64}')
LINKED_CONTENT_HASH_LIMIT = 4096
MAX_READY_MARKER_BYTES = 16 * 1024 * 1024


def is_linked_path(name):
    return any(name == linked or name.startswith(linked + '/') for linked in LINK_DIRECTORIES)


def linked_file_digest(path):
    """Identify a linked asset without full-hashing multi-gigabyte bundles.

    Tiny assets (<= 4 KiB) still hash file bytes so rapid same-size content
    edits are detected; larger immutable bundles record a SHA-256 over their
    size and mtime_ns after verifying no reparse point is present.
    """
    path = Path(path)
    if is_reparse_point(path) or not path.is_file():
        raise ValueError('Client identity requires a regular, non-linked file: ' + str(path))
    st = os.lstat(path)
    if st.st_size <= LINKED_CONTENT_HASH_LIMIT:
        return file_digest(path)
    token = f'linked:{st.st_size}:{st.st_mtime_ns}'.encode('ascii')
    return hashlib.sha256(token).hexdigest()


def is_reparse_point(path):
    """Detect symlinks and all Windows reparse points, including junctions.

    Path.is_junction() is unavailable on the supported Python 3.10 test runtime;
    the file attributes work on every supported Windows Python version.
    """
    try:
        return bool(os.lstat(path).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except FileNotFoundError:
        return False
    except AttributeError:
        return Path(path).is_symlink()


def is_junction(path):
    if not is_reparse_point(path):
        return False
    try:
        return bool(os.lstat(path).st_reparse_tag == stat.IO_REPARSE_TAG_MOUNT_POINT)
    except AttributeError:
        return False


def is_writable_name(name):
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in WRITABLE_NAMES)


def file_digest(path):
    """Hash a regular file without following a reparse point."""
    path = Path(path)
    for attempt in range(10):
        try:
            if is_reparse_point(path) or not path.is_file():
                raise ValueError('Client identity requires a regular, non-linked file: ' + str(path))
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            if is_reparse_point(path):
                raise ValueError('Client file became linked while hashing: ' + str(path))
            return digest.hexdigest()
        except OSError as error:
            if getattr(error, 'winerror', None) not in (5, 32, 33) or attempt == 9:
                raise
            time.sleep(0.1)
    raise AssertionError('unreachable')


def _scan(client, allow_links=False, private_copy=False, reject_unknown_files=False):
    client = Path(client)
    if is_reparse_point(client) or not client.is_dir():
        raise ValueError('Client root must be a regular, non-linked directory: ' + str(client))
    files, links = {}, {}

    def visit(directory, relative, record_files=True):
        with os.scandir(directory) as entries:
            children = sorted(entries, key=lambda entry: entry.name)
        for entry in children:
            child = Path(entry.path)
            parts = relative + (entry.name,)
            name = '/'.join(parts)
            linked = is_reparse_point(child)
            # Runtime caches are never imported into a rebuild. They are still
            # inspected for links so a cache cannot become an escape hatch.
            writable = any(is_writable_name(part) for part in parts)
            if linked:
                if (allow_links and name in LINK_DIRECTORIES and is_junction(child)
                        and entry.is_dir(follow_symlinks=False)):
                    links[name] = str(child.resolve())
                else:
                    raise ValueError('Unexpected client symlink or Junction: ' + str(child))
                continue
            if writable:
                continue
            if entry.is_dir(follow_symlinks=False):
                # Unknown top-level installation folders may contain saves or
                # updater state. Mirror an empty private folder, never copy it.
                if private_copy and not relative and entry.name != 'launcher_Data':
                    visit(child, parts, record_files=False)
                    continue
                visit(child, parts)
            elif entry.is_file(follow_symlinks=False):
                if not record_files:
                    if reject_unknown_files:
                        raise ValueError('Unexpected file in private runtime directory: ' + str(child))
                    continue
                if private_copy and not relative and entry.name == 'client-ready.json':
                    continue
                files[name] = (linked_file_digest(child)
                               if not allow_links and is_linked_path(name)
                               else file_digest(child))
            else:
                raise ValueError('Unsupported client filesystem entry: ' + str(child))

    visit(client, ())
    return files, links


def scan_source(client):
    """Return immutable source hashes; reject every source reparse point."""
    return _scan(client, private_copy=True)


def fingerprint(client):
    client = Path(client).resolve()
    files, _ = scan_source(client)
    values = {'source': str(client), 'files': files}
    values['identity'] = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
    return values


def expected_copies(identity, channel, replacements=None):
    """Hashes expected in the private copy, excluding linked source trees."""
    copies = {name: digest for name, digest in identity['files'].items()
              if not is_linked_path(name)}
    replacements = replacements or {}
    for name, digest in replacements.items():
        if name not in identity['files']:
            raise ValueError('SDK replacement is not part of the verified source: ' + name)
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            raise ValueError('Invalid SDK replacement hash.')
        copies[name] = digest
    if channel == 'pcgamesdk' and 'launcher_Data/Plugins/x86_64/PCGameSDK.dll' not in replacements:
        raise ValueError('The PCGameSDK copy requires a verified local UID SDK replacement.')
    return copies


def read_ready_marker(marker):
    marker = Path(marker)
    if is_reparse_point(marker) or not marker.is_file():
        raise ValueError('Missing or linked client-ready marker.')
    if marker.name != 'client-ready.json' or marker.stat().st_size > MAX_READY_MARKER_BYTES:
        raise ValueError('Invalid client-ready marker.')
    recorded = json.loads(marker.read_text(encoding='utf-8-sig'))
    if not isinstance(recorded, dict) or recorded.get('schema') != MARKER_SCHEMA:
        raise ValueError('Legacy or unsupported client-ready marker; rebuild the private copy.')
    if recorded.get('adapter') not in ('pcgamesdk', 'quicksdk-pc'):
        raise ValueError('Unsupported client-ready adapter.')
    for field in ('files', 'copies', 'links'):
        if not isinstance(recorded.get(field), dict):
            raise ValueError('Malformed client-ready marker: ' + field)
    source = recorded.get('source')
    if not isinstance(source, str) or not Path(source).is_absolute():
        raise ValueError('Client-ready marker has no valid source identity.')
    if not isinstance(recorded.get('fingerprint'), str) or not SHA256.fullmatch(recorded['fingerprint']):
        raise ValueError('Client-ready marker has no valid source fingerprint.')
    return recorded


def make_ready_marker(source, prepared, channel, replacements=None, source_identity=None):
    """Create a schema-2 marker only after validating every staged file/link."""
    source, prepared = Path(source).resolve(), Path(prepared).resolve()
    assert_private_destination(source, prepared)
    identity = (source_identity
                if source_identity is not None and source_identity.get('source') == str(source)
                else fingerprint(source))
    files, links = _scan(prepared, allow_links=True, private_copy=True, reject_unknown_files=True)
    copies = expected_copies(identity, channel, replacements)
    if files != copies or links != {name: str((source / name).resolve()) for name in LINK_DIRECTORIES
                                    if (source / name).is_dir()}:
        raise ValueError('Prepared client files or Junction whitelist do not match the source.')
    return {
        'schema': MARKER_SCHEMA,
        'source': str(source),
        'fingerprint': identity['identity'],
        'files': identity['files'],
        'copies': copies,
        'links': links,
        'adapter': channel,
        'localUidSdk': channel == 'pcgamesdk',
    }


def cache_matches(client, marker, replacements=None, source_identity=None):
    """Validate source, marker, copied bytes and exact Junction destinations."""
    marker = Path(marker)
    try:
        recorded = read_ready_marker(marker)
        source = Path(recorded['source']).resolve()
        if source != Path(client).resolve():
            return False
        prepared = marker.parent.resolve()
        assert_private_destination(source, prepared)
        current = (source_identity
                   if source_identity is not None and source_identity.get('source') == str(source)
                   else fingerprint(source))
        expected_files = recorded['files']
        if recorded.get('fingerprint') != current['identity'] or expected_files != current['files']:
            return False
        if recorded.get('adapter') != classify(source):
            return False
        for mapping in (expected_files, recorded['copies']):
            if any(not SHA256.fullmatch(digest) for digest in mapping.values()):
                return False
        copies = expected_copies(current, recorded['adapter'], replacements)
        if recorded['copies'] != copies:
            return False
        files, links = _scan(prepared, allow_links=True, private_copy=True, reject_unknown_files=True)
        expected_links = {name: str((source / name).resolve()) for name in LINK_DIRECTORIES
                          if (source / name).is_dir()}
        if links != recorded['links'] or links != expected_links:
            return False
        return files == copies
    except (ValueError, OSError, TypeError, KeyError, json.JSONDecodeError):
        return False


def classify(client):
    client = Path(client)
    plugins = client / 'launcher_Data/Plugins'
    if is_reparse_point(plugins / 'x86_64/PCGameSDK.dll') or is_reparse_point(plugins / 'Win/PCSDK_release/PCSDKUI.exe'):
        raise ValueError('Linked channel SDK files are not trusted.')
    if (plugins / 'x86_64/PCGameSDK.dll').is_file():
        return 'pcgamesdk'
    if (plugins / 'Win/PCSDK_release/PCSDKUI.exe').is_file():
        return 'quicksdk-pc'
    if (plugins / 'x86_64/tapsdkcore.dll').is_file():
        return 'unverified-tap'
    return 'unknown'


def assert_private_destination(source, destination):
    requested = Path(destination)
    if is_reparse_point(requested):
        raise ValueError('Refusing a linked private-client destination.')
    source, destination = Path(source).resolve(), requested.resolve()
    # resolve() follows junctions: the destination must never land in official assets.
    if destination == source or source in destination.parents:
        raise ValueError('Refusing to modify the original official client through a shared path.')
    if is_reparse_point(destination):
        raise ValueError('Refusing a linked private-client destination.')
    return destination
