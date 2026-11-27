"""Build-contract tests and release-artifact verification.

The verifier only exercises the launcher's UI-preview path and native SDK
callbacks. It never starts a game, a service, capture, or a network connection.
"""
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
CONFIG_PATH = ROOT / 'release-build.json'
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
            source = ROOT / name
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


def source_snapshot_info(output):
    intermediate = Path(output) / '.intermediate'
    launcher = json.loads((intermediate / 'launcher' / 'launcher-inputs.json').read_text(encoding='utf-8-sig'))
    sdk = json.loads((intermediate / 'sdk' / 'sdk-inputs.json').read_text(encoding='utf-8-sig'))
    for name, digest in launcher.items():
        if sha256(intermediate / 'launcher' / 'launcher-src' / name) != digest:
            raise ValueError(f'Launcher input snapshot changed: {name}')
    for name, digest in sdk['inputs'].items():
        if sha256(intermediate / 'sdk' / 'sdk-src' / name) != digest:
            raise ValueError(f'SDK input snapshot changed: {name}')
    return {'launcher': launcher, 'sdk': sdk}


def verify_build(directory, report_path, logs, rebuild_directory=None):
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
                'assemblyTitle': expected['assemblyTitle'], 'fileVersion': '1.3.0.0',
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
    record('source-snapshots', lambda: source_snapshot_info(directory))
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
        code = (ROOT / 'build_launcher.ps1').read_text(encoding='utf-8-sig')
        self.assertIn('Framework64\\v4.0.30319\\csc.exe', code)
        self.assertIn("'/warnaserror+'", code)
        self.assertIn("'/debug-'", code)
        self.assertIn('Save-DeterministicAssembly', code)
        self.assertIn('no previous binary was retained', code)
        self.assertIn('launcher-inputs.json', code)
        self.assertNotIn("'/deterministic'", code)  # Unsupported by the installed Framework compiler.

    def test_sdk_script_discovers_build_tools_and_is_reproducible(self):
        code = (ROOT / 'build_sdk.ps1').read_text(encoding='utf-8-sig')
        self.assertIn('vswhere.exe', code)
        self.assertIn('Microsoft.VisualStudio.Component.VC.Tools.x86.x64', code)
        self.assertIn('Windows Kits\\Installed Roots', code)
        self.assertIn("'/Brepro'", code)
        self.assertIn("'/WX'", code)
        self.assertNotIn('2022\\Community', code)

    def test_build_all_never_reuses_old_outputs(self):
        code = (ROOT / 'build_all.bat').read_text(encoding='utf-8-sig')
        for name in ARTIFACT_NAMES:
            self.assertIn(name, code)
        self.assertIn('if errorlevel 1', code)
        self.assertIn('reproducibility', code)
        self.assertIn('test_build.py', code)
        self.assertIn('privacy_check.py', code)

    def test_build_files_are_release_allowlisted(self):
        for name in ('build_launcher.ps1', 'build_sdk.cmd', 'build_sdk.ps1', 'build_all.bat',
                     'release-build.json', 'tests/test_build.py'):
            self.assertIn(name, privacy_check.ALLOWED)

    def test_sdk_definition_matches_release_contract(self):
        lines = (ROOT / 'sdk.def').read_text(encoding='utf-8-sig').splitlines()
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
    parser.add_argument('--rebuild-directory', type=Path)
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
        return verify_build(args.verify_build, report, args.logs, args.rebuild_directory)
    unittest.main(argv=[sys.argv[0]], verbosity=2)


if __name__ == '__main__':
    sys.exit(main())
