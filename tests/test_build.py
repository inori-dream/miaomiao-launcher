"""Build-contract tests and release-artifact verification.

The verifier only exercises the launcher's UI-preview path and native SDK
callbacks. It never starts a game, a service, capture, or a network connection.
"""
from build.project_layout import source_file
import argparse
import base64
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest

import privacy_check

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = source_file(ROOT, 'release-build.json')
CONFIG = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
ARTIFACT_NAMES = [item['file'] for item in CONFIG['artifacts'].values()] + [CONFIG['sdk']['file']]


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def write_hashes(directory):
    directory = Path(directory)
    lines = [f'{sha256(directory / name)}  {name}' for name in ARTIFACT_NAMES]
    target = directory / 'SHA256SUMS.txt'
    content = '\n'.join(lines) + '\n'
    # Virus scanners can briefly hold a freshly created file open.
    for attempt in range(5):
        try:
            target.write_text(content, encoding='ascii')
            return target
        except PermissionError:
            if attempt == 4:
                raise
            try:
                target.unlink()
            except OSError:
                pass
            time.sleep(1)
    return target


def pe_info(path):
    data = Path(path).read_bytes()
    if len(data) < 64 or data[:2] != b'MZ':
        raise ValueError('Not a PE image')
    pe = struct.unpack_from('<I', data, 0x3C)[0]
    if data[pe:pe + 4] != b'PE\0\0':
        raise ValueError('Invalid PE signature')
    machine = struct.unpack_from('<H', data, pe + 4)[0]
    count = struct.unpack_from('<H', data, pe + 6)[0]
    optional_size = struct.unpack_from('<H', data, pe + 20)[0]
    optional = pe + 24
    magic = struct.unpack_from('<H', data, optional)[0]
    directory = optional + (96 if magic == 0x10B else 112)
    sections = []
    for index in range(count):
        base = optional + optional_size + index * 40
        virtual_size, address, raw_size, raw = struct.unpack_from('<IIII', data, base + 8)
        sections.append((address, max(virtual_size, raw_size), raw))

    def offset(rva):
        for address, span, raw in sections:
            if address <= rva < address + span:
                return raw + rva - address
        raise ValueError(f'Unmapped RVA {rva}')

    timestamp = struct.unpack_from('<I', data, pe + 8)[0]
    subsystem = struct.unpack_from('<H', data, optional + 68)[0]
    result = {'machine': hex(machine), 'timestamp': timestamp, 'subsystem': subsystem, 'sections': sections}
    cli_rva, cli_size = struct.unpack_from('<II', data, directory + 14 * 8)
    if cli_rva:
        cli = offset(cli_rva)
        metadata_rva = struct.unpack_from('<I', data, cli + 8)[0]
        metadata = offset(metadata_rva)
        if data[metadata:metadata + 4] != b'BSJB':
            raise ValueError('Invalid CLR metadata')
        result['cli'] = {'offset': cli, 'size': cli_size, 'metadata': metadata, 'flags': struct.unpack_from('<I', data, cli + 16)[0]}
    export_rva, export_size = struct.unpack_from('<II', data, directory)
    if export_rva:
        export = offset(export_rva)
        names_rva = struct.unpack_from('<I', data, export + 32)[0]
        name_count = struct.unpack_from('<I', data, export + 24)[0]
        names = offset(names_rva)

        def c_string(rva):
            start = offset(rva)
            end = data.index(0, start)
            return data[start:end].decode('ascii')

        result['exports'] = sorted(c_string(struct.unpack_from('<I', data, names + index * 4)[0]) for index in range(name_count))
        result['exportSize'] = export_size
    return result


