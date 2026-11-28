"""Independent local/LAN server configuration and consistent SQLite snapshots."""
from __future__ import annotations
import argparse
import datetime as dt
import ipaddress
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from urllib.request import ProxyHandler, build_opener

_SELF_DIR = str(Path(__file__).resolve().parent)
if _SELF_DIR not in sys.path:
    sys.path.insert(0, _SELF_DIR)
import re
import subprocess
import time
import uuid
from contextlib import closing

MODES = {'solo': (18080, 18100, 18200), 'host': (19080, 19100, 19200), 'steam': (20080, 20100, 20200)}

# Each profile owns a disjoint port block, so the national and the international
# worlds can run side by side on one machine without negotiating a free port.
# The room gateway admin port sits 220 above the HTTP port (18300/19300/20300).

# Client language enum values must be verified against the actual client.
# A voice-language enum is not evidence for the UI-language numbering.
# Launcher UI language is selected at build time, independently of this setting.
LANGUAGE_DEFAULT = {}
PINNED_RESOURCE_VERSIONS = {'2026-10-21-14-05-50': '2026-10-21-14-05-50'}


def _install_root():
    runtime = Path(__file__).resolve().parent
    if runtime.name.lower() == 'app':
        return runtime.parent
    project = runtime.parent.parent
    if runtime == project / 'src/runtime' and (project / 'src/launcher').is_dir():
        return project
    return runtime


def _sdk_file():
    runtime = Path(__file__).resolve().parent
    adjacent = runtime / 'LocalUidSDK.dll'
    project = runtime.parent.parent
    if (not adjacent.is_file() and runtime == project / 'src/runtime'
            and (project / 'src/launcher').is_dir()):
        for candidate in (project / 'dist/bin/LocalUidSDK.dll',
                          project / 'dist/LocalUidSDK.dll'):
            if candidate.is_file():
                return candidate
        return project / 'dist/bin/LocalUidSDK.dll'
    return adjacent


def client_resource_version(client, program):
    import repair_version
    manifest = client / 'yoo/DefaultPackage/ManifestFiles/PackageManifest_DefaultPackage.version'
    if manifest.is_file():
        return repair_version.read_version(manifest)
    return PINNED_RESOURCE_VERSIONS.get(program)

# Channels whose client assets differ; the Steam build keeps its own copy so the
# two editions never share a writable install.
CHANNEL_SOURCES = {'steam': 'steam'}


def lan_ip(value):
    ip = ipaddress.IPv4Address(value.strip())
    if not any(ip in ipaddress.IPv4Network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')):
        raise ValueError('A private LAN IPv4 address is required.')
    return str(ip)


def write_json(path, value):
    from client_identity import is_reparse_point
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if is_reparse_point(path.parent) or is_reparse_point(path):
        raise ValueError('Refusing to write JSON through a symlink or Junction: ' + str(path))
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        _retry_file_operation(lambda: temporary.replace(path))
    finally:
        temporary.unlink(missing_ok=True)


def snapshot(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + '.tmp')
    with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)) as origin:
        with closing(sqlite3.connect(temporary)) as target:
            origin.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('SQLite snapshot validation failed.')
    temporary.replace(destination)


def locate(roots):
    """Find the client launcher and LoloServer, tolerating one extra nesting level.

    Players pick different folders or direct launcher/Steam shortcut files:
    accept folders, direct launcher.exe files, or steam:// .url shortcuts.
    """
    client = server = None
    for raw_base in roots:
        if raw_base is None:
            continue
        base = Path(raw_base)
        if base.is_file():
            suffix = base.suffix.lower()
            if suffix == '.url':
                try:
                    from official_launcher import steam_app_id, steam_install_dir
                    app_id = steam_app_id(base)
                    if app_id:
                        install = steam_install_dir(app_id)
                        if install and (install / 'launcher.exe').is_file() and client is None:
                            client = install
                except (ValueError, OSError, ImportError):
                    pass
                continue
            if suffix == '.exe':
                if base.name.lower() == 'launcher.exe' and client is None:
                    client = base.parent
                base = base.parent
        try:
            candidates = [base] + [p for p in base.iterdir() if p.is_dir()]
        except OSError:
            continue
        for folder in candidates:
            if client is None:
                if (folder / 'OverField Game' / 'launcher.exe').is_file():
                    client = folder / 'OverField Game'
                elif (folder / 'launcher.exe').is_file():
                    client = folder
            if server is None:
                if (folder / 'LoloServer' / 'Lolo.exe').is_file():
                    server = folder / 'LoloServer'
                elif (folder / 'Lolo.exe').is_file() and (folder / 'config.json').is_file():
                    server = folder
        if client is not None and server is not None:
            break
    return client, server


