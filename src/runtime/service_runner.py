"""Launch and clean up processes without ever trusting a recycled PID.

The controller records PID, executable path and creation time.  Cleanup repeats
that check and, on Windows, keeps verified process handles open while walking
and terminating the owned process tree.
"""
import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

_SELF_DIR = str(Path(__file__).resolve().parent)
if _SELF_DIR not in sys.path:
    sys.path.insert(0, _SELF_DIR)


_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_TERMINATE = 0x0001
_SYNCHRONIZE = 0x00100000
_STILL_ACTIVE = 259
_TH32CS_SNAPPROCESS = 0x00000002
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_WAIT_TIMEOUT = 258


class _ProcessEntry(ctypes.Structure):
    _fields_ = [
        ('dwSize', wintypes.DWORD),
        ('cntUsage', wintypes.DWORD),
        ('th32ProcessID', wintypes.DWORD),
        ('th32DefaultHeapID', ctypes.POINTER(ctypes.c_size_t)),
        ('th32ModuleID', wintypes.DWORD),
        ('cntThreads', wintypes.DWORD),
        ('th32ParentProcessID', wintypes.DWORD),
        ('pcPriClassBase', wintypes.LONG),
        ('dwFlags', wintypes.DWORD),
        ('szExeFile', wintypes.WCHAR * 260),
    ]


class ProcessCleanupError(RuntimeError):
    def __init__(self, message, records=()):
        super().__init__(message)
        self.records = list(records)


def _same_path(first, second):
    return os.path.normcase(os.path.abspath(str(first))) == os.path.normcase(os.path.abspath(str(second)))


def _started_iso(value):
    stamp = datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=int(value) // 10)
    return stamp.isoformat(timespec='microseconds').replace('+00:00', 'Z')


def _open_process(pid, access):
    if os.name != 'nt':
        raise RuntimeError('Verified process cleanup requires Windows.')
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(access, False, int(pid))
    if not handle:
        code = ctypes.get_last_error()
        if code == 87:  # ERROR_INVALID_PARAMETER: the PID no longer exists.
            raise ProcessLookupError(f'Process {pid} no longer exists.')
        raise OSError(code, f'Could not open process {pid}')
    return handle


def _close_process(handle):
    if handle:
        ctypes.WinDLL('kernel32', use_last_error=True).CloseHandle(handle)


def _identity_from_handle(handle):
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    size = wintypes.DWORD(32768)
    buffer = ctypes.create_unicode_buffer(size.value)
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
        raise OSError(ctypes.get_last_error(), 'Could not verify process executable path.')
    creation = wintypes.FILETIME()
    exit_time = wintypes.FILETIME()
    kernel_time = wintypes.FILETIME()
    user_time = wintypes.FILETIME()
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME)]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    if not kernel32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time), ctypes.byref(kernel_time), ctypes.byref(user_time)):
        raise OSError(ctypes.get_last_error(), 'Could not verify process creation time.')
    started = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
    return buffer.value, started


def process_record(pid, expected_path=None, expected_started=None):
    """Return an identity for a live process, or fail without touching it."""
    pid = int(pid)
    if pid <= 0:
        raise ValueError('A positive PID is required.')
    handle = _open_process(pid, _PROCESS_QUERY_LIMITED_INFORMATION)
    try:
        path, started = _identity_from_handle(handle)
    finally:
        _close_process(handle)
    if expected_path is not None and not _same_path(path, expected_path):
        raise RuntimeError(f'Process {pid} executable path does not match the owned process.')
    if expected_started is not None and int(started) != int(expected_started):
        raise RuntimeError(f'Process {pid} creation time does not match the owned process.')
    # PowerShell parses JSON keys case-insensitively, so the numeric creation
    # time and the ISO string must not differ only by case. 'startedTicks' is
    # the identity check value; 'Started' is the display/recovery string.
    return {
        'pid': pid,
        'id': pid,
        'path': path,
        'startedTicks': int(started),
        'Started': _started_iso(started),
    }


