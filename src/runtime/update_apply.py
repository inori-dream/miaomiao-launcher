"""Signed launcher update helper. All installation writes are journaled and reversible.

CLI stdout is one JSON result. No process is terminated; unknown process state blocks.
Tests may inject verifier/busy_finder, production CLI never accepts those overrides.
"""
from pathlib import Path
import argparse
import contextlib
import hashlib
import json
import os
import stat
import subprocess
import sys
import time
import zipfile

_SELF_DIR = str(Path(__file__).resolve().parent)
if _SELF_DIR not in sys.path:
    sys.path.insert(0, _SELF_DIR)

from update_policy import (assert_no_reparse, require_managed, validate_files,
                           validate_path, MAX_FILES, MAX_TOTAL_SIZE)


def _json(raw):
    def unique(pairs):
        result = {}
        for k, v in pairs:
            if k in result:
                raise ValueError('duplicate JSON key')
            result[k] = v
        return result
    return json.loads(raw, object_pairs_hook=unique)


def _meta(path):
    assert_no_reparse(path)
    h = hashlib.sha256()
    size = 0
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
            size += len(block)
    return {'size': size, 'sha256': h.hexdigest()}


def _sync_dir(path):
    if os.name != 'nt':
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _replace_once(src, dst):
    assert_no_reparse(src)
    assert_no_reparse(dst)
    if os.name == 'nt':
        # ReplaceFileW keeps the destination's attributes and is atomic on NTFS,
        # but it refuses when another handle is open. MoveFileEx is the fallback.
        import ctypes
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        replace = kernel32.ReplaceFileW
        replace.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
                            ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        replace.restype = ctypes.c_int
        if Path(dst).exists() and replace(str(dst), str(src), None, 0, None, None):
            return
        # MOVEFILE_WRITE_THROUGH + REPLACE_EXISTING: unlike rename alone,
        # Windows explicitly waits for the move to reach storage.
        move = kernel32.MoveFileExW
        move.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
        move.restype = ctypes.c_int
        if not move(str(src), str(dst), 0x1 | 0x8):
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        os.replace(src, dst)
        _sync_dir(Path(dst).parent)


# Transient sharing violations are normal on Windows: antivirus, the indexer or a
# just-exited process can hold a handle for a moment. Retrying beats failing an
# otherwise valid update, which is what MAA's updater does too.
REPLACE_ATTEMPTS = 8
REPLACE_BACKOFF = 0.05


def _replace(src, dst):
    """Replace dst with src, retrying transient Windows sharing violations."""
    last = None
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            _replace_once(src, dst)
            return
        except OSError as error:
            last = error
            # ERROR_SHARING_VIOLATION / ERROR_LOCK_VIOLATION / ERROR_ACCESS_DENIED
            if getattr(error, 'winerror', None) not in (32, 33, 5):
                raise
            if attempt + 1 >= REPLACE_ATTEMPTS:
                break
            time.sleep(REPLACE_BACKOFF * (2 ** attempt))
    raise last


