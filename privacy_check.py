"""Conservative secret scanning, redaction, and release-tree checks.
Findings contain categories only.  Neither a matched secret nor an untrusted
exception is interpolated into a diagnostic message.
"""
from __future__ import annotations
import json
from pathlib import Path
import re
import sys
ROOT = Path(__file__).resolve().parent
ALLOWED = {
    'Launcher.cs', 'Friendly.cs', 'SeasideUI.cs', 'RoomManagerForm.cs', 'Strings.cs', 'tests/test_friendly_errors.py', 'tests/test_editions.py', 'docs/editions.md', 'launcher.manifest', 'launcher.ico',
    'build_launcher.ps1', 'tests/test_pcap_streaming.py', 'tests/test_capture_modes.py', 'tests/test_collector_preflight.py', 'tests/test_collector_integration.py', 'tests/test_collector_gui_contract.py', 'LocalUidSDK.cpp', 'sdk.def', 'build_sdk.cmd', 'build_sdk.ps1', 'build_all.bat',
    'World-Control.ps1', 'Lan-Firewall.ps1', 'profile_manager.py', 'UpdaterUI.cs', 'launcher_updater.py', 'update_apply.py', 'update_policy.py', 'update_trust.py', 'build_update_assets.py', 'release-build.json', 'tests/test_build.py', 'tests/test_signed_updater_download.py', 'tests/test_update_apply.py', 'tests/test_updater_gui_contract.py', 'docs/auto-update.md',
    'capture_import.py', 'room_gateway.py', 'tests/test_gateway_forwarding.py', 'docs/login-empty-name.md', 'docs/channel-switch.md', 'proto_fields.json',
    'model_schema.json', 'service_runner.py', 'make_junction.ps1',
    'repair_version.py', 'lolo_redirect.py', 'tests/test_launcher.py',
    'README.md', 'requirements.txt', '.gitignore', 'privacy_check.py',
    'RELEASE-CHECKLIST.md', 'RELEASE-NOTES.md', 'tests/test_backup_tool.py',
    'official_capture.py', 'official_import.py', 'tests/test_official_import.py',
    'capture_checklist.py', 'tests/test_capture_checklist.py', 'tests/test_wifi_compat.py',
    'official_launcher.py', 'tests/test_official_launcher.py', 'tests/test_pktmon_encoding.py',
    'capture_zip.py', 'tests/test_capture_zip.py', 'tests/test_capture_completion.py', 'tests/test_import_robust.py', 'schema_restore.py', 'tests/test_bool_fields.py', 'repair_fashion_rows.py',
    'tests/test_capture_gui_completeness.py', 'tests/test_runtime_hardening.py', 'tests/test_import_security.py',
    'tests/test_client_isolation.py', 'tests/test_launcher_gui_state.py', 'tests/test_service_lifecycle.py',
    'docs/official-capture.md', 'docs/taptap-private.md', 'docs/international-error-overlay.md', 'docs/android-backup.md', 'docs/collector-1012.md',
    'client_identity.py', 'taptap_private_runner.py', 'taptap_private_agent.js', 'tests/test_taptap_private.py',
    'docs/login-and-data.md', 'docs/release-audit.md', 'docs/channel-login.md', 'preflight.py',
    'backup-tool/1_backup_account.cmd', 'backup-tool/2_backup_gardens.cmd',
    'backup-tool/3_decode_pcap.cmd', 'backup-tool/capture.ps1',
    'backup-tool/parse_pcap.py', 'backup-tool/extract_gardens.py', 'backup-tool/collector_preflight.py', 'backup-tool/exploration.py', 'backup-tool/exploration_catalog.json', 'backup-tool/test_exploration_integration.py', 'backup-tool/test_tcp_gap.py', 'backup-tool/test_scene_backup.py',
    'backup-tool/README.md', 'backup-tool/requirements.txt',
    'backup-tool/THIRD-PARTY.txt', 'backup-tool/使用说明.txt',
    'backup-tool/VERSION', 'backup-tool/RELEASE-NOTES.txt', 'backup-tool/pcap_compat.py',
    'backup-tool/Redirect.py', 'backup-tool/start_proxy.ps1', 'backup-tool/INTEGRATION.json',
    'backup-tool/UPSTREAM-MANIFEST.sha256',
    'backup-tool/4_import_player.cmd', 'backup-tool/import_to_ofps.py',
    'backup-tool/proto_gen/cmdid.csv', 'backup-tool/proto_gen/msg_id.txt',
    'backup-tool/proto_gen/net_pb2.py',
    'backup-tool/proto_gen/Lolo-LICENSE.txt',
    'backup-tool/proto_gen/of-ps-LICENSE.txt',
}
# 上游新增的自动更新与错误文案文件
ALLOWED |= {
    'Friendly.cs', 'UpdaterUI.cs', 'launcher_updater.py', 'update_apply.py',
    'update_policy.py', 'update_trust.py', 'build_update_assets.py', 'docs/auto-update.md',
    'docs/login-empty-name.md', 'docs/channel-switch.md',
}
GENERATED = {'OverFieldLauncher.exe', 'OverFieldLauncher-EN.exe', 'LocalUidSDK.dll', 'LocalUidSDK.obj',
             'LocalUidSDK.lib', 'LocalUidSDK.exp'}