def _open_verified_process(record):
    if not isinstance(record, dict) or not isinstance(record.get('pid'), int):
        raise ValueError('A complete PID record is required for cleanup.')
    if not record.get('path') or not isinstance(record.get('startedTicks'), int):
        raise ValueError('Refusing to clean up a process without path and creation time.')
    handle = _open_process(record['pid'], _PROCESS_QUERY_LIMITED_INFORMATION | _PROCESS_TERMINATE | _SYNCHRONIZE)
    try:
        identity = process_record(record['pid'], record['path'], record['startedTicks'])
    except Exception:
        exited = _wait_for_exit(handle, 0) != _WAIT_TIMEOUT
        _close_process(handle)
        if exited:
            raise ProcessLookupError(f'Process {record["pid"]} exited before cleanup.')
        raise
    # Query the already-open handle too.  The PID cannot be recycled while this
    # handle is retained, so subsequent termination addresses the same object.
    try:
        path, started = _identity_from_handle(handle)
    except Exception:
        exited = _wait_for_exit(handle, 0) != _WAIT_TIMEOUT
        _close_process(handle)
        if exited:
            raise ProcessLookupError(f'Process {record["pid"]} exited before cleanup.')
        raise
    if not _same_path(path, record['path']) or int(started) != int(record['startedTicks']):
        _close_process(handle)
        raise RuntimeError('Process identity changed while it was being opened.')
    return handle, identity


def _child_pids(parent_pid):
    if os.name != 'nt':
        return []
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snapshot == _INVALID_HANDLE_VALUE:
        raise OSError(ctypes.get_last_error(), 'Could not enumerate child processes.')
    children = []
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
        kernel32.Process32FirstW.restype = wintypes.BOOL
        kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
        kernel32.Process32NextW.restype = wintypes.BOOL
        valid = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while valid:
            if entry.th32ParentProcessID == int(parent_pid):
                children.append(int(entry.th32ProcessID))
            valid = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return children


def _wait_for_exit(handle, milliseconds):
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    return kernel32.WaitForSingleObject(handle, milliseconds)


def _terminate_handle(handle):
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    if not kernel32.TerminateProcess(handle, 1):
        raise OSError(ctypes.get_last_error(), 'Could not terminate verified process.')


def _record_running(record):
    """Return False for an exited or recycled PID, without ever killing it."""
    try:
        handle = _open_process(record['pid'], _PROCESS_QUERY_LIMITED_INFORMATION)
    except ProcessLookupError:
        return False
    try:
        path, started = _identity_from_handle(handle)
        if not _same_path(path, record['path']) or int(started) != int(record['startedTicks']):
            return False
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise OSError(ctypes.get_last_error(), 'Could not verify process liveness.')
        return code.value == _STILL_ACTIVE
    finally:
        _close_process(handle)


def terminate_process_tree(record):
    """Terminate only the verified root and its verified descendants.

    All handles and identities are collected before the first termination.  A
    missing path, creation time or inaccessible descendant fails closed and
    leaves the tree untouched.
    """
    opened = []
    records = []
    errors = []
    if not _record_running(record):
        return
    try:
        root_handle, root_identity = _open_verified_process(record)
        opened.append(root_handle)
        records.append(root_identity)

        # Children need their own path/start identity.  Open them directly and
        # retain the handle before recursion; this also pins every parent PID.
        def collect_children(parent_handle, parent_identity, depth):
            if depth >= 64 or len(records) >= 1024:
                raise RuntimeError('Owned process tree is unexpectedly large.')
            for child_pid in _child_pids(parent_identity['pid']):
                child_handle = None
                try:
                    child_handle = _open_process(child_pid, _PROCESS_QUERY_LIMITED_INFORMATION | _PROCESS_TERMINATE | _SYNCHRONIZE)
                    path, started = _identity_from_handle(child_handle)
                    if not path:
                        raise RuntimeError(f'Could not verify child process {child_pid} path.')
                    child_identity = {'pid': child_pid, 'id': child_pid, 'path': path, 'startedTicks': int(started), 'Started': _started_iso(started)}
                    if _wait_for_exit(parent_handle, 0) != _WAIT_TIMEOUT:
                        raise RuntimeError('Owned parent exited during process-tree verification.')
                    opened.append(child_handle)
                    records.append(child_identity)
                    child_handle = None
                    collect_children(opened[-1], child_identity, depth + 1)
                except ProcessLookupError:
                    continue
                finally:
                    if child_handle is not None:
                        _close_process(child_handle)

        collect_children(root_handle, root_identity, 0)
        for handle, identity in reversed(list(zip(opened, records))):
            try:
                if _wait_for_exit(handle, 0) != _WAIT_TIMEOUT:
                    continue
                _terminate_handle(handle)
                if _wait_for_exit(handle, 5000) != 0:
                    raise TimeoutError(f'Timed out stopping process {identity["pid"]}.')
            except Exception as error:
                errors.append(f'{identity["path"]} (PID {identity["pid"]}): {error}')
    except ProcessLookupError:
        return
    except Exception as error:
        errors.append(str(error))
    finally:
        for handle in reversed(opened):
            _close_process(handle)
    if errors:
        raise ProcessCleanupError('Verified process cleanup failed: ' + '; '.join(errors), records)