def apply_language(resources, language):
    """Pin the client UI language inside this profile's own resource copy.

    The value lives in InitConfigAsset.json. Rewriting only this profile's file
    keeps the shipped server-kit template untouched, so one installation can run
    a Chinese and an English world at the same time.
    """
    path = Path(resources) / 'Config' / 'InitConfigAsset.json'
    if not path.is_file():
        return None
    document = json.loads(path.read_text(encoding='utf-8-sig'))
    changed = 0
    for section in document.values():
        if isinstance(section, dict) and 'ForeceLaguageType' in section:
            if section['ForeceLaguageType'] != language:
                section['ForeceLaguageType'] = language
                changed += 1
    if changed:
        write_json(path, document)
    return {'path': str(path), 'language': language, 'sections': changed}


def prepare(root, base, mode, host):
    http, gate, log = MODES[mode]
    address = lan_ip(host) if mode == 'host' else '127.0.0.1'
    profile = base / mode
    profile.mkdir(parents=True, exist_ok=True)
    (profile / 'logs').mkdir(exist_ok=True)
    result = {'mode': mode, 'profile': str(profile), 'host': address, 'http': http, 'gateway': gate, 'log': log, 'imported': False}
    install = _install_root()
    located_client, located_server = locate([root, install])
    server = located_server or (root / 'LoloServer')
    if not (server / 'Lolo.exe').is_file():
        server = install / 'server-kit'
    if not (server / 'Lolo.exe').is_file():
        raise FileNotFoundError('LoloServer/Lolo.exe is missing. Put the server files next to the launcher or select the folder that contains LoloServer.')
    data = profile / 'data'
    if not data.exists():
        shutil.copytree(server / 'data', data)
    resources = profile / 'Resource'
    if not resources.exists():
        shutil.copytree(server / 'Resource', resources)
    language = LANGUAGE_DEFAULT.get(mode)
    if language is not None:
        result['language'] = apply_language(resources, language)
    executable = profile / 'server/Lolo.exe'
    executable.parent.mkdir(exist_ok=True)
    if not executable.exists(): shutil.copy2(server / 'Lolo.exe', executable)
    # Repair only this profile's version map; original installation is read-only.
    import repair_version
    from client_identity import is_reparse_point
    original_client = located_client or root
    if original_client is not None:
        client = original_client / 'launcher_Data'
        program_manifest = client / 'StreamingAssets/yoo/DefaultPackage/PackageManifest_DefaultPackage.version'
        resource_manifest = client / 'yoo/DefaultPackage/ManifestFiles/PackageManifest_DefaultPackage.version'
        if program_manifest.exists() or is_reparse_point(program_manifest):
            if is_reparse_point(data) or is_reparse_point(client):
                raise ValueError('Version mapping requires regular, non-linked client/profile directories.')
            program = repair_version.read_version(program_manifest)
            # Bundled/offline installs may ship only the program manifest. In that
            # case the program timestamp is the resource version, not a pinned or
            # stale value from another build.
            if resource_manifest.is_file() or is_reparse_point(resource_manifest):
                resource = repair_version.read_version(resource_manifest)
            else:
                resource = program
            mapping_path = data / 'ClientVersion.json'
            if is_reparse_point(mapping_path):
                raise ValueError('Refusing a linked profile ClientVersion.json.')
            mapping = json.loads(mapping_path.read_text(encoding='utf-8-sig'))
            if not isinstance(mapping, dict) or not isinstance(mapping.get('VersionMap'), dict):
                raise ValueError('Profile ClientVersion.json has no valid VersionMap.')
            mapping['VersionMap'][program] = program + '_' + resource
            write_json(mapping_path, mapping)
    database = profile / 'db/lolo.db'
    database.parent.mkdir(exist_ok=True)
    metadata = profile / 'profile.json'
    # Public builds never inherit accounts from an installation/template database.
    # Keep an existing private profile unchanged; import player progress only explicitly.
    if not database.exists():
        from capture_import import schema_database
        schema_database(server / 'db/lolo.db', database)
    if not metadata.exists():
        write_json(metadata, {'mode': mode, 'createdAt': dt.datetime.now().isoformat(), 'schemaOnly': True})
    config = json.loads((server / 'config.json').read_text(encoding='utf-8-sig'))
    config['Resources'] = {'ResourcePath': str(resources), 'DataPath': str(data)}
    config['HttpNet'].update(InnerIp=address, InnerPort=str(http), Tls=False, HttpsPort=str(http + 3), CertPath=str(data / 'cert.pem'), KeyPath=str(data / 'key.pem'))
    config['GateWay'].update(OuterIp=address, OuterPort=gate, OuterAddr=f'127.0.0.1:{gate+1}', CheckToken=False, CheckUrl=f'http://{address}:{http}/gucooing/lolo/checkSdkToken')
    config['LogServer'].update(OuterIp=address, OuterPort=log, OuterAddr=f'{address}:{log}')
    config['DB'].update(dbType='sqlite', dsn=str(database) + '?_pragma=busy_timeout(10000)&_pragma=journal_mode(WAL)&_pragma=synchronous(FULL)', readDsn=[])
    write_json(profile / 'config.json', config)
    result['config'] = str(profile / 'config.json')
    result['database'] = str(database)
    result['executable'] = str(executable)
    return result