def _durable(path, data):
    path = assert_no_reparse(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.new')
    assert_no_reparse(temp)
    with open(temp, 'wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    _replace(temp, path)


def _journal_write(path, journal):
    _durable(path, json.dumps(journal, ensure_ascii=False, sort_keys=True).encode('utf-8'))


def _identity(root):
    s = root.stat()
    return {'path': os.path.normcase(str(root)), 'device': s.st_dev, 'inode': s.st_ino}


@contextlib.contextmanager
def _lock(work):
    path = assert_no_reparse(work / 'apply.lock')
    with open(path, 'a+b') as f:
        f.seek(0)
        f.write(b'0')
        f.flush()
        f.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == 'nt':
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def _launcher_mutex(edition):
    """Hold the launcher's single-instance mutex while installation files change.

    Otherwise a user double-clicking mid-apply starts a second launcher against a
    half-replaced tree. The new instance sees the mutex taken, focuses nothing and
    exits, which is exactly what should happen during an update.
    """
    if os.name != 'nt':
        yield
        return
    import ctypes
    name = 'Local\\OverFieldLocalLauncherEN' if edition == 'EN' else 'Local\\OverFieldLocalLauncher'
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel32.CreateMutexW
    create.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    create.restype = ctypes.c_void_p
    close = kernel32.CloseHandle
    close.argtypes = [ctypes.c_void_p]
    close.restype = ctypes.c_int
    handle = create(None, 0, name)
    ERROR_ALREADY_EXISTS = 183
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    held = ctypes.get_last_error() != ERROR_ALREADY_EXISTS
    try:
        yield held
    finally:
        close(handle)


def _processes():
    if os.name != 'nt':
        raise RuntimeError('process inspection is only implemented on Windows')
    command = ('[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); '
               '$ErrorActionPreference = "Stop"; Get-CimInstance Win32_Process -ErrorAction Stop | '
               'Select-Object ProcessId,Name,ExecutablePath,CommandLine,CreationDate | '
               'ConvertTo-Json -Compress')
    p = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                       capture_output=True, timeout=20, encoding='utf-8', errors='replace',
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if p.returncode:
        raise RuntimeError('process inspection failed')
    rows = json.loads(p.stdout)
    return rows if isinstance(rows, list) else [rows]


def _query_process_image(pid):
    """Best-effort read-only Windows image lookup; never requests terminate rights."""
    if os.name != 'nt':
        return None
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    opening = kernel.OpenProcess
    opening.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    opening.restype = wintypes.HANDLE
    query = kernel.QueryFullProcessImageNameW
    query.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    query.restype = wintypes.BOOL
    closing = kernel.CloseHandle
    closing.argtypes = [wintypes.HANDLE]
    closing.restype = wintypes.BOOL
    handle = opening(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION only
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        return buffer.value if query(handle, 0, buffer, ctypes.byref(size)) else None
    finally:
        closing(handle)


def _root_in_text(root, text):
    """Match a root directory boundary, not e.g. D:\\en against D:\\english."""
    import re
    normalized = str(root).replace('/', '\\').rstrip('\\').casefold()
    value = str(text or '').replace('/', '\\').casefold()
    return bool(re.search(r'(?<![\w\\])' + re.escape(normalized) + r'(?=$|[\\\s\"\'])', value))


def _root_command_use(root, command):
    """A root file argument or explicit working directory, not prose/probe --root."""
    import re
    directory = str(root).replace('/', '\\').rstrip('\\').casefold()
    value = str(command or '').replace('/', '\\').casefold()
    file_reference = re.search(r'(?<![\w\\])' + re.escape(directory) + r'\\', value)
    cwd_reference = re.search(r'(?:--cwd|--working-directory|-workingdirectory|set-location|\bcd)\s*(?:=|\s)\s*[\"\']?' + re.escape(directory) + r'(?=$|[\s\"\'])', value)
    return bool(file_reference or cwd_reference)


def _state_pids(root):
    paths = [root / 'runtime/capture-state.json']
    for directory in (root, root / 'runtime'):
        assert_no_reparse(directory)
        if directory.is_dir():
            paths.extend(directory.glob('world-state*.json'))
    known = set()
    for path in paths:
        assert_no_reparse(path)
        if not path.exists():
            continue
        if path.stat().st_size > 1024 * 1024:
            raise ValueError('state file budget exceeded')
        state = _json(path.read_bytes())
        if not isinstance(state, dict):
            raise ValueError('invalid process state')
        for key, value in state.items():
            if key.lower() in ('pid', 'officialpid'):
                known.add(int(value or 0))
            elif key.lower() in ('server', 'gateway', 'proxy', 'client') and value is not None:
                if isinstance(value, dict):
                    lower = {k.lower(): v for k, v in value.items()}
                    known.add(int(lower.get('id', lower.get('pid', 0)) or 0))
                elif type(value) is int:
                    known.add(value)
                else:
                    raise ValueError('invalid owned process state')
    return {pid for pid in known if pid > 0}


def find_busy(root, ignore_pids=(), diagnostics=None):
    """Only installation ownership or dedicated candidates block partial visibility.

    Unowned generic interpreters are non-blocking diagnostics, even if privileged.
    Entire enumeration failure, owned state PIDs and dedicated unknowns fail closed.
    """
    root_path = assert_no_reparse(root)
    try:
        known_pids = _state_pids(root_path)
    except Exception as e:
        return [{'reason': 'unknown', 'detail': 'process state invalid: ' + str(e)}]
    ignored = {os.getpid(), *map(int, ignore_pids)}
    candidates = {'overfieldlauncher.exe', 'overfieldlauncher-en.exe', 'lolo.exe',
                  'overfield.exe', 'overfieldclient.exe', 'game.exe', 'client.exe',
                  'dumpcap.exe', 'tshark.exe', 'pktmon.exe', 'tcpdump.exe', 'mitmdump.exe'}
    generic = {'python.exe', 'pythonw.exe', 'python3.exe', 'pwsh.exe', 'powershell.exe'}
    result = []
    try:
        rows = _processes()
        if not isinstance(rows, list) or not rows:
            raise ValueError('process enumeration unavailable')
        for row in rows:
            pid = int(row['ProcessId'])
            if pid in ignored or pid in (0, 4):
                continue
            exe = row.get('ExecutablePath')
            cmd = row.get('CommandLine')
            if not exe:
                exe = _query_process_image(pid)
            name = str(row.get('Name') or (str(exe).replace('\\', '/').rsplit('/', 1)[-1] if exe else '')).lower()
            if _root_in_text(root_path, exe) or (name in candidates | generic and _root_command_use(root_path, cmd)):
                result.append({'pid': pid, 'reason': 'installation_in_use', 'path': exe})
            elif exe and cmd is not None:
                continue  # Fully inspected outside root, including stale/reused state PIDs.
            elif pid in known_pids or name in candidates or not name:
                result.append({'pid': pid, 'reason': 'unknown', 'name': name})
            elif name in generic and diagnostics is not None:
                diagnostics.append({'pid': pid, 'reason': 'unowned_interpreter_visibility',
                                    'name': name, 'path': exe, 'blocking': False})
            # System processes and unowned generic Python/PowerShell are not evidence
            # of this installation being in use. Do not block every root globally.
    except Exception as e:
        result.append({'reason': 'unknown', 'detail': str(e)})
    return result


def _wait_parent(pid, root, timeout, recorded=None):
    if not pid:
        return
    if pid == os.getpid() or pid <= 0:
        raise ValueError('invalid parent PID')
    rows = _processes()
    parent = recorded or next((r for r in rows if int(r['ProcessId']) == pid), None)
    if parent is not None and int(parent.get('ProcessId', -1)) != pid:
        raise ValueError('recorded parent PID mismatch')
    if parent is None:
        raise ValueError('parent identity unavailable; parent must still be alive at helper start')
    exe = parent.get('ExecutablePath')
    if not exe or not parent.get('CreationDate'):
        raise ValueError('unknown parent identity')
    allowed = {str(root / 'OverFieldLauncher.exe').lower(), str(root / 'OverFieldLauncher-EN.exe').lower()}
    if exe.lower() not in allowed:
        raise ValueError('parent is not this installation launcher')
    identity = (exe.lower(), parent['CreationDate'])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = _processes()
        current = next((r for r in rows if int(r['ProcessId']) == pid), None)
        if current is None:
            return
        if (str(current.get('ExecutablePath')).lower(), current.get('CreationDate')) != identity:
            raise ValueError('parent PID identity changed')
        time.sleep(0.2)
    raise TimeoutError('parent launcher is still running')


def _baseline_key_from_files(files):
    present = [name for name in ('app/installation-manifest.json', 'installation-manifest.json') if name in files]
    if len(present) != 1:
        raise ValueError('signed installation baseline is required')
    return present[0]


def _installed_baseline_path(root):
    nested = assert_no_reparse(root / 'app/installation-manifest.json')
    if nested.is_file():
        return nested, 'app/installation-manifest.json'
    return assert_no_reparse(root / 'installation-manifest.json'), 'installation-manifest.json'


def _baseline(raw, edition, version=None):
    doc = _json(raw)
    if doc.get('schemaVersion') != 1 or doc.get('edition') != edition or not isinstance(doc.get('version'), str):
        raise ValueError('invalid installation baseline')
    if version is not None and doc['version'] != version:
        raise ValueError('baseline version mismatch')
    files = validate_files(doc.get('files'))
    if any(p.lower() in ('installation-manifest.json', 'app/installation-manifest.json') for p in files):
        raise ValueError('baseline must not include itself')
    return doc


def _package(stage, edition, verifier):
    for name in ('manifest.json', 'manifest.sig', 'payload.zip'):
        assert_no_reparse(stage / name)
    raw = (stage / 'manifest.json').read_bytes()
    sig = (stage / 'manifest.sig').read_bytes()
    if len(raw) > 8 * 1024 * 1024 or len(sig) > 16384:
        raise ValueError('manifest budget exceeded')
    # Duplicate keys are rejected independently, even if a verifier ignores them.
    _json(raw)
    if verifier is None:
        from update_trust import verify_manifest
        verifier = verify_manifest
    manifest = verifier(raw, sig)
    if (manifest.get('schemaVersion') != 1 or manifest.get('repository') != 'inori-dream/miaomiao-launcher'
            or manifest.get('channel') != 'stable' or manifest.get('minUpdaterVersion', 999) > 1):
        raise ValueError('unsupported release manifest')
    asset = manifest['assets'][edition]
    files = validate_files(asset['files'])
    baseline_key = _baseline_key_from_files(files)
    if _meta(stage / 'payload.zip') != {'size': asset['size'], 'sha256': asset['sha256']}:
        raise ValueError('payload hash/size mismatch')
    with zipfile.ZipFile(stage / 'payload.zip') as z:
        infos = z.infolist()
        if len(infos) > MAX_FILES:
            raise ValueError('ZIP count budget exceeded')
        names = set()
        for info in infos:
            validate_path(info.filename)
            mode = info.external_attr >> 16
            if info.is_dir() or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG)):
                raise ValueError('ZIP non-regular member')
            if info.flag_bits & 1:
                raise ValueError('encrypted ZIP member')
            if info.filename.casefold() in names:
                raise ValueError('duplicate/case-colliding ZIP member')
            names.add(info.filename.casefold())
            if info.filename not in files or info.file_size != files[info.filename]['size']:
                raise ValueError('ZIP members differ from signed files')
        if {i.filename for i in infos} != set(files):
            raise ValueError('ZIP missing signed files')
        for info in infos:
            digest = hashlib.sha256()
            size = 0
            with z.open(info) as source:
                for block in iter(lambda: source.read(1024 * 1024), b''):
                    size += len(block)
                    if size > files[info.filename]['size']:
                        raise ValueError('ZIP member size exceeded')
                    digest.update(block)
            if {'size': size, 'sha256': digest.hexdigest()} != files[info.filename]:
                raise ValueError('signed member hash mismatch: ' + info.filename)
        baseline_meta = files[baseline_key]
        baseline_raw = z.read(baseline_key)
        if {'size': len(baseline_raw), 'sha256': hashlib.sha256(baseline_raw).hexdigest()} != baseline_meta:
            raise ValueError('baseline hash mismatch')
        baseline = _baseline(baseline_raw, edition, manifest['version'])
        if baseline['files'] != {k: v for k, v in files.items() if k != baseline_key}:
            raise ValueError('baseline does not match signed files')
    return manifest, files


def _extract(stage, files, prepared):
    with zipfile.ZipFile(stage / 'payload.zip') as z:
        total = 0
        for index, (name, expected) in enumerate(files.items()):
            target = assert_no_reparse(prepared / str(index))
            h = hashlib.sha256()
            size = 0
            with z.open(name) as src, open(target, 'xb') as out:
                while True:
                    block = src.read(1024 * 1024)
                    if not block:
                        break
                    size += len(block)
                    total += len(block)
                    if size > expected['size'] or total > MAX_TOTAL_SIZE:
                        raise ValueError('ZIP expansion budget exceeded')
                    h.update(block)
                    out.write(block)
                out.flush()
                os.fsync(out.fileno())
            if {'size': size, 'sha256': h.hexdigest()} != expected:
                raise ValueError('signed member hash mismatch: ' + name)


def _conflicts(root, old, files):
    conflicts = []
    for name in set(old) | set(files):
        target = assert_no_reparse(root / name)
        if name in old:
            if not target.is_file() or _meta(target) != old[name]:
                conflicts.append(name)
        elif target.exists():
            # Never adopt or overwrite pre-existing files absent from baseline.
            conflicts.append(name)
    return sorted(conflicts)


def _recover(root, work, dry_run=False):
    journal_path = assert_no_reparse(work / 'journal.json')
    if not journal_path.exists():
        return False
    journal = _json(journal_path.read_bytes())
    if journal.get('schemaVersion') != 1 or journal.get('root') != _identity(root):
        raise ValueError('transaction root identity mismatch')
    operations = journal['operations']
    validate_files({op['path']: op.get('new') or op['old'] for op in operations})
    if len({op['path'].casefold() for op in operations}) != len(operations):
        raise ValueError('invalid duplicate journal operations')
    if journal.get('state') in ('committed', 'rolled_back'):
        return False
    if journal.get('state') != 'applying' or journal.get('edition') not in ('CN', 'EN'):
        raise ValueError('invalid unfinished journal state')
    for field in ('old', 'new'):
        records = {op['path']: op[field] for op in operations if op[field] is not None}
        if records:
            validate_files(records)
    # Preflight every backup and destination before any rollback write.
    for i, op in enumerate(operations):
        target = assert_no_reparse(root / require_managed(op['path']))
        if op['old'] is not None:
            if _meta(work / 'backup' / str(i)) != op['old']:
                raise ValueError('rollback backup changed')
        current = _meta(target) if target.is_file() else None
        if current not in (op['old'], op['new']):
            raise ValueError('rollback target externally changed: ' + op['path'])
    if dry_run:
        return True
    for i, op in reversed(list(enumerate(operations))):
        target = assert_no_reparse(root / op['path'])
        current = _meta(target) if target.is_file() else None
        if current not in (op['old'], op['new']):
            raise ValueError('rollback target changed during recovery: ' + op['path'])
        if op['old'] is None:
            if target.exists():
                target.unlink()
                _sync_dir(target.parent)
        else:
            # Keep backup intact until recovery is fully durable, so a crash during
            # rollback can safely run the same journal again.
            restore = work / ('restore-' + str(i))
            _durable(restore, (work / 'backup' / str(i)).read_bytes())
            _replace(restore, target)
    journal['state'] = 'rolled_back'
    _journal_write(journal_path, journal)
    return True


def recover_update(root, *, busy_finder=None):
    """Recover an interrupted transaction, only when installation is known idle."""
    try:
        root = assert_no_reparse(root)
        work = assert_no_reparse(root / 'runtime/updates/transaction')
        busy = (busy_finder or find_busy)(root, ignore_pids=())
        if busy:
            return {'status': 'busy', 'details': busy}
        if not work.exists():
            return {'status': 'applied', 'recovered': False}
        with _lock(work):
            recovered = _recover(root, work)
        return {'status': 'applied', 'recovered': recovered}
    except Exception as e:
        return {'status': 'error', 'reason': str(e)}


def _launcher_exe(root):
    """Return the launcher executable that actually exists in this install.

    The portable package stores the international build as OverFieldLauncher.exe,
    so it must never be assumed from the edition label.
    """
    for name in ('OverFieldLauncher.exe', 'OverFieldLauncher-EN.exe'):
        path = root / name
        if assert_no_reparse(path).is_file():
            return path
    return None


def _record_applied(root, edition, version):
    """Leave a one-line record of the applied version for the launcher to report.

    Without it a successful update looks identical to a failed one from the outside:
    the window just comes back at some version. Best effort only - a failure here
    must never fail an otherwise complete update.
    """
    try:
        path = root / 'runtime/updates/last-update.json'
        assert_no_reparse(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        _durable(path, json.dumps({'edition': edition, 'version': version,
                                   'applied_at': int(time.time())},
                                  ensure_ascii=False, sort_keys=True).encode('utf-8'))
    except Exception:
        pass


def _restart_result(result, root, edition):
    try:
        journal_path = assert_no_reparse(root / 'runtime/updates/transaction/journal.json')
        if journal_path.exists() and _json(journal_path.read_bytes()).get('state') not in ('committed', 'rolled_back'):
            result['restart_error'] = 'unfinished transaction; manual recovery required'
            return result
        exe = _launcher_exe(root)
        if exe is None:
            result['restart_error'] = 'same-edition launcher missing'
        else:
            subprocess.Popen([str(exe)], cwd=root, close_fds=True)
            result['restarted'] = True
    except Exception as e:
        result['restart_error'] = str(e)
    return result


def apply_update(root, stage, edition, parent_pid=None, restart=False, *,
                 busy_finder=None, verifier=None, parent_timeout=120, fault_hook=None,
                 parent_identity=None):
    """Apply a verified staged package. Injectable hooks are for synthetic fixtures.

    Status: applied/busy/conflict/error. A failure after journal creation attempts
    immediate rollback; interrupted helpers recover on the next invocation.
    """
    journal_started = False
    parent_released = False
    def finish(result):
        return _restart_result(result, root, edition) if restart and parent_released else result
    try:
        if edition not in ('CN', 'EN'):
            raise ValueError('edition must be CN or EN')
        root = assert_no_reparse(root)
        if not root.is_dir():
            raise ValueError('installation root missing')
        stage = assert_no_reparse(stage)
        staging_root = root / 'runtime/updates/staging'
        if stage.parent != staging_root:
            raise ValueError('stage must be a direct child of installation staging directory')
        _wait_parent(parent_pid, root, parent_timeout, parent_identity)
        parent_released = True
        busy = (busy_finder or find_busy)(root, ignore_pids=())
        if busy:
            return finish({'status': 'busy', 'details': busy})
        work = assert_no_reparse(root / 'runtime/updates/transaction')
        work.mkdir(parents=True, exist_ok=True)
        with _lock(work):
            recovered = _recover(root, work)
            manifest, files = _package(stage, edition, verifier)
            if stage.name != str(manifest['version']) + '-' + edition:
                raise ValueError('stage version/edition mismatch')
            baseline_path, old_baseline_key = _installed_baseline_path(root)
            if not baseline_path.is_file():
                return finish({'status': 'conflict', 'reason': 'installation_baseline_missing'})
            old_doc = _baseline(baseline_path.read_bytes(), edition)
            from update_trust import version_tuple
            if version_tuple(manifest['version']) <= version_tuple(old_doc['version']):
                raise ValueError('update version must be newer than installed baseline')
            old = dict(old_doc['files'])
            old[old_baseline_key] = _meta(baseline_path)
            new_baseline_key = _baseline_key_from_files(files)
            validate_files({**old, **files})
            conflicts = _conflicts(root, old, files)
            if conflicts:
                return finish({'status': 'conflict', 'reason': 'local_changes', 'details': conflicts})
            # Fresh scratch only: leftovers from a previous extraction are removed
            # solely after lexical containment and reparse checks.
            for folder in ('prepared', 'backup'):
                directory = assert_no_reparse(work / folder)
                if directory.exists():
                    for child in directory.iterdir():
                        assert_no_reparse(child)
                        if not child.name.isdecimal() or not child.is_file():
                            raise ValueError('unexpected transaction scratch file')
                        child.unlink()
                else:
                    directory.mkdir()
            _extract(stage, files, work / 'prepared')
            operations = []
            indices = {name: i for i, name in enumerate(files)}
            # Baseline is always last. Journal covers removals too.
            baseline_keys = {old_baseline_key, new_baseline_key}
            tail = baseline_keys | {'OverFieldLauncher.exe', 'OverFieldLauncher-EN.exe'}
            all_names = set(old) | set(files)
            names = sorted(all_names - tail) + sorted((all_names & tail) - baseline_keys) + sorted(all_names & baseline_keys)
            for i, name in enumerate(names):
                target = assert_no_reparse(root / name)
                before = old.get(name)
                if before:
                    _durable(work / 'backup' / str(i), target.read_bytes())
                    if _meta(work / 'backup' / str(i)) != before:
                        raise ValueError('local file changed while backing up')
                operations.append({'path': name, 'old': before, 'new': files.get(name)})
            journal = {'schemaVersion': 1, 'root': _identity(root), 'edition': edition,
                       'version': manifest['version'], 'state': 'applying', 'operations': operations}
            busy = (busy_finder or find_busy)(root, ignore_pids=())
            if busy:
                return finish({'status': 'busy', 'details': busy})
            conflicts = _conflicts(root, old, files)
            if conflicts:
                return finish({'status': 'conflict', 'reason': 'local_changes', 'details': conflicts})
            _journal_write(work / 'journal.json', journal)
            journal_started = True
            # Only the write phase takes the single-instance mutex: a double-click
            # now cannot start a second launcher against a half-replaced tree. It is
            # released here so the restart at the end can start normally.
            try:
                with _launcher_mutex(edition):
                    for i, op in enumerate(operations):
                        target = assert_no_reparse(root / op['path'])
                        target.parent.mkdir(parents=True, exist_ok=True)
                        current = _meta(target) if target.is_file() else None
                        if current != op['old']:
                            raise ValueError('target changed during apply: ' + op['path'])
                        if op['new'] is None:
                            target.unlink()
                            _sync_dir(target.parent)
                        else:
                            _replace(work / 'prepared' / str(indices[op['path']]), target)
                        if fault_hook:
                            fault_hook(i, op)
                journal['state'] = 'committed'
                _journal_write(work / 'journal.json', journal)
            except Exception:
                _recover(root, work)
                raise
        result = {'status': 'applied', 'version': manifest['version'], 'edition': edition, 'recovered': recovered}
        _record_applied(root, edition, manifest['version'])
        return finish(result)
    except Exception as e:
        return finish({'status': 'error', 'reason': str(e), 'transaction_started': journal_started})


def _launcher_names(root):
    """Both launcher file names are valid inside one installation root.

    The portable package stores the international build as OverFieldLauncher.exe,
    so requiring the -EN name for edition EN rejected every real EN install.
    """
    return {str(root / 'OverFieldLauncher.exe').lower(),
            str(root / 'OverFieldLauncher-EN.exe').lower()}


def _installed_edition(root):
    """Read the edition from the installation baseline (read-only)."""
    path, _ = _installed_baseline_path(root)
    if not path.is_file():
        raise ValueError('installation baseline missing')
    doc = _json(path.read_bytes())
    edition = doc.get('edition')
    if edition not in ('CN', 'EN'):
        raise ValueError('invalid installation baseline edition')
    return edition


def launch_update(root, stage, edition, parent_pid, restart=False, *, verifier=None, busy_finder=None):
    """Detach helper without a shell; record parent identity before GUI exits."""
    try:
        root = assert_no_reparse(root)
        stage = assert_no_reparse(stage)
        if edition not in ('CN', 'EN') or not root.is_dir():
            raise ValueError('invalid root/edition')
        if stage.parent != root / 'runtime/updates/staging' or not stage.is_dir():
            raise ValueError('invalid stage')
        rows = _processes()
        parent = next((r for r in rows if int(r['ProcessId']) == parent_pid), None)
        allowed = _launcher_names(root)
        if (parent is None or not parent.get('CreationDate') or
                str(parent.get('ExecutablePath')).lower() not in allowed):
            raise ValueError('parent identity is not this installation launcher')
        busy = (busy_finder or find_busy)(root, ignore_pids=(parent_pid,))
        if busy:
            return {'status': 'busy', 'details': busy}
        manifest, files = _package(stage, edition, verifier)
        if stage.name != manifest['version'] + '-' + edition:
            raise ValueError('stage version/edition mismatch')
        baseline_path, old_baseline_key = _installed_baseline_path(root)
        if not baseline_path.is_file():
            return {'status': 'conflict', 'reason': 'installation_baseline_missing'}
        baseline = _baseline(baseline_path.read_bytes(), edition)
        from update_trust import version_tuple
        if version_tuple(manifest['version']) <= version_tuple(baseline['version']):
            raise ValueError('update version must be newer than installed baseline')
        old = {**baseline['files'], old_baseline_key: _meta(baseline_path)}
        validate_files({**old, **files})
        conflicts = _conflicts(root, old, files)
        if conflicts:
            return {'status': 'conflict', 'reason': 'local_changes', 'details': conflicts}
        journal_path = assert_no_reparse(root / 'runtime/updates/transaction/journal.json')
        if journal_path.exists() and _json(journal_path.read_bytes()).get('state') not in ('committed', 'rolled_back'):
            return {'status': 'error', 'reason': 'interrupted_transaction_requires_recovery'}
        helper = assert_no_reparse(Path(__file__).absolute())
        log_dir = assert_no_reparse(root / 'runtime/updates')
        log_dir.mkdir(parents=True, exist_ok=True)
        import uuid
        log = assert_no_reparse(log_dir / ('apply-' + uuid.uuid4().hex + '.log'))
        argv = [sys.executable, str(helper), 'apply', '--root', str(root), '--stage', str(stage),
                '--edition', edition, '--parent-pid', str(parent_pid),
                '--parent-identity', json.dumps(parent, ensure_ascii=False)]
        if restart:
            argv.append('--restart')
        with open(log, 'xb') as output:
            child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                     close_fds=True, cwd=root,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return {'status': 'launched', 'pid': child.pid, 'log': str(log)}
    except Exception as e:
        return {'status': 'error', 'reason': str(e)}


def launch_recovery(root, parent_pid, restart=False, *, busy_finder=None):
    """Read-only recovery preflight while GUI lives; child waits before writes."""
    try:
        root = assert_no_reparse(root)
        rows = _processes()
        parent = next((r for r in rows if int(r['ProcessId']) == parent_pid), None)
        if parent is None or not parent.get('CreationDate'):
            raise ValueError('parent identity unavailable')
        path = str(parent.get('ExecutablePath')).lower()
        if path not in _launcher_names(root):
            raise ValueError('parent not installation launcher')
        edition = _installed_edition(root)
        busy = (busy_finder or find_busy)(root, ignore_pids=(parent_pid,))
        if busy:
            return {'status': 'busy', 'details': busy}
        work = assert_no_reparse(root / 'runtime/updates/transaction')
        if not _recover(root, work, dry_run=True):
            raise ValueError('no interrupted transaction')
        import uuid
        log = assert_no_reparse(root / 'runtime/updates' / ('recover-' + uuid.uuid4().hex + '.log'))
        helper = assert_no_reparse(Path(__file__).absolute())
        argv = [sys.executable, str(helper), 'recover', '--root', str(root),
                '--parent-pid', str(parent_pid), '--edition', edition,
                '--parent-identity', json.dumps(parent, ensure_ascii=False)]
        if restart:
            argv.append('--restart')
        with open(log, 'xb') as output:
            child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                     close_fds=True, cwd=root,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return {'status': 'launched', 'pid': child.pid, 'log': str(log)}
    except Exception as e:
        return {'status': 'error', 'reason': str(e)}


def recovery_helper(root, edition, parent_pid, restart=False, *, parent_identity=None, busy_finder=None):
    try:
        root = assert_no_reparse(root)
        _wait_parent(parent_pid, root, 120, parent_identity)
        result = recover_update(root, busy_finder=busy_finder)
        if restart and result['status'] == 'applied':
            result = _restart_result(result, root, edition)
        return result
    except Exception as e:
        return {'status': 'error', 'reason': str(e)}


def main(argv=None):
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    probe = commands.add_parser('probe')
    probe.add_argument('--root', required=True)
    probe.add_argument('--ignore-pid', action='append', type=int, default=[])
    apply = commands.add_parser('apply')
    apply.add_argument('--root', required=True)
    apply.add_argument('--stage', required=True)
    apply.add_argument('--edition', choices=['CN', 'EN'], required=True)
    apply.add_argument('--parent-pid', type=int)
    apply.add_argument('--restart', action='store_true')
    apply.add_argument('--parent-identity', help=argparse.SUPPRESS)
    launch = commands.add_parser('launch')
    launch.add_argument('--root', required=True)
    launch.add_argument('--stage', required=True)
    launch.add_argument('--edition', choices=['CN', 'EN'], required=True)
    launch.add_argument('--parent-pid', required=True, type=int)
    launch.add_argument('--restart', action='store_true')
    launch_recover = commands.add_parser('launch-recover')
    launch_recover.add_argument('--root', required=True)
    launch_recover.add_argument('--parent-pid', required=True, type=int)
    launch_recover.add_argument('--restart', action='store_true')
    recover = commands.add_parser('recover')
    recover.add_argument('--root', required=True)
    recover.add_argument('--edition', required=True, choices=['CN', 'EN'])
    recover.add_argument('--parent-pid', required=True, type=int)
    recover.add_argument('--parent-identity', help=argparse.SUPPRESS)
    recover.add_argument('--restart', action='store_true')
    args = parser.parse_args(argv)
    if args.command == 'probe':
        try:
            diagnostics = []
            busy = find_busy(args.root, args.ignore_pid, diagnostics=diagnostics)
            result = {'status': ('unknown' if any(x.get('reason') == 'unknown' for x in busy) else 'busy') if busy else 'idle',
                      'busy': busy, 'details': busy, 'diagnostics': diagnostics, 'probe': True}
            if not busy:
                root = assert_no_reparse(args.root)
                journal = assert_no_reparse(root / 'runtime/updates/transaction/journal.json')
                if journal.exists() and _json(journal.read_bytes()).get('state') not in ('committed', 'rolled_back'):
                    result['status'] = 'recovery_required'
        except Exception as e:
            result = {'status': 'error', 'reason': str(e)}
    elif args.command == 'launch':
        result = launch_update(args.root, args.stage, args.edition, args.parent_pid, args.restart)
    elif args.command == 'launch-recover':
        result = launch_recovery(args.root, args.parent_pid, args.restart)
    elif args.command == 'recover':
        try:
            identity = _json(args.parent_identity) if args.parent_identity else None
            result = recovery_helper(args.root, args.edition, args.parent_pid, args.restart,
                                     parent_identity=identity)
        except Exception as e:
            result = {'status': 'error', 'reason': str(e)}
    else:
        try:
            identity = _json(args.parent_identity) if args.parent_identity else None
            result = apply_update(args.root, args.stage, args.edition, args.parent_pid, args.restart,
                                  parent_identity=identity)
        except Exception as e:
            result = {'status': 'error', 'reason': str(e)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['status'] in ('applied', 'idle', 'launched') else 1


if __name__ == '__main__':
    sys.exit(main())