_USER_PATH = re.compile(r'[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s"\']+', re.I)
_ABSOLUTE_PATH = re.compile(r'\b[A-Za-z]:[\\/][^\s"\'<>|]+', re.I)
_PRIVATE_KEY = re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----')
_PRIVATE_KEY_BLOCK = re.compile(
    r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----.*?'
    r'-----END (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----', re.S
)
_GITHUB_TOKEN = re.compile(r'gh[pousr]_[A-Za-z0-9]{20,}')
_AWS_KEY = re.compile(r'\b(?:AKIA|ASIA)[0-9A-Z]{16}\b')
_JWT = re.compile(r'\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b')
_BEARER = re.compile(r'\bBearer\s+[A-Za-z0-9._~+/-]{12,}=*', re.I)
_URL_CREDENTIALS = re.compile(r'\b[a-z][a-z0-9+.-]*://[^\s/:@"\']+:[^\s/@\"\']+@', re.I)
_SECRET_ASSIGNMENT = re.compile(
    r'(?i)(["\']?(?:api[_-]?key|access[_-]?(?:key|token)|refresh[_-]?token|'
    r'login[_-]?token|sdk[_-]?token|session[_-]?token|client[_-]?(?:log[_-]?server[_-]?token|secret)|'
    r'token|secret|password|passwd|authorization|cookie)["\']?)'
    r'(\s*[:=]\s*["\']?)([^\s,"\'}]+)'
)
TEXT_PATTERNS = (
    ('private-key', _PRIVATE_KEY), ('github-token', _GITHUB_TOKEN),
    ('cloud-access-key', _AWS_KEY), ('jwt', _JWT), ('bearer-token', _BEARER),
    ('url-credentials', _URL_CREDENTIALS), ('secret-assignment', _SECRET_ASSIGNMENT),
)
PATTERNS = [_USER_PATH, *(pattern for _, pattern in TEXT_PATTERNS)]
_SENSITIVE_KEYS = {
    'token', 'secret', 'password', 'passwd', 'credential', 'credentials',
    'authorization', 'cookie', 'cookies', 'sessionkey', 'privatekey',
    'accesstoken', 'refreshtoken', 'logintoken', 'sdktoken', 'clientlogservertoken',
    'apikey', 'accesskey', 'secretkey', 'sessiontoken', 'authToken'.lower(),
    'auth', 'authentication', 'session', 'sessionid', 'passwordhash', 'clientsecret',
}
_MAX_SCAN_NODES = 200_000
def normalize_key(value: str) -> str:
    return re.sub(r'[^a-z0-9]', '', value.lower())
def sensitive_key(key) -> bool:
    if not isinstance(key, str):
        return False
    normalized = normalize_key(key)
    return normalized in _SENSITIVE_KEYS or normalized.endswith(('token', 'password', 'passwd', 'secretkey', 'apikey'))