def _read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _remove(path):
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def _stop_worker(worker):
    if worker.poll() is not None:
        return
    worker.terminate()
    try:
        worker.wait(timeout=5)
    except subprocess.TimeoutExpired:
        worker.kill()
        worker.wait()


def launch(executable, arguments, cwd, stdout_path, stderr_path, extra_env=None, visible=False, pid_path=None):
    from client_identity import classify
    executable = str(Path(executable).resolve())
    if visible and Path(executable).name.lower() == 'launcher.exe' and classify(Path(executable).parent) in ('quicksdk-pc', 'steam-quicksdk'):
        return launch_quicksdk(executable, cwd, stdout_path, stderr_path, extra_env or {})
    with open(stdout_path, 'ab', buffering=0) as output, open(stderr_path, 'ab', buffering=0) as error:
        process = subprocess.Popen(
            [executable, *arguments], cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=output, stderr=error, close_fds=True,
            creationflags=0 if visible else subprocess.CREATE_NO_WINDOW,
            env=dict(os.environ, **(extra_env or {})),
        )
    record = None
    try:
        if process.poll() is not None:
            raise RuntimeError(f'Service exited during startup with code {process.returncode}; see {stderr_path}.')
        record = process_record(process.pid, executable)
        if pid_path:
            from profile_manager import write_json
            try:
                write_json(pid_path, record)
            except Exception:
                _remove(str(pid_path) + '.tmp')
                raise
    except Exception as error:
        cleanup_errors = []
        if record is not None:
            try:
                terminate_process_tree(record)
            except Exception as cleanup_error:
                cleanup_errors.append(str(cleanup_error))
        elif process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if cleanup_errors:
            raise ProcessCleanupError(f'Could not verify the started service: {error}; cleanup failed: ' + '; '.join(cleanup_errors), [record]) from error
        raise RuntimeError(f'Could not verify the started service: {error}') from error
    return process.pid