def powershell_json(script, environment=None):
    encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
    result = subprocess.run(
        ['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60,
        env=environment,
    )
    if result.returncode:
        raise RuntimeError(privacy_check.safe_error(result.stderr or result.stdout))
    text = result.stdout.strip().lstrip('\ufeff')
    for index, character in enumerate(text):
        if character not in '[{':
            continue
        try:
            return json.loads(text[index:])
        except json.JSONDecodeError:
            continue
    raise RuntimeError('PowerShell returned no JSON value')


def inspect_edition(path):
    environment = dict(os.environ, MIAOMIAO_ASSEMBLY=str(Path(path).resolve()))
    script = r'''
$ErrorActionPreference = 'Stop'
$assembly = [Reflection.Assembly]::LoadFrom($env:MIAOMIAO_ASSEMBLY)
$type = $assembly.GetType('OverFieldLocal.Loc')
$backendType = $assembly.GetType('OverFieldLocal.Backend')
$backend = [Activator]::CreateInstance($backendType)
$flags = [Reflection.BindingFlags]'Public,NonPublic,Instance,Static'
$english = [bool]$type.GetField('English', $flags).GetValue($null)
function AttributeValue($type) {
    $attribute = $type.GetCustomAttributes($false) | Where-Object { $_.GetType().Name -eq 'AssemblyFileVersionAttribute' } | Select-Object -First 1
    return $attribute.Version
}
[ordered]@{
    english = $english
    title = $type.GetMethod('S').Invoke($null, @('开放空间 · 本地启动器'))
    start = $type.GetMethod('S').Invoke($null, @('启动游戏  →'))
    defaultMode = [string]$backendType.GetField('Mode', $flags).GetValue($backend)
    settingsFile = [string]$backendType.GetProperty('SettingsName').GetValue($null)
    assemblyTitle = $assembly.GetCustomAttributes($false) | Where-Object { $_.GetType().Name -eq 'AssemblyTitleAttribute' } | Select-Object -First 1 -ExpandProperty Title
    product = $assembly.GetCustomAttributes($false) | Where-Object { $_.GetType().Name -eq 'AssemblyProductAttribute' } | Select-Object -First 1 -ExpandProperty Product
    fileVersion = AttributeValue $assembly
    informationalVersion = $assembly.GetCustomAttributes($false) | Where-Object { $_.GetType().Name -eq 'AssemblyInformationalVersionAttribute' } | Select-Object -First 1 -ExpandProperty InformationalVersion
    assemblyName = $assembly.GetName().Name
} | ConvertTo-Json -Compress
'''
    return powershell_json(script, environment)


def signature_info(paths):
    literal = ','.join("'" + str(path).replace("'", "''") + "'" for path in paths)
    script = f'''
$ErrorActionPreference = 'Stop'
# A PowerShell 7 parent can leave incompatible modules in PSModulePath.
Import-Module (Join-Path $PSHOME 'Modules\\Microsoft.PowerShell.Security\\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
$result = @(Get-AuthenticodeSignature {literal} | ForEach-Object {{
    [ordered]@{{ path=$_.Path; status=[string]$_.Status; message=$_.StatusMessage; signer=if ($_.SignerCertificate) {{ $_.SignerCertificate.Subject }} else {{ '' }} }}
}})
$result | ConvertTo-Json -Compress
'''
    payload = powershell_json(script)
    return payload if isinstance(payload, list) else [payload]


def defender_status():
    script = r'''
try {
    $status = Get-MpComputerStatus -ErrorAction Stop
    [ordered]@{
        available=$true
        runningMode=[string]$status.AMRunningMode
        realTimeProtectionEnabled=[bool]$status.RealTimeProtectionEnabled
        antivirusEnabled=[bool]$status.AntivirusEnabled
        defenderSignaturesOutOfDate=[bool]$status.DefenderSignaturesOutOfDate
        note='No custom malware scan was run when real-time protection was disabled.'
    } | ConvertTo-Json -Compress
} catch {
    [ordered]@{ available=$false; error=(Get-MpComputerStatus -ErrorAction SilentlyContinue); note='Defender status unavailable; no malware-scan claim is made.' } | ConvertTo-Json -Compress
}
'''
    return powershell_json(script)


def sdk_callback_test(path):
    path = Path(path).resolve()
    code = r'''
import ctypes, json, os, sys
library = ctypes.CDLL(sys.argv[1])
callback_type = ctypes.CFUNCTYPE(None, ctypes.c_char_p, ctypes.c_int)
messages = []
callback = callback_type(lambda data, length: messages.append(json.loads(ctypes.string_at(data, length))))
library.SDKInit.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, callback_type]
library.SDKLogin.argtypes = [callback_type]
init = library.SDKInit(b'{}', None, 0, 1, callback)
login = library.SDKLogin(callback)
print(json.dumps({'init': init, 'login': login, 'messages': messages}))
'''
    environment = dict(os.environ, LOLO_SDK_UID='1000000')
    result = subprocess.run([sys.executable, '-c', code, str(path)], capture_output=True, text=True, encoding='utf-8', env=environment, timeout=30, check=True)
    valid = json.loads(result.stdout)
    if valid['init'] != 0 or valid['login'] != 0 or valid['messages'][-1]['data']['uid'] != '1000000':
        raise ValueError('SDK callback contract failed')
    invalid_code = r'''
import ctypes, json, os, sys
library = ctypes.CDLL(sys.argv[1])
callback_type = ctypes.CFUNCTYPE(None, ctypes.c_char_p, ctypes.c_int)
messages = []
callback = callback_type(lambda data, length: messages.append(json.loads(ctypes.string_at(data, length))))
library.SDKLogin.argtypes = [callback_type]
print(json.dumps({'code': library.SDKLogin(callback), 'messages': messages}))
'''
    rejected = []
    for value in ('', '0', '-1', 'abc', ' 1000000', '4294967296'):
        environment['LOLO_SDK_UID'] = value
        result = subprocess.run([sys.executable, '-c', invalid_code, str(path)], capture_output=True, text=True, encoding='utf-8', env=environment, timeout=30, check=True)
        payload = json.loads(result.stdout)
        if payload != {'code': -1, 'messages': []}:
            raise ValueError('SDK accepted an invalid UID')
        rejected.append(value)
    environment['LOLO_SDK_UID'] = '4294967295'
    result = subprocess.run([sys.executable, '-c', invalid_code, str(path)], capture_output=True, text=True, encoding='utf-8', env=environment, timeout=30, check=True)
    if json.loads(result.stdout)['messages'][-1]['data']['uid'] != '4294967295':
        raise ValueError('SDK rejected UINT32_MAX')
    return {'passed': True, 'validUid': '1000000', 'rejectedValues': rejected, 'maximumUid': '4294967295'}


def clean_start_test(edition, executable, directory, logs):
    edition_dir = Path(directory) / edition
    edition_dir.mkdir(parents=True)
    for name in [Path(executable).name, CONFIG['sdk']['file']] + CONFIG['cleanStartAssets']:
        source = Path(executable).parent / name if name != Path(executable).name else Path(executable)
        if name in CONFIG['cleanStartAssets']:
            source = source_file(ROOT, name)
        shutil.copy2(source, edition_dir / name)
    expected_files = sorted(path.name for path in edition_dir.iterdir())
    runs = []
    with tempfile.TemporaryDirectory(prefix=f'launcher-{edition}-preview-', dir=logs) as preview:
        for size in CONFIG['smokeTest']['sizes']:
            result = subprocess.run(
                [str(edition_dir / Path(executable).name), CONFIG['smokeTest']['argument'], preview,
                 '--ui-size', size, '--ui-mode', CONFIG['artifacts'][edition]['defaultMode']],
                cwd=edition_dir, capture_output=True, text=True, encoding='utf-8', errors='replace',
                timeout=CONFIG['smokeTest']['timeoutSeconds'],
            )
            stem = f"{CONFIG['artifacts'][edition]['defaultMode']}-{size}"
            image = Path(preview) / (stem + '.png')
            audit = Path(preview) / (stem + '.json')
            if result.returncode != 0 or not image.is_file() or not audit.is_file():
                raise RuntimeError(privacy_check.safe_error(f'UI preview failed: {result.returncode}: {result.stderr or result.stdout}'))
            layout = json.loads(audit.read_text(encoding='utf-8-sig'))
            if layout['mode'] != CONFIG['artifacts'][edition]['defaultMode'] or layout['layoutErrors']:
                raise ValueError(f'UI preview audit failed: {layout}')
            png = image.read_bytes()
            if png[:8] != b'\x89PNG\r\n\x1a\n':
                raise ValueError('UI preview is not a PNG')
            width, height = struct.unpack_from('>II', png, 16)
            if (width, height) != tuple(map(int, size.split('x'))):
                raise ValueError('UI preview dimensions do not match the requested clean-window size')
            runs.append({'size': size, 'exitCode': result.returncode, 'layoutErrors': [], 'screenshotBytes': len(png)})
    if sorted(path.name for path in edition_dir.iterdir()) != expected_files:
        raise ValueError('Clean startup unexpectedly wrote settings, logs, profiles, or runtime files')
    return {'passed': True, 'gameStarted': False, 'servicesStarted': False, 'filesRemainClean': True, 'runs': runs}


def source_snapshot_info(intermediate):
    intermediate = Path(intermediate)
    launcher = json.loads((intermediate / 'launcher' / 'launcher-inputs.json').read_text(encoding='utf-8-sig'))
    sdk = json.loads((intermediate / 'sdk' / 'sdk-inputs.json').read_text(encoding='utf-8-sig'))
    for name, digest in launcher.items():
        if sha256(intermediate / 'launcher' / 'launcher-src' / name) != digest:
            raise ValueError(f'Launcher input snapshot changed: {name}')
    for name, digest in sdk['inputs'].items():
        if sha256(intermediate / 'sdk' / 'sdk-src' / name) != digest:
            raise ValueError(f'SDK input snapshot changed: {name}')
    return {'launcher': launcher, 'sdk': sdk}


def staged_layout_test(staged_directory, bin_directory):
    staged = Path(staged_directory).resolve()
    binaries = Path(bin_directory).resolve()
    entries = {item.name: item for item in staged.iterdir()}
    loose_files = [name for name, item in entries.items() if not item.is_dir()]
    if loose_files:
        raise ValueError('Staged output root contains loose files: ' + ', '.join(sorted(loose_files)))
    expected_dirs = {'backup-tool', 'bin', 'cn', 'en'}
    allowed_dirs = expected_dirs | {'reports'}
    unexpected = set(entries) - allowed_dirs
    if unexpected:
        raise ValueError('Unexpected staged output directories: ' + ', '.join(sorted(unexpected)))
    missing = expected_dirs - set(entries)
    if missing:
        raise ValueError('Missing staged output directories: ' + ', '.join(sorted(missing)))

    sdk_hash = sha256(binaries / CONFIG['sdk']['file'])
    checked = {}
    for folder, edition in (('cn', 'CN'), ('en', 'EN')):
        edition_dir = staged / folder
        app_dir = edition_dir / 'app'
        edition_entries = {item.name for item in edition_dir.iterdir()}
        expected_edition_entries = {'OverFieldLauncher.exe', 'README.md', 'app', 'backup-tool', 'docs'}
        if edition_entries != expected_edition_entries:
            raise ValueError(f'{folder} root entries mismatch: {sorted(edition_entries)}')
        launcher_path = edition_dir / 'OverFieldLauncher.exe'
        if sha256(launcher_path) != sha256(binaries / CONFIG['artifacts'][edition]['file']):
            raise ValueError(f'{folder}/OverFieldLauncher.exe does not match built {edition} binary')
        if sha256(app_dir / CONFIG['sdk']['file']) != sdk_hash:
            raise ValueError(f'{folder}/app/{CONFIG["sdk"]["file"]} does not match built SDK')
        actual = inspect_edition(launcher_path)
        if actual.get('english') != (edition == 'EN'):
            raise ValueError(f'{folder} launcher edition mismatch: {actual!r}')
        privacy = subprocess.run(
            [sys.executable, '-B', '-X', 'utf8', str(app_dir / 'privacy_check.py')],
            cwd=edition_dir, capture_output=True, text=True, encoding='utf-8', check=False,
        )
        if privacy.returncode != 0:
            raise RuntimeError(privacy_check.safe_error(privacy.stderr or privacy.stdout))
        importing = subprocess.run(
            [sys.executable, '-I', '-B', '-X', 'utf8', '-c',
             'import sys; sys.path.insert(0, sys.argv[1]); import capture_zip', str(app_dir)],
            capture_output=True, text=True, encoding='utf-8', check=False,
        )
        if importing.returncode != 0:
            raise RuntimeError(privacy_check.safe_error(importing.stderr or importing.stdout))
        for script_args in (
            [str(app_dir / 'capture_import.py'), '--help'],
            [str(app_dir / 'official_import.py'), '--help'],
            [str(app_dir / 'update_apply.py'), '--help'],
            [str(app_dir / 'launcher_updater.py'), '--help'],
            [str(app_dir / 'profile_manager.py'), '--help'],
            [str(app_dir / 'official_capture.py'), 'status'],
        ):
            isolated = subprocess.run(
                [sys.executable, '-I', '-B', '-X', 'utf8', *script_args],
                cwd=edition_dir, capture_output=True, text=True, encoding='utf-8', check=False,
            )
            if isolated.returncode != 0:
                raise RuntimeError(f'{folder} isolated script failed {script_args}: {isolated.stderr or isolated.stdout}')
        # Trigger sibling module imports (official_launcher, repair_version) under isolated mode too.
        for script_args, expected in (
            ([str(app_dir / 'official_capture.py'), 'validate-launcher', '--launcher', str(edition_dir / 'missing.exe')], '"error"'),
            ([str(app_dir / 'profile_manager.py'), 'client', '--root', str(edition_dir), '--base', str(edition_dir / 'missing-profiles')], 'FileNotFoundError'),
        ):
            isolated = subprocess.run(
                [sys.executable, '-I', '-B', '-X', 'utf8', *script_args],
                cwd=edition_dir, capture_output=True, text=True, encoding='utf-8', check=False,
            )
            combined = (isolated.stdout or '') + '\n' + (isolated.stderr or '')
            if 'ModuleNotFoundError' in combined or expected not in combined:
                raise RuntimeError(f'{folder} isolated sibling check failed {script_args}: {combined}')
        checked[folder] = {'launcherSha256': sha256(launcher_path), 'privacyPassed': True, 'importable': True}

    if (staged / 'cn/README.md').read_bytes() == (staged / 'en/README.md').read_bytes():
        raise ValueError('CN and EN staged README.md should reflect their distinct editions')
    if not (staged / 'en/README.md').read_text(encoding='utf-8').startswith(f"# OverField International Launcher {CONFIG['version']}"):
        raise ValueError('EN staged README.md version header mismatch')
    standalone_backup = staged / 'backup-tool'
    if not (standalone_backup / 'parse_pcap.py').is_file():
        raise ValueError('Standalone backup-tool is missing parse_pcap.py')
    if (standalone_backup / 'tools').exists() or list(standalone_backup.rglob('test_*.py')):
        raise ValueError('Standalone backup-tool contains excluded vendor tools or tests')
    return {'passed': True, 'directories': sorted(entries), 'editions': checked}


def verify_build(directory, report_path, logs, rebuild_directory=None, intermediate_directory=None, staged_directory=None):
    directory = Path(directory).resolve()
    report_path = Path(report_path).resolve()
    logs = Path(logs).resolve()
    logs.mkdir(parents=True, exist_ok=True)
    checks = []

    def record(name, function, warning=False):
        try:
            details = function()
            checks.append({'name': name, 'status': 'WARN' if warning else 'PASS', 'details': details})
            return details
        except Exception as error:
            checks.append({'name': name, 'status': 'FAIL', 'details': privacy_check.safe_error(error)})
            return None

    paths = [directory / name for name in ARTIFACT_NAMES]
    signatures = record('authenticode', lambda: signature_info(paths), warning=True) or []
    signature_by_name = {Path(item['path']).name: item for item in signatures}
    artifacts = {}
    for name in ARTIFACT_NAMES:
        path = directory / name

        def inspect(name=name, path=path):
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError('Artifact is missing or empty')
            findings = sorted(privacy_check.scan_file(path, include_assignments=False))
            if findings:
                raise ValueError('Artifact privacy findings: ' + ', '.join(findings))
            info = pe_info(path)
            signature = signature_by_name.get(name)
            if not signature:
                raise ValueError('Authenticode result is missing')
            accepted = {CONFIG['signing']['acceptedUnsignedStatus'], 'Valid'}
            if signature['status'] not in accepted:
                raise ValueError('Unacceptable Authenticode state: ' + signature['status'])
            return {
                'file': name, 'size': path.stat().st_size, 'sha256': sha256(path),
                'machine': info['machine'], 'peTimestamp': info['timestamp'],
                'subsystem': info['subsystem'], 'privacyFindings': [],
                'signature': signature,
                'cli': info.get('cli'), 'exports': info.get('exports', []),
            }

        artifacts[name] = record(f'artifact-{name}', inspect)

    for edition, expected in CONFIG['artifacts'].items():
        def check_edition(edition=edition, expected=expected):
            actual = inspect_edition(directory / expected['file'])
            wanted = {
                'english': edition == 'EN', 'title': 'OverField · Local Launcher' if edition == 'EN' else '开放空间 · 本地启动器',
                'start': 'Start game  →' if edition == 'EN' else '启动游戏  →',
                'defaultMode': expected['defaultMode'], 'settingsFile': expected['settingsFile'],
                'assemblyTitle': expected['assemblyTitle'], 'fileVersion': CONFIG['version'] + '.0',
                'informationalVersion': CONFIG['version'],
            }
            for key, value in wanted.items():
                if actual.get(key) != value:
                    raise ValueError(f'{edition} {key}: expected {value!r}, got {actual.get(key)!r}')
            pe = artifacts[expected['file']]
            if not pe or not pe.get('cli', {}).get('flags', 0) & 1:
                raise ValueError(f'{edition} is not an IL-only managed executable')
            return actual

        record(f'edition-{edition}', check_edition)

    def check_sdk():
        path = directory / CONFIG['sdk']['file']
        info = pe_info(path)
        if info['machine'] != '0x8664':
            raise ValueError('SDK is not AMD64')
        if sorted(info.get('exports', [])) != sorted(CONFIG['sdk']['exports']):
            raise ValueError('SDK export table differs from release-build.json/sdk.def')
        return sdk_callback_test(path)

    record('sdk-exports-and-callbacks', check_sdk)
    snapshot_dir = Path(intermediate_directory).resolve() if intermediate_directory else directory / '.intermediate'
    record('source-snapshots', lambda: source_snapshot_info(snapshot_dir))
    if staged_directory:
        record('staged-layout', lambda: staged_layout_test(staged_directory, directory))
    with tempfile.TemporaryDirectory(prefix='launcher-clean-start-', dir=logs) as smoke:
        for edition, expected in CONFIG['artifacts'].items():
            record(f'clean-start-{edition}', lambda edition=edition, expected=expected: clean_start_test(edition, directory / expected['file'], smoke, logs))
    if rebuild_directory:
        def compare_rebuild():
            rebuild = Path(rebuild_directory).resolve()
            compared = {}
            for name in ARTIFACT_NAMES:
                first, second = sha256(directory / name), sha256(rebuild / name)
                if first != second:
                    raise ValueError(f'Non-reproducible artifact {name}: {first} != {second}')
                compared[name] = first
            return {'passed': True, 'sha256': compared}

        record('reproducibility', compare_rebuild)
    else:
        checks.append({'name': 'reproducibility', 'status': 'WARN', 'details': 'No second build directory was supplied.'})
    record('defender-status', defender_status, warning=True)
    report = {
        'schemaVersion': 1,
        'generatedAtUtc': datetime.now(timezone.utc).isoformat(),
        'version': CONFIG['version'],
        'outputDirectory': str(directory),
        'passed': not any(check['status'] == 'FAIL' for check in checks),
        'artifacts': artifacts,
        'checks': checks,
        'limitations': [
            'Unsigned artifacts are release candidates, not publisher-identified releases.',
            'Defender real-time protection was not enabled; no active malware-scan result is claimed.',
            'Clean startup means UI preview only; no game, service, capture, account, or network operation was tested.',
            'Final release requires rebuilding after all in-flight business-source edits finish.',
        ],
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = report_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(report_path)
    return 0 if report['passed'] else 1


class BuildScriptTests(unittest.TestCase):
    def test_release_config_has_distinct_editions(self):
        cn, en = CONFIG['artifacts']['CN'], CONFIG['artifacts']['EN']
        self.assertEqual((cn['defaultMode'], en['defaultMode']), ('solo', 'steam'))
        self.assertNotEqual(cn['settingsFile'], en['settingsFile'])
        self.assertEqual(en['define'], 'ENGLISH')
        self.assertNotEqual(cn['file'], en['file'])
        self.assertFalse(CONFIG['smokeTest']['startsGame'])
        self.assertFalse(CONFIG['smokeTest']['startsServices'])

    def test_launcher_script_uses_framework_csc_and_guards_failures(self):
        code = (source_file(ROOT, 'build_launcher.ps1')).read_text(encoding='utf-8-sig')
        self.assertIn('Framework64\\v4.0.30319\\csc.exe', code)
        self.assertIn("'/warnaserror+'", code)
        self.assertIn("'/debug-'", code)
        self.assertIn('Save-DeterministicAssembly', code)
        self.assertIn('no previous binary was retained', code)
        self.assertIn('launcher-inputs.json', code)
        self.assertNotIn("'/deterministic'", code)  # Unsupported by the installed Framework compiler.

    def test_sdk_script_discovers_build_tools_and_is_reproducible(self):
        code = (source_file(ROOT, 'build_sdk.ps1')).read_text(encoding='utf-8-sig')
        self.assertIn('vswhere.exe', code)
        self.assertIn('Microsoft.VisualStudio.Component.VC.Tools.x86.x64', code)
        self.assertIn('Windows Kits\\Installed Roots', code)
        self.assertIn("'/Brepro'", code)
        self.assertIn("'/WX'", code)
        self.assertNotIn('2022\\Community', code)

    def test_build_all_never_reuses_old_outputs(self):
        code = (source_file(ROOT, 'build_all.bat')).read_text(encoding='utf-8-sig')
        for name in ARTIFACT_NAMES:
            self.assertIn(name, code)
        self.assertIn('if errorlevel 1', code)
        self.assertIn('reproducibility', code)
        self.assertIn('test_build.py', code)
        self.assertIn('privacy_check.py', code)

    def test_build_files_are_release_allowlisted(self):
        for name in ('build_launcher.ps1', 'build_sdk.cmd', 'build_sdk.ps1', 'build_all.bat',
                     'release-build.json', 'tests/test_build.py'):
            self.assertIn(source_file(ROOT, name).relative_to(ROOT).as_posix(), privacy_check.ALLOWED)

    def test_update_packages_keep_capture_runtime_importable(self):
        import io
        import zipfile
        from contextlib import redirect_stdout
        from unittest.mock import patch
        from build import build_update_assets as builder

        for layout in ('flat', 'repository', 'repository-bin', 'repository-app'):
            with self.subTest(layout=layout), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source, templates, output = (root / name for name in ('source', 'templates', 'output'))
                runtime = source if layout == 'flat' else source / 'src/runtime'
                binaries = source if layout == 'flat' else source / (
                    'dist/bin' if layout in ('repository-bin', 'repository-app') else 'dist'
                )
                runtime.mkdir(parents=True)
                binaries.mkdir(parents=True, exist_ok=True)
                templates.mkdir()
                for name in ('capture_zip.py', 'privacy_check.py', 'launcher_updater.py',
                             'update_apply.py', 'update_policy.py', 'update_trust.py'):
                    shutil.copyfile(source_file(ROOT, name), runtime / name)
                (source / 'docs').mkdir()
                shutil.copyfile(source_file(ROOT, 'docs/auto-update.md'), source / 'docs/auto-update.md')
                template_entry = 'app/capture_zip.py' if layout == 'repository-app' else 'capture_zip.py'
                for edition, binary in CONFIG['artifacts'].items():
                    (binaries / binary['file']).write_bytes(b'MZ synthetic build fixture')
                    with zipfile.ZipFile(templates / f'OverFieldLauncher-{edition}-portable.zip', 'w') as archive:
                        archive.write(runtime / 'capture_zip.py', template_entry)
                signing_key = root / 'synthetic-key.pem'
                signing_key.write_text('Synthetic test fixture; not a private key.', encoding='ascii')

                def fake_sign(arguments, **kwargs):
                    Path(arguments[-1]).write_bytes(b'synthetic signature')

                with patch.object(builder.subprocess, 'run', side_effect=fake_sign), \
                        patch('update_trust.verify_manifest', return_value={'version': CONFIG['version']}), \
                        redirect_stdout(io.StringIO()):
                    builder.build(CONFIG['version'], source, templates, output, 'synthetic-node', signing_key)

                # All supported layouts must produce valid bundles and keep runtime imports working.
                for edition in CONFIG['artifacts']:
                    for kind in ('update', 'portable'):
                        package = root / f'{edition}-{kind}'
                        with zipfile.ZipFile(output / f'OverFieldLauncher-{edition}-{kind}.zip') as archive:
                            self.assertFalse(any(name.startswith(('src/', 'build/')) for name in archive.namelist()))
                            archive.extractall(package)
                        import_root = package / 'app' if layout == 'repository-app' else package
                        self.assertTrue((import_root / 'installation-manifest.json').is_file())
                        if kind == 'portable':
                            self.assertTrue((import_root / 'release-build.json').is_file())
                        result = subprocess.run([
                            sys.executable, '-I', '-B', '-X', 'utf8', '-c',
                            'import sys; sys.path.insert(0, sys.argv[1]); import capture_zip', str(import_root)
                        ], capture_output=True, text=True, encoding='utf-8', check=False)
                        self.assertEqual(result.returncode, 0, f'{layout}/{edition}-{kind}: {result.stderr}')

    def test_stage_runtime_organizes_editions_and_backup_tool(self):
        from build.stage_runtime import stage_runtime

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'dist'
            binaries = output / 'bin'
            binaries.mkdir(parents=True)
            (binaries / 'OverFieldLauncher.exe').write_bytes(b'cn-exe')
            (binaries / 'OverFieldLauncher-EN.exe').write_bytes(b'en-exe')
            (binaries / 'LocalUidSDK.dll').write_bytes(b'sdk-dll')
            summary = stage_runtime(ROOT, output, binaries)
            self.assertEqual(set(path.name for path in output.iterdir()), {'backup-tool', 'bin', 'cn', 'en'})
            expected_root = {'OverFieldLauncher.exe', 'README.md', 'app', 'backup-tool', 'docs'}
            self.assertEqual(set(path.name for path in (output / 'cn').iterdir()), expected_root)
            self.assertEqual(set(path.name for path in (output / 'en').iterdir()), expected_root)
            self.assertGreater(summary['backupToolFiles'], 10)
            self.assertEqual((output / 'cn/OverFieldLauncher.exe').read_bytes(), b'cn-exe')
            self.assertEqual((output / 'en/OverFieldLauncher.exe').read_bytes(), b'en-exe')
            self.assertEqual((output / 'cn/app/LocalUidSDK.dll').read_bytes(), b'sdk-dll')
            self.assertEqual((output / 'en/app/LocalUidSDK.dll').read_bytes(), b'sdk-dll')
            self.assertTrue((output / 'cn/app/World-Control.ps1').is_file())
            self.assertIn(f"# OverField International Launcher {CONFIG['version']}",
                          (output / 'en/README.md').read_text(encoding='utf-8'))
            self.assertIn('# 开放空间私服启动器', (output / 'cn/README.md').read_text(encoding='utf-8'))
            self.assertFalse((output / 'cn/RELEASE-NOTES.md').exists())
            self.assertIn('](channel-switch.md)', (output / 'cn/docs/RELEASE-NOTES.md').read_text(encoding='utf-8'))
            self.assertTrue((output / 'cn/docs/RELEASE-CHECKLIST.md').is_file())
            self.assertTrue((output / 'backup-tool/parse_pcap.py').is_file())
            self.assertFalse((output / 'backup-tool/tools').exists())
            self.assertEqual(list((output / 'backup-tool').rglob('test_*.py')), [])

    def test_sdk_definition_matches_release_contract(self):
        lines = (source_file(ROOT, 'sdk.def')).read_text(encoding='utf-8-sig').splitlines()
        exports = [line.strip() for line in lines[lines.index('EXPORTS') + 1:] if line.strip()]
        self.assertEqual(exports, CONFIG['sdk']['exports'])

    def test_privacy_scanner_checks_utf16_and_binary_text(self):
        user_path = '\\'.join(['C:', 'Users', 'example-user', 'private'])
        self.assertIn('user-path', privacy_check.scan_bytes(user_path.encode('utf-16le')))
        sample = 'ghp_' + 'A' * 30
        self.assertIn('github-token', privacy_check.scan_bytes(sample.encode('utf-8')))
        self.assertEqual(privacy_check.scan_bytes(b'ordinary release bytes'), set())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-build', type=Path)
    parser.add_argument('--intermediate', type=Path)
    parser.add_argument('--rebuild-directory', type=Path)
    parser.add_argument('--staged-directory', type=Path)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--logs', type=Path, default=ROOT)
    parser.add_argument('--write-hashes', type=Path)
    args = parser.parse_args()
    if args.write_hashes:
        target = write_hashes(args.write_hashes)
        print(target.read_text(encoding='ascii'), end='')
        return 0
    if args.verify_build:
        report = args.report or args.verify_build / 'build-report.json'
        return verify_build(args.verify_build, report, args.logs, args.rebuild_directory,
                            args.intermediate, args.staged_directory)
    unittest.main(argv=[sys.argv[0]], verbosity=2)


if __name__ == '__main__':
    sys.exit(main())