def scan_text(text: str, include_assignments: bool = True) -> set[str]:
    """Return secret categories, never the matched values.
    Generic assignment syntax is useful for decoded credential documents but is
    not high-confidence evidence in source code (for example ``token = None``).
    """
    if not isinstance(text, str):
        return set()
    return {label for label, pattern in TEXT_PATTERNS
            if (include_assignments or label != 'secret-assignment') and pattern.search(text)}
def secret_findings(value, _budget=None) -> set[str]:
    """Inspect decoded JSON or bytes for credentials without retaining them."""
    budget = [ _MAX_SCAN_NODES ] if _budget is None else _budget
    if budget[0] <= 0:
        raise ValueError('Archive content is too complex to scan safely.')
    budget[0] -= 1
    if isinstance(value, dict):
        findings = set()
        for key, item in value.items():
            if sensitive_key(key) and item not in (None, '', False, 0):
                findings.add('sensitive-field')
            findings.update(secret_findings(item, budget))
        return findings
    if isinstance(value, (list, tuple)):
        findings = set()
        for item in value:
            findings.update(secret_findings(item, budget))
        return findings
    if isinstance(value, bytes):
        return scan_text(value.decode('utf-8', errors='ignore'))
    if isinstance(value, str):
        return scan_text(value)
    return set()
def reject_secrets(value) -> None:
    findings = secret_findings(value)
    if findings:
        # Category names are fixed constants and contain no account data.
        raise ValueError('Archive contains sensitive credentials (' + ', '.join(sorted(findings)) + ').')
def redact_text(value) -> str:
    """Remove secrets and local usernames from an untrusted diagnostic string."""
    text = str(value)
    text = _PRIVATE_KEY_BLOCK.sub('[REDACTED_PRIVATE_KEY]', text)
    text = _PRIVATE_KEY.sub('[REDACTED_PRIVATE_KEY]', text)
    text = _URL_CREDENTIALS.sub('[REDACTED_URL_CREDENTIALS]', text)
    for pattern in (_GITHUB_TOKEN, _AWS_KEY, _JWT, _BEARER):
        text = pattern.sub('[REDACTED]', text)
    text = _SECRET_ASSIGNMENT.sub(lambda match: match.group(1) + match.group(2) + '[REDACTED]', text)
    text = _ABSOLUTE_PATH.sub('[REDACTED_PATH]', text)
    return _USER_PATH.sub('[REDACTED_USER_PATH]', text)
def redact_value(value):
    if isinstance(value, dict):
        return {key: redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
def safe_error(error: Exception) -> str:
    """Bound error size and redact paths/credentials before logging or display."""
    return redact_text(error)[:1000]
def scan_bytes(data: bytes, include_assignments: bool = True) -> set[str]:
    """Return finding categories for text or binary release artifacts."""
    findings = set()
    texts = (data.decode('utf-8', errors='ignore'), data.decode('utf-16-le', errors='ignore'))
    for text in texts:
        findings.update(scan_text(text, include_assignments))
        if _USER_PATH.search(text):
            findings.add('user-path')
    return findings
def scan_file(path, include_assignments: bool = True) -> set[str]:
    return scan_bytes(Path(path).read_bytes(), include_assignments)
def main():
    errors = []
    count = 0
    for path in ROOT.rglob('*'):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        parts = relative.parts
        if parts[0] == '.git' or '__pycache__' in parts:
            continue
        if parts[:2] == ('backup-tool', 'tools'):
            continue  # Git-ignored vendor runtime; verified against the upstream bundle manifest.
        if relative.as_posix() not in ALLOWED | GENERATED:
            errors.append(f'Unexpected file: {path.relative_to(ROOT)}')
            continue
        count += 1
        # Release sources and generated binaries both use documented local
        # placeholders (for example accessToken=local-session). Generic
        # assignment syntax is therefore not treated as a credential here;
        # user paths and high-confidence credential formats are still rejected.
        findings = scan_file(path, include_assignments=False)
        if findings:
            errors.append(f'Potential private data ({", ".join(sorted(findings))}): {path.name}')
    if errors:
        print('\n'.join(redact_text(error) for error in errors))
        return 1
    print(f'PASS: {count} allowlisted files; no detected user paths or credentials.')
    print('Manually review account identifiers, binary metadata and asset licenses before uploading.')
    return 0
if __name__ == '__main__':
    sys.exit(main())
