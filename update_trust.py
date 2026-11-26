"""Pinned, dependency-free verification of the stable launcher update protocol."""
import hashlib
import hmac
import json
import re
from urllib.parse import urlsplit

REPOSITORY = 'inori-dream/miaomiao-launcher'
KEY_ID = 'overfield-update-2026-11'
N_HEX = 'aa300ccbb14988cb4ea161b5b4046ef08b42a4afc7158ff85470e7df1b705cedc6654f0465ec900d87b024e057cb2dc5a3c604f213e2144e8e5f18347ff0f32e6671894bca5fa181d33ef57ff0201eb83313bd800d9c90e1de4dcf220d000393d017df43f0c54d555e42eca38c83ddaa1f75ad061c085833452eb2b7df1bc7c0637f17bfe8a335c2dba20aac88e778c2782aa1ccd563b8bc00a8c4e5c889715be50d5f864aab09e6b5f013e4489e4a52fc39f1e370aa23710f7af942b6d83f0a887802e8d5b3c28a59f2cd317c7f09b1f7af9cbb7240d3912bc9e843139c091103ee90d01e68ab917c2342492e866eccbee1c01935af88447bbaa685f70ce297'
E = 65537
MAX_MANIFEST = 2 * 1024 * 1024
MAX_PAYLOAD = 64 * 1024 * 1024
MAX_FILES = 4096

class TrustError(ValueError):
    pass

def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise TrustError('duplicate JSON key')
            result[key] = value
        return result
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                          parse_constant=lambda x: (_ for _ in ()).throw(TrustError('invalid JSON constant')))
    except (UnicodeError, ValueError) as exc:
        raise TrustError('invalid JSON: ' + str(exc)) from exc

def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', value) or len(value) > 32:
        raise TrustError('invalid stable version')
    return tuple(map(int, value.split('.')))

def asset_url(tag, name):
    return 'https://github.com/' + REPOSITORY + '/releases/download/' + tag + '/' + name

def safe_relative(path):
    if not isinstance(path, str) or len(path) > 240 or '\\' in path or ':' in path or not path or path.startswith('/'):
        raise TrustError('unsafe file path')
    parts = path.split('/')
    for part in parts:
        if part in ('', '.', '..') or part.endswith((' ', '.')) or any(ord(c) < 32 for c in part):
            raise TrustError('unsafe file path')
        if part.split('.')[0].upper() in {'CON','PRN','AUX','NUL', *('COM'+str(i) for i in range(1,10)), *('LPT'+str(i) for i in range(1,10))}:
            raise TrustError('reserved file path')
    # Defense in depth: update packages never own user data, games or runtimes.
    if parts[0].lower() in {'runtime','games','game','android','backups','profiles','saves','data','database','databases','.git'} or path.lower().endswith(('.apk','.db','.sqlite','.sqlite3','.pem')):
        raise TrustError('unmanaged file path')
    return path

def _record(record, limit):
    if not isinstance(record, dict) or type(record.get('size')) is not int or not 0 <= record['size'] <= limit:
        raise TrustError('invalid size')
    if not isinstance(record.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', record['sha256']):
        raise TrustError('invalid sha256')

def verify_manifest(raw, sig):
    """Verify exact bytes against pinned RSA2048 PKCS1v1.5 SHA256, then schema."""
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_MANIFEST or not isinstance(sig, bytes) or len(sig) != 256:
        raise TrustError('invalid manifest/signature length')
    n = int(N_HEX, 16)
    signature = int.from_bytes(sig, 'big')
    if signature >= n:
        raise TrustError('invalid RSA signature')
    digest_info = bytes.fromhex('3031300d060960864801650304020105000420') + hashlib.sha256(raw).digest()
    expected = b'\x00\x01' + b'\xff' * (256-len(digest_info)-3) + b'\x00' + digest_info
    actual = pow(signature, E, n).to_bytes(256, 'big')
    if not hmac.compare_digest(actual, expected):
        raise TrustError('signature verification failed')
    obj = strict_json(raw)
    if not isinstance(obj, dict) or type(obj.get('schemaVersion')) is not int or obj['schemaVersion'] != 1 or obj.get('repository') != REPOSITORY or obj.get('channel') != 'stable':
        raise TrustError('unsupported manifest')
    version_tuple(obj.get('version'))
    if obj.get('releaseTag') != 'v'+obj['version'] or type(obj.get('minUpdaterVersion')) is not int or not 1 <= obj['minUpdaterVersion'] <= 1:
        raise TrustError('unsupported updater/release version')
    assets = obj.get('assets')
    if not isinstance(assets, dict) or set(assets) != {'CN','EN'}:
        raise TrustError('both edition assets required')
    for edition, record in assets.items():
        _record(record, MAX_PAYLOAD)
        name = 'OverFieldLauncher-'+edition+'-update.zip'
        if record.get('name') != name or record.get('url') != asset_url(obj['releaseTag'], name):
            raise TrustError('wrong asset origin or edition')
        files = record.get('files')
        if not isinstance(files, dict) or not 0 < len(files) <= MAX_FILES:
            raise TrustError('invalid files inventory')
        from update_policy import validate_files
        try:
            validate_files(files)
        except ValueError as exc:
            raise TrustError(str(exc)) from exc
        seen = set()
        total = 0
        for path, info in files.items():
            safe_relative(path)
            folded = path.casefold()
            if folded in seen:
                raise TrustError('case-colliding paths')
            seen.add(folded)
            _record(info, MAX_PAYLOAD)
            total += info['size']
        if total > MAX_PAYLOAD:
            raise TrustError('expanded payload too large')
    return obj