def launch_quicksdk(executable, cwd, stdout_path, stderr_path, environment):
    import sys
    from taptap_private_runner import validated_config
    executable = str(Path(executable).resolve())
    token = uuid.uuid4().hex
    result_path = Path(stdout_path).with_name('quicksdk-start-' + token + '.json')
    pid_path = Path(stdout_path).with_name('quicksdk-pid-' + token + '.json')
    evidence_path = result_path.with_name('quicksdk-failure-' + token + '.json')
    worker = None
    ready_pid = None
    spawned_pid = None
    spawned_record = None
    cleanup_errors = []
    failure = None
    result = None
    try:
        host = environment.get('LOLO_DISPATCH_HOST', '')
        port = int(environment.get('LOLO_DISPATCH_PORT', '0'))
        uid = int(environment.get('LOLO_SDK_UID', '0'))
        validated_config(Path(executable).parent, host, port, uid)
        import frida  # fail before spawning if the portable adapter dependency is missing
        command = [sys.executable, '-X', 'utf8', str(Path(__file__).with_name('taptap_private_runner.py')),
                   '--client', str(Path(executable).parent), '--host', host, '--port', str(port),
                   '--uid', str(uid), '--log', str(Path(stdout_path).with_name('quicksdk-local.jsonl')),
                   '--result-path', str(result_path), '--pid-path', str(pid_path)]
        with open(stdout_path, 'ab', buffering=0) as out, open(stderr_path, 'ab', buffering=0) as err:
            worker = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                      close_fds=True, creationflags=subprocess.CREATE_NO_WINDOW,
                                      env=dict(os.environ, **environment))
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            pid_data = _read_json(pid_path)
            if pid_data:
                candidate = pid_data.get('pid')
                if isinstance(candidate, int):
                    spawned_pid = candidate
                    if isinstance(pid_data.get('path'), str) and isinstance(pid_data.get('startedTicks'), int):
                        spawned_record = pid_data
            result = _read_json(result_path)
            if result is not None:
                candidate = result.get('pid')
                if isinstance(candidate, int):
                    spawned_pid = candidate
                if result.get('ready') is True:
                    record = result if isinstance(result.get('path'), str) and isinstance(result.get('startedTicks'), int) else spawned_record
                    if not record or record.get('pid') != spawned_pid:
                        raise RuntimeError('QuickSDK readiness result has no matching verified process identity.')
                    process_record(spawned_pid, executable, record['startedTicks'])
                    # Do not report success for a worker/game pair that crashes
                    # immediately after publishing readiness.
                    time.sleep(.15)
                    if worker.poll() is not None:
                        raise RuntimeError(f'QuickSDK adapter exited during startup stabilization with code {worker.returncode}.')
                    ready_pid = spawned_pid
                    return ready_pid
                raise RuntimeError('QuickSDK local adapter did not become ready.')
            if worker.poll() is not None:
                raise RuntimeError(f'QuickSDK adapter exited with code {worker.returncode}; see private client logs.')
            time.sleep(.05)
        raise TimeoutError('QuickSDK adapter startup timed out.')
    except Exception as error:
        failure = error
        raise
    finally:
        # Capture evidence before the worker removes its transient PID file.
        pid_data = _read_json(pid_path)
        if pid_data:
            if isinstance(pid_data.get('path'), str) and isinstance(pid_data.get('startedTicks'), int):
                spawned_record = pid_data
            else:
                spawned_record = None
            if isinstance(pid_data.get('pid'), int):
                spawned_pid = pid_data['pid']
        # A worker that failed while cleaning up may have removed its PID file
        # but left a complete identity in the failure result. Retry that cleanup
        # only with the recorded path and creation time.
        if isinstance(result, dict):
            result_record = result.get('process')
            if isinstance(result_record, dict) and isinstance(result_record.get('path'), str) and isinstance(result_record.get('startedTicks'), int):
                spawned_record = result_record
                if isinstance(result_record.get('pid'), int):
                    spawned_pid = result_record['pid']
        if ready_pid is None:
            if worker is not None:
                try:
                    _stop_worker(worker)
                except Exception as error:
                    cleanup_errors.append(f'worker: {error}')
            if spawned_record and isinstance(spawned_record.get('path'), str) and isinstance(spawned_record.get('startedTicks'), int):
                try:
                    terminate_process_tree(spawned_record)
                except Exception as error:
                    cleanup_errors.append(f'client: {error}')
            if failure is not None:
                evidence = {
                    'error': str(failure), 'type': type(failure).__name__,
                    'workerPid': getattr(worker, 'pid', None) if worker is not None else None,
                    'process': spawned_record, 'unverifiedPid': spawned_pid if spawned_record is None else None,
                    'cleanupErrors': cleanup_errors, 'result': result,
                    'stdout': str(stdout_path), 'stderr': str(stderr_path),
                    'created': datetime.now(timezone.utc).isoformat(),
                }
                try:
                    from profile_manager import write_json
                    write_json(evidence_path, evidence)
                except Exception as error:
                    cleanup_errors.append(f'evidence: {error}')
        _remove(result_path)
        _remove(pid_path)
        if failure is not None and cleanup_errors:
            raise ProcessCleanupError(f'{failure}; cleanup failed: ' + '; '.join(cleanup_errors), [spawned_record] if spawned_record else []) from failure


def _main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executable', required=True)
    parser.add_argument('--cwd', required=True)
    parser.add_argument('--stdout', required=True)
    parser.add_argument('--stderr', required=True)
    parser.add_argument('--env', action='append', default=[])
    parser.add_argument('--visible', action='store_true')
    parser.add_argument('--pid-path')
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
    environment = dict(item.split('=', 1) for item in args.env)
    try:
        pid = launch(args.executable, command, args.cwd, args.stdout, args.stderr, environment, args.visible, args.pid_path)
        record = _read_json(args.pid_path) if args.pid_path and Path(args.pid_path).is_file() else None
        if not record:
            record = process_record(pid, args.executable)
            if args.pid_path:
                from profile_manager import write_json
                try:
                    write_json(args.pid_path, record)
                except Exception:
                    _remove(str(args.pid_path) + '.tmp')
                    terminate_process_tree(record)
                    raise
        print(json.dumps(record), flush=True)
    except Exception as error:
        print(json.dumps({'error': str(error)}), flush=True)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(_main())