def _same_file_hash(first, second):
    from client_identity import file_digest
    try:
        return file_digest(first) == file_digest(second)
    except (ValueError, OSError):
        return False


def _retry_file_operation(operation):
    """Retry only bounded Windows sharing/access races, never arbitrary I/O."""
    for attempt in range(50):
        try:
            return operation()
        except OSError as error:
            if getattr(error, 'winerror', None) not in (5, 32, 33) or attempt == 49:
                raise
            time.sleep(0.1)


def _copy_file(source, destination):
    return _retry_file_operation(lambda: shutil.copy2(source, destination))


def _rename_directory(source, destination):
    _retry_file_operation(lambda: source.rename(destination))


def _remove_staging(path):
    from client_identity import is_reparse_point
    path = Path(path)
    if is_reparse_point(path):
        try:
            path.rmdir()
        except OSError:
            path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _recover_client_swap(profile):
    from client_identity import is_reparse_point
    profile = Path(profile).resolve()
    marker = profile / 'client-swap.json'
    if not marker.exists() and not is_reparse_point(marker):
        return
    if is_reparse_point(marker) or not marker.is_file() or marker.stat().st_size > 1024 * 1024:
        raise ValueError('Invalid or linked client swap marker; refusing automatic recovery.')
    state = json.loads(marker.read_text(encoding='utf-8'))
    if not isinstance(state, dict):
        raise ValueError('Malformed client swap marker.')
    target = profile / 'client'
    if state.get('target') != str(target):
        raise ValueError('Client swap marker points outside this profile.')
    backups = profile / 'backups'
    if is_reparse_point(backups):
        raise ValueError('Client swap backup directory is linked.')
    previous_text = state.get('previous')
    staging_text = state.get('staging')
    previous = Path(previous_text) if previous_text else None
    staging = Path(staging_text) if staging_text else None
    if previous is not None:
        if not previous.is_absolute() or previous.parent != backups or previous.name == '..':
            raise ValueError('Client swap backup points outside this profile.')
        if is_reparse_point(previous):
            raise ValueError('Client swap backup is linked.')
    if staging is not None:
        import re
        if (not staging.is_absolute() or staging.parent != profile
                or not re.fullmatch(r'\.client-staging-[A-Za-z0-9-]+', staging.name)):
            raise ValueError('Client staging path points outside this profile.')
        if is_reparse_point(staging):
            raise ValueError('Client staging path is linked.')
    if is_reparse_point(target):
        raise ValueError('Client swap target is linked.')
    if previous is not None and previous.is_dir():
        if not target.exists():
            _rename_directory(previous, target)
        elif target.is_dir():
            # A marker left after publication means the swap was not confirmed.
            # Roll back a fully present new target as well as a missing target.
            import uuid
            recovery = profile / ('.client-recovery-' + uuid.uuid4().hex)
            _rename_directory(target, recovery)
            try:
                _rename_directory(previous, target)
            except Exception:
                _rename_directory(recovery, target)
                raise
            _remove_staging(recovery)
        else:
            raise ValueError('Client swap target is not a directory.')
    if staging is not None and (staging.exists() or is_reparse_point(staging)):
        _remove_staging(staging)
    marker.unlink()


def _copy_client_tree(source, destination, relative_parts=(), link_directories=(), link_fn=None):
    """Copy without following links or importing any known runtime cache."""
    from client_identity import is_reparse_point, is_writable_name
    source, destination = Path(source), Path(destination)
    if is_reparse_point(source) or not source.is_dir():
        raise ValueError('Unsafe client source directory: ' + str(source))
    destination.mkdir(exist_ok=True)
    if is_writable_name(source.name):
        return
    with os.scandir(source) as entries:
        children = sorted(entries, key=lambda entry: entry.name)
    for entry in children:
        child, target = Path(entry.path), destination / entry.name
        parts = relative_parts + (entry.name,)
        relative = '/'.join(parts)
        if is_reparse_point(child):
            raise ValueError('Refusing to copy a linked client entry: ' + str(child))
        if is_writable_name(entry.name):
            target.mkdir(exist_ok=True)
        elif entry.is_dir(follow_symlinks=False):
            if link_fn is not None and relative in link_directories:
                link_fn(target, child)
            else:
                _copy_client_tree(child, target, parts, link_directories, link_fn)
        elif entry.is_file(follow_symlinks=False):
            _copy_file(child, target)
        else:
            raise ValueError('Unsupported client filesystem entry: ' + str(child))


def _purge_runtime_caches(target):
    """Discard untrusted runtime cache/log state without following any links."""
    from client_identity import is_reparse_point, is_writable_name

    def clean(directory):
        with os.scandir(directory) as entries:
            children = list(entries)
        for entry in children:
            path = Path(entry.path)
            if is_writable_name(entry.name):
                _remove_staging(path)
            elif entry.is_dir(follow_symlinks=False) and not is_reparse_point(path):
                clean(path)
    clean(Path(target))


def prepare_client(root, base, mode):
    if sys.version_info < (3, 12):
        raise RuntimeError('Client preparation requires Python 3.12 or later.')
    if mode not in MODES:
        raise ValueError('Unknown client profile mode.')
    original, _ = locate([root])
    if original is None:
        raise FileNotFoundError('Game client not found. Select the folder that contains OverField Game or launcher.exe.')
    from client_identity import (CORE_FILES, LINK_DIRECTORIES, assert_private_destination,
                                 cache_matches, classify, file_digest, fingerprint,
                                 is_junction, is_reparse_point)
    profile = Path(base) / mode
    if is_reparse_point(profile):
        raise ValueError('Client profile directory must not be a symlink or Junction.')
    target = profile / 'client'
    _recover_client_swap(profile)
    assert_private_destination(original, target)
    channel = classify(original)
    if channel not in ('pcgamesdk', 'quicksdk-pc', 'steam-quicksdk'):
        raise ValueError('Unsupported or unverified game channel; refusing to prepare a private client.')
    source_identity = fingerprint(original)
    if any(name not in source_identity['files'] for name in CORE_FILES):
        raise OSError('Selected game client is incomplete: ' + ', '.join(
            name for name in CORE_FILES if name not in source_identity['files']))
    replacements = {}
    if channel == 'pcgamesdk':
        sdk = _sdk_file()
        replacements['launcher_Data/Plugins/x86_64/PCGameSDK.dll'] = file_digest(sdk)
    marker = target / 'client-ready.json'
    if cache_matches(original, marker, replacements, source_identity=source_identity):
        _purge_runtime_caches(target)
        return {'client': str(target / 'launcher.exe'), 'adapter': channel}
    return _rebuild_client(original, target, profile, mode, channel, source_identity, replacements)


def _create_junctions(pairs):
    """Create one or more NTFS directory junctions without spawning a shell per link."""
    if not pairs:
        return
    script = str(Path(__file__).with_name('make_junction.ps1'))
    if len(pairs) == 1:
        destination, source = pairs[0]
        result = subprocess.run(
            ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
             script, '-Destination', str(destination), '-Source', str(source)],
            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        failed_source = source if result.returncode else None
    else:
        payload = json.dumps([{'d': str(dest), 's': str(src)} for dest, src in pairs], ensure_ascii=False)
        env = dict(os.environ, OVERFIELD_JUNCTIONS=payload)
        command = (
            "$ErrorActionPreference='Stop'; "
            "foreach ($item in ($env:OVERFIELD_JUNCTIONS | ConvertFrom-Json)) { "
            "New-Item -ItemType Junction -Path $item.d -Target $item.s | Out-Null }"
        )
        result = subprocess.run(
            ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', command],
            capture_output=True, env=env, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        failed_source = pairs[0][1] if result.returncode else None
    if failed_source is not None:
        raise OSError('Could not link immutable client assets: ' + str(failed_source))


def _rebuild_client(original, target, profile, mode, channel, source_identity, replacements):
    from client_identity import (LINK_DIRECTORIES, cache_matches, fingerprint,
                                 is_junction, is_reparse_point, is_writable_name, make_ready_marker)
    original, target, profile = Path(original).resolve(), Path(target), Path(profile).resolve()
    backups = profile / 'backups'
    backups.mkdir(parents=True, exist_ok=True)
    if is_reparse_point(backups):
        raise ValueError('Client backup directory must not be linked.')
    staging = profile / f'.client-staging-{uuid.uuid4().hex}'
    previous = None
    created = False
    pending_junctions = []

    def junction(destination, source):
        source = Path(source).resolve()
        if is_reparse_point(source):
            raise OSError('Refusing to link a reparse-point source: ' + str(source))
        if destination.exists() or is_reparse_point(destination):
            if not is_junction(destination) or destination.resolve() != source:
                raise OSError('Unsafe or mismatched client asset link: ' + str(destination))
            return
        pending_junctions.append((destination, source))

    try:
        staging.mkdir(parents=True)
        created = True
        for entry in original.iterdir():
            destination = staging / entry.name
            if is_reparse_point(entry):
                raise ValueError('Refusing to copy a linked client entry: ' + str(entry))
            if entry.is_file():
                if not is_writable_name(entry.name):
                    _copy_file(entry, destination)
            elif entry.name == 'launcher_Data':
                destination.mkdir()
                for child in entry.iterdir():
                    output = destination / child.name
                    if is_reparse_point(child):
                        raise ValueError('Refusing to copy a linked client entry: ' + str(child))
                    if child.is_file():
                        if not is_writable_name(child.name):
                            _copy_file(child, output)
                    elif child.name == 'Quicksdk':
                        output.mkdir(exist_ok=True)
                    elif child.name == 'StreamingAssets':
                        output.mkdir(exist_ok=True)
                        for asset in child.iterdir():
                            asset_output = output / asset.name
                            if is_reparse_point(asset):
                                raise ValueError('Refusing to copy a linked client entry: ' + str(asset))
                            if asset.is_file():
                                if not is_writable_name(asset.name):
                                    _copy_file(asset, asset_output)
                            elif is_writable_name(asset.name):
                                asset_output.mkdir(exist_ok=True)
                            else:
                                relative = 'launcher_Data/StreamingAssets/' + asset.name
                                if relative in LINK_DIRECTORIES:
                                    junction(asset_output, asset)
                                else:
                                    _copy_client_tree(asset, asset_output)
                    else:
                        relative = 'launcher_Data/' + child.name
                        if relative in LINK_DIRECTORIES:
                            junction(output, child)
                        else:
                            _copy_client_tree(
                                child, output,
                                relative_parts=('launcher_Data', child.name),
                                link_directories=LINK_DIRECTORIES,
                                link_fn=junction,
                            )
            elif entry.is_dir():
                # Unknown root folders are private writable/runtime state.
                destination.mkdir()
        _create_junctions(pending_junctions)
        for destination, source in pending_junctions:
            if not is_junction(destination) or destination.resolve() != source:
                raise OSError('Could not link immutable client assets: ' + str(source))
        if channel == 'pcgamesdk':
            _copy_file(_sdk_file(),
                       staging / 'launcher_Data/Plugins/x86_64/PCGameSDK.dll')
        pre_swap_identity = fingerprint(original)
        if pre_swap_identity['identity'] != source_identity['identity']:
            raise OSError('The selected installation changed while its private copy was prepared.')
        marker = staging / 'client-ready.json'
        write_json(marker, make_ready_marker(original, staging, channel, replacements, source_identity=pre_swap_identity))
        required = ('launcher.exe', 'GameAssembly.dll', 'launcher_Data/app.info', 'client-ready.json')
        if any(not (staging / name).is_file() or is_reparse_point(staging / name) for name in required):
            raise OSError('Prepared client is incomplete.')
        if target.exists() or is_reparse_point(target):
            if is_reparse_point(target) or not target.is_dir():
                raise OSError('Refusing to replace a linked or non-directory client target.')
            previous = backups / ('client-before-source-change-' + dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
            write_json(profile / 'client-swap.json', {
                'target': str(target), 'previous': str(previous), 'staging': str(staging),
            })
            _rename_directory(target, previous)
        try:
            _rename_directory(staging, target)
            # Catch a source update during the publication window. The previous
            # copy remains available until validation after the rename succeeds.
            post_swap_identity = fingerprint(original)
            if post_swap_identity['identity'] != source_identity['identity']:
                raise OSError('The selected installation changed while publishing the private copy.')
            if not cache_matches(original, target / 'client-ready.json', replacements, source_identity=post_swap_identity):
                raise OSError('Published private client failed cache validation.')
            (profile / 'client-swap.json').unlink(missing_ok=True)
        except Exception:
            if previous is not None and previous.exists():
                if target.exists():
                    _rename_directory(target, staging)
                if not target.exists():
                    _rename_directory(previous, target)
            (profile / 'client-swap.json').unlink(missing_ok=True)
            raise
        return {'client': str(target / 'launcher.exe'), 'adapter': channel}
    except Exception:
        if (profile / 'client-swap.json').exists():
            _recover_client_swap(profile)
        if created and (staging.exists() or is_reparse_point(staging)):
            _remove_staging(staging)
        raise


def previous_route(settings):
    enabled = settings.get('ProxyEnable', {}).get('Value') == 1
    server = settings.get('ProxyServer', {}).get('Value') or ''
    auto = settings.get('AutoConfigURL', {}).get('Value') or ''
    if auto:
        with build_opener(ProxyHandler({})).open(auto, timeout=6) as response:
            old = response.read(2_000_000).decode('utf-8-sig')
        if not re.search(r'function\s+FindProxyForURL\s*\(', old):
            raise ValueError('The existing PAC script does not contain FindProxyForURL.')
        old = re.sub(r'\bFindProxyForURL\b', 'LoloPreviousProxyForURL', old)
        return old, 'LoloPreviousProxyForURL(url, host)'
    if not enabled or not server:
        return '', '"DIRECT"'
    entries = {}
    if '=' in server:
        for entry in server.split(';'):
            if '=' in entry:
                protocol, value = entry.split('=', 1)
                entries[protocol.lower()] = value
    else:
        entries = {'http': server, 'https': server}
    lines = ['function LoloPreviousProxyForURL(url, host) {']
    override = settings.get('ProxyOverride', {}).get('Value') or ''
    for item in override.split(';'):
        if not item or item == '<-loopback>':
            continue
        if item == '<local>':
            lines.append('if (isPlainHostName(host)) return "DIRECT";')
        else:
            lines.append('if (shExpMatch(host, ' + json.dumps(item) + ')) return "DIRECT";')
    for protocol, value in entries.items():
        if protocol in ('http', 'https', 'ftp'):
            lines.append('if (url.indexOf(' + json.dumps(protocol + ':') + ') === 0) return ' + json.dumps('PROXY ' + value) + ';')
    lines.append('return "DIRECT"; }')
    return '\n'.join(lines), 'LoloPreviousProxyForURL(url, host)'


def pac_text(settings, proxy_port):
    old, fallback = previous_route(settings)
    return old + '\nfunction FindProxyForURL(url, host) {\n' + r'''
host = host.toLowerCase().replace(/\.$/, "");
var domain = /^(?:sdkapi[^.]*|dsp-[^.]*|api-callback[^.]*)\.inutan\.com$/.test(host);
var fallbackIp = (host === "14.103.225.205" || host === "14.103.224.57") && /^http:\/\/[^/]+:18881\/dispatch\//.test(url);
''' + f'if (domain || fallbackIp) return "PROXY 127.0.0.1:{proxy_port}";\nreturn {fallback};\n}}\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'pac', 'backup', 'client'])
    parser.add_argument('--root', type=Path)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--mode', choices=MODES, default='solo')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--before', type=Path)
    parser.add_argument('--proxy-port', type=int, default=18890)
    # Explicit language wins over the per-mode default, so a user can run an
    # English world on either channel.
    parser.add_argument('--language', type=int, choices=[1, 2, 3, 4, 5])
    args = parser.parse_args()
    if args.action == 'prepare':
        if args.language is not None:
            LANGUAGE_DEFAULT[args.mode] = args.language
        result = prepare(args.root, args.base, args.mode, args.host)
    elif args.action == 'client':
        result = prepare_client(args.root,args.base,args.mode)
    elif args.action == 'pac':
        settings = json.loads(args.before.read_text(encoding='utf-8-sig'))
        target = args.base / args.mode / 'game.pac'
        target.write_text(pac_text(settings, args.proxy_port), encoding='utf-8')
        result = {'pac': str(target)}
    else:
        database = args.base / args.mode / 'db/lolo.db'
        target = args.base / args.mode / 'backups' / f'lolo-{dt.datetime.now():%Y%m%d-%H%M%S-%f}.db'
        snapshot(database, target)
        result = {'backup': str(target)}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
