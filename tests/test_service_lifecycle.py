"""Offline failure-injection tests for service and game process lifecycle."""
import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import room_gateway
import service_runner
import taptap_private_runner


POWERSHELL = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'


def powershell_function(source, name):
    match = re.search(rf'(?ms)^function {re.escape(name)}\b.*?^}}', source)
    if not match:
        raise AssertionError(f'Missing PowerShell function {name}')
    return match.group(0)


class ServiceRunnerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.stdout = self.root / 'client-stdout.log'
        self.stderr = self.root / 'client-stderr.log'
        self.executable = self.root / 'profiles/solo/client/launcher.exe'

    def quicksdk(self, worker):
        token = 'deadbeef'
        with patch.dict(sys.modules, {'frida': types.ModuleType('frida')}), \
             patch('taptap_private_runner.validated_config', return_value={}), \
             patch('service_runner.uuid.uuid4', return_value=types.SimpleNamespace(hex=token)), \
             patch.object(service_runner.subprocess, 'Popen', return_value=worker):
            return service_runner.launch_quicksdk(
                self.executable, self.root, self.stdout, self.stderr,
                {'LOLO_DISPATCH_HOST': '127.0.0.1', 'LOLO_DISPATCH_PORT': '18080', 'LOLO_SDK_UID': '1000000'},
            )

    def test_worker_start_failure_is_nonzero_and_preserves_evidence(self):
        with patch.dict(sys.modules, {'frida': types.ModuleType('frida')}), \
             patch('taptap_private_runner.validated_config', return_value={}), \
             patch('service_runner.uuid.uuid4', return_value=types.SimpleNamespace(hex='deadbeef')), \
             patch.object(service_runner.subprocess, 'Popen', side_effect=OSError('synthetic spawn failure')):
            with self.assertRaisesRegex(OSError, 'synthetic spawn failure'):
                service_runner.launch_quicksdk(
                    self.executable, self.root, self.stdout, self.stderr,
                    {'LOLO_DISPATCH_HOST': '127.0.0.1', 'LOLO_DISPATCH_PORT': '18080', 'LOLO_SDK_UID': '1000000'},
                )
        evidence = json.loads((self.root / 'quicksdk-failure-deadbeef.json').read_text(encoding='utf-8'))
        self.assertIn('synthetic spawn failure', evidence['error'])
        self.assertIsNone(evidence['workerPid'])
        self.assertFalse((self.root / 'quicksdk-start-deadbeef.json').exists())
        self.assertFalse((self.root / 'quicksdk-pid-deadbeef.json').exists())

    def test_partial_readiness_files_are_retried_until_service_is_ready(self):
        class Worker:
            pid = 10
            terminated = False
            def poll(self): return None
            def terminate(self): self.terminated = True
            def wait(self, timeout=None): return 0

        worker = Worker()
        record = {'pid': 20, 'path': str(self.executable), 'startedTicks': 123, 'Started': '2026-01-01T00:00:00.000000Z'}
        result = dict(record, ready=True)
        pid_calls = 0
        def read_json(path):
            nonlocal pid_calls
            if Path(path).name.startswith('quicksdk-pid-'):
                pid_calls += 1
                return None if pid_calls == 1 else record
            return None if pid_calls < 3 else result

        with patch.object(service_runner, '_read_json', side_effect=read_json), \
             patch.object(service_runner.time, 'sleep'), \
             patch.object(service_runner, 'process_record') as verify:
            self.assertEqual(self.quicksdk(worker), 20)
        verify.assert_called_once_with(20, str(self.executable.resolve()), 123)
        self.assertFalse(worker.terminated)

    def test_quicksdk_crash_after_ready_is_not_reported_as_success(self):
        class Worker:
            pid = 10
            returncode = 9
            def poll(self): return 9
            def terminate(self): self.fail('exited worker must not be terminated again')
            def wait(self, timeout=None): return 9

        record = {'pid': 20, 'path': str(self.executable), 'startedTicks': 123}
        (self.root / 'quicksdk-pid-deadbeef.json').write_text(json.dumps(record), encoding='utf-8')
        (self.root / 'quicksdk-start-deadbeef.json').write_text(json.dumps(dict(record, ready=True)), encoding='utf-8')
        with patch.object(service_runner, 'process_record'), \
             patch.object(service_runner.time, 'sleep'), \
             patch.object(service_runner, 'terminate_process_tree') as terminate:
            with self.assertRaisesRegex(RuntimeError, 'startup stabilization'):
                self.quicksdk(Worker())
        terminate.assert_called_once_with(record)
        evidence = json.loads((self.root / 'quicksdk-failure-deadbeef.json').read_text(encoding='utf-8'))
        self.assertEqual(evidence['process']['pid'], 20)

    def test_pid_only_failure_record_is_not_kill_authority(self):
        class Worker:
            pid = 10
            def poll(self): return None
            def terminate(self): pass
            def wait(self, timeout=None): return 0

        (self.root / 'quicksdk-start-deadbeef.json').write_text('{"ready":false}', encoding='utf-8')
        (self.root / 'quicksdk-pid-deadbeef.json').write_text('{"pid":4242}', encoding='utf-8')
        with patch.object(service_runner, 'terminate_process_tree') as terminate, \
             patch.object(service_runner.subprocess, 'run') as run:
            with self.assertRaises(RuntimeError):
                self.quicksdk(Worker())
        terminate.assert_not_called()
        run.assert_not_called()
        evidence = json.loads((self.root / 'quicksdk-failure-deadbeef.json').read_text(encoding='utf-8'))
        self.assertEqual(evidence['unverifiedPid'], 4242)

    def test_direct_spawn_crash_fails_instead_of_reporting_a_pid(self):
        class Process:
            pid = 123
            returncode = 7
            def poll(self): return 7

        with patch.object(service_runner.subprocess, 'Popen', return_value=Process()), \
             patch.object(service_runner, 'process_record') as verify:
            with self.assertRaisesRegex(RuntimeError, 'exited during startup'):
                service_runner.launch('server.exe', [], self.root, self.stdout, self.stderr)
        verify.assert_not_called()

    def test_service_pid_file_write_failure_cleans_verified_process(self):
        class Process:
            pid = 123
            def poll(self): return None
            def terminate(self): self.fail('verified tree cleanup must be used')
            def wait(self, timeout=None): return 0

        record = {'pid': 123, 'path': str((self.root / 'server.exe').resolve()), 'startedTicks': 456}
        pid_path = self.root / 'runtime/owned-solo-server.json'
        with patch.object(service_runner.subprocess, 'Popen', return_value=Process()), \
             patch.object(service_runner, 'process_record', return_value=record), \
             patch.object(service_runner, 'terminate_process_tree') as terminate, \
             patch('profile_manager.write_json', side_effect=OSError('synthetic PID write failure')):
            with self.assertRaisesRegex(RuntimeError, 'synthetic PID write failure'):
                service_runner.launch(self.root / 'server.exe', [], self.root, self.stdout, self.stderr, pid_path=pid_path)
        terminate.assert_called_once_with(record)
        self.assertFalse(pid_path.exists())
        self.assertFalse(Path(str(pid_path) + '.tmp').exists())

    def test_cleanup_treats_process_exit_race_as_success(self):
        record = {'pid': 77, 'path': str(self.executable), 'startedTicks': 123}
        with patch.object(service_runner, '_record_running', return_value=True), \
             patch.object(service_runner, '_open_verified_process', side_effect=ProcessLookupError('exited')):
            service_runner.terminate_process_tree(record)

    def test_verified_tree_is_killed_child_first(self):
        root = {'pid': 1, 'path': r'C:\task\server.exe', 'startedTicks': 100}
        child = {'pid': 2, 'path': r'C:\task\child.exe', 'startedTicks': 200}
        terminated = []
        waited = set()
        def open_verified(record):
            self.assertEqual(record, root)
            return 11, dict(root, Started='root')
        def open_process(pid, access):
            self.assertEqual(pid, 2)
            return 22
        def identity(handle):
            return (child['path'], child['startedTicks']) if handle == 22 else (root['path'], root['startedTicks'])
        def children(pid): return [2] if pid == 1 else []
        def wait(handle, timeout):
            if timeout == 0:
                return service_runner._WAIT_TIMEOUT if handle not in waited else 0
            return 0
        def terminate(handle):
            terminated.append(handle)
            waited.add(handle)
        with patch.object(service_runner, '_record_running', return_value=True), \
             patch.object(service_runner, '_open_verified_process', side_effect=open_verified), \
             patch.object(service_runner, '_open_process', side_effect=open_process), \
             patch.object(service_runner, '_identity_from_handle', side_effect=identity), \
             patch.object(service_runner, '_child_pids', side_effect=children), \
             patch.object(service_runner, '_wait_for_exit', side_effect=wait), \
             patch.object(service_runner, '_terminate_handle', side_effect=terminate), \
             patch.object(service_runner, '_close_process'):
            service_runner.terminate_process_tree(root)
        self.assertEqual(terminated, [22, 11])

    def test_unverified_descendant_fails_closed_before_any_kill(self):
        root = {'pid': 1, 'path': r'C:\task\server.exe', 'startedTicks': 100}
        with patch.object(service_runner, '_record_running', return_value=True), \
             patch.object(service_runner, '_open_verified_process', return_value=(11, dict(root, Started='root'))), \
             patch.object(service_runner, '_open_process', return_value=22), \
             patch.object(service_runner, '_identity_from_handle', side_effect=OSError('access denied')), \
             patch.object(service_runner, '_child_pids', return_value=[2]), \
             patch.object(service_runner, '_terminate_handle') as terminate, \
             patch.object(service_runner, '_close_process'):
            with self.assertRaises(service_runner.ProcessCleanupError):
                service_runner.terminate_process_tree(root)
        terminate.assert_not_called()

    def test_recycled_root_pid_is_a_safe_noop(self):
        with patch.object(service_runner, '_record_running', return_value=False), \
             patch.object(service_runner, '_open_verified_process') as opened:
            service_runner.terminate_process_tree({'pid': 1, 'path': 'user.exe', 'startedTicks': 5})
        opened.assert_not_called()


class PrivateRunnerFailureTests(unittest.TestCase):
    def test_pid_file_write_failure_cleans_verified_spawn(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = root / 'profiles/solo/client'
            client.mkdir(parents=True)
            record = {'pid': 20, 'path': str(client / 'launcher.exe'), 'startedTicks': 123}
            profile = client.parent
            device = Mock()
            device.spawn.return_value = 20
            frida = types.ModuleType('frida')
            frida.get_local_device = Mock(return_value=device)
            config = {'host': '127.0.0.1', 'port': 18080, 'uid': 1000000}
            from profile_manager import write_json as real_write_json
            def fail_pid_write(path, value):
                if Path(path).name == 'pid.json':
                    raise OSError('synthetic PID write failure')
                return real_write_json(path, value)
            with patch.dict(sys.modules, {'frida': frida}), \
                 patch.object(taptap_private_runner, 'validated_config', return_value=config), \
                 patch.object(taptap_private_runner, '_validated_cache', return_value=client), \
                 patch.object(service_runner, 'process_record', return_value=record), \
                 patch.object(service_runner, 'terminate_process_tree') as terminate, \
                 patch('profile_manager.write_json', side_effect=fail_pid_write):
                with self.assertRaisesRegex(OSError, 'synthetic PID write failure'):
                    taptap_private_runner.run(client, '127.0.0.1', 18080, 1000000,
                                              profile / 'logs/adapter.jsonl', profile / 'result.json', profile / 'pid.json')
            terminate.assert_called_once_with(record)
            self.assertFalse((profile / 'pid.json').exists())
            self.assertFalse(json.loads((profile / 'result.json').read_text(encoding='utf-8'))['ready'])
            device.kill.assert_not_called()


class RoomGatewayLifecycleTests(unittest.TestCase):
    def test_corrupt_admin_key_is_quarantined_and_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory)
            (profile / 'data').mkdir()
            (profile / 'data/Constant.json').write_text('{}', encoding='utf-8')
            (profile / 'admin-key.txt').write_text('', encoding='ascii')
            loop = asyncio.new_event_loop()
            try:
                manager = room_gateway.Manager(profile, loop)
            finally:
                loop.close()
            self.assertRegex(manager.key, r'^[0-9a-f]{64}$')
            self.assertEqual(len(list(profile.glob('admin-key.corrupt-*.txt'))), 1)

    def test_gateway_bind_failure_closes_admin_listener(self):
        http = Mock()
        thread = Mock()
        args = types.SimpleNamespace(profile=Path('unused'), admin_port=18300, backend_port=18101, host='127.0.0.1', port=18100)
        with patch.object(room_gateway, 'Manager', return_value=Mock()), \
             patch.object(room_gateway, 'ThreadingHTTPServer', return_value=http), \
             patch.object(room_gateway.threading, 'Thread', return_value=thread), \
             patch.object(room_gateway.asyncio, 'start_server', new=AsyncMock(side_effect=OSError('address in use'))):
            with self.assertRaisesRegex(OSError, 'address in use'):
                asyncio.run(room_gateway.main(args))
        http.shutdown.assert_called_once_with()
        http.server_close.assert_called_once_with()
        thread.join.assert_called_once_with(timeout=5)


class PowerShellLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not POWERSHELL.is_file():
            raise unittest.SkipTest('Native PowerShell is unavailable')
        cls.world = (REPO / 'World-Control.ps1').read_text(encoding='utf-8-sig')

    def run_script(self, body):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'lifecycle-test.ps1'
            script.write_text("$ErrorActionPreference='Stop'\n" + body, encoding='utf-8-sig')
            return subprocess.run([str(POWERSHELL), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                                  capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)

    def test_corrupt_state_recovers_backup_and_retains_primary(self):
        functions = '\n'.join(powershell_function(self.world, name) for name in ('Read-StateFile', 'Preserve-CorruptState', 'Read-State', 'Save-State'))
        body = functions + r'''
$taskRoot=$env:LIFECYCLE_TEST_ROOT
$taskStateFile=Join-Path $taskRoot 'runtime\world-state-solo.json'
$taskLegacyStateFile=Join-Path $taskRoot 'runtime\world-state.json'
$Mode='solo'
[IO.Directory]::CreateDirectory((Split-Path -Parent $taskStateFile))|Out-Null
[IO.File]::WriteAllText($taskStateFile,'{broken')
[IO.File]::WriteAllText("$taskStateFile.bak",'{"Active":true,"Mode":"solo","Target":"127.0.0.1"}')
$taskState=Read-State
if (-not $taskState.Active -or $taskState.Mode -ne 'solo') { throw 'backup was not recovered' }
if (-not (Test-Path -LiteralPath $taskStateFile)) { throw 'primary was not restored' }
if (@(Get-ChildItem (Split-Path -Parent $taskStateFile) -Filter 'world-state-solo.json.corrupt-*.json').Count -ne 1) { throw 'corrupt evidence missing' }
'''
        with tempfile.TemporaryDirectory() as directory:
            environment = dict(os.environ, LIFECYCLE_TEST_ROOT=directory)
            result = subprocess.run([str(POWERSHELL), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', '-'],
                                    input="$ErrorActionPreference='Stop'\n" + body, capture_output=True, text=True,
                                    encoding='utf-8', errors='replace', timeout=30, env=environment)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_repeated_stop_only_stops_matching_identity_once(self):
        names = ('Test-OwnedRecord', 'Process-Matches', 'Stop-Descendants', 'Stop-Recorded')
        functions = '\n'.join(powershell_function(self.world, name) for name in names)
        body = functions + r'''
$script:processes=@{};$script:stopped=@();$script:cim=@()
function Get-Process { param([int]$Id) if($script:processes.ContainsKey($Id)){return $script:processes[$Id]};throw 'gone' }
function Get-CimInstance { param([string]$ClassName) return @($script:cim|Where-Object{$script:processes.ContainsKey([int]$_.ProcessId)}) }
function Stop-Process { param([int]$Id,[switch]$Force) $script:stopped+=$Id;$script:processes.Remove($Id) }
$taskPath='C:\task\server.exe';$taskStarted=[DateTime]::UtcNow
$taskProcess=[pscustomobject]@{Id=41;Path=$taskPath;StartTime=$taskStarted}
$taskProcess|Add-Member ScriptMethod WaitForExit { param([int]$Milliseconds) return $true }
$script:processes[41]=$taskProcess
$script:cim=@([pscustomobject]@{ProcessId=41;ParentProcessId=0})
$taskRecord=[pscustomobject]@{Id=41;Path=$taskPath;Started=$taskStarted.ToUniversalTime().ToString('o');Owned=$true}
Stop-Recorded $taskRecord $taskPath
Stop-Recorded $taskRecord $taskPath
if($script:stopped.Count -ne 1){throw 'repeated stop was not idempotent'}
$taskProcess2=[pscustomobject]@{Id=42;Path='C:\user\unrelated.exe';StartTime=$taskStarted}
$taskProcess2|Add-Member ScriptMethod WaitForExit { param([int]$Milliseconds) return $true }
$script:processes[42]=$taskProcess2
$taskRecord2=[pscustomobject]@{Id=42;Path='C:\user\unrelated.exe';Started=$taskStarted.ToUniversalTime().ToString('o');Owned=$true}
Stop-Recorded $taskRecord2 $taskPath
if($script:stopped -contains 42){throw 'path mismatch process was killed'}
'''
        result = self.run_script(body)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_process_tree_stops_verified_child_before_root(self):
        names = ('Test-OwnedRecord', 'Process-Matches', 'Stop-Descendants', 'Stop-Recorded')
        functions = '\n'.join(powershell_function(self.world, name) for name in names)
        body = functions + r'''
$script:processes=@{};$script:stopped=@();$taskStarted=[DateTime]::UtcNow
function Get-Process { param([int]$Id) if($script:processes.ContainsKey($Id)){return $script:processes[$Id]};throw 'gone' }
function Get-CimInstance { param([string]$ClassName) return @($script:cim|Where-Object{$script:processes.ContainsKey([int]$_.ProcessId)}) }
function Stop-Process { param([int]$Id,[switch]$Force) $script:stopped+=$Id;$script:processes.Remove($Id) }
foreach($taskId in 51,52){$taskProcess=[pscustomobject]@{Id=$taskId;Path="C:\task\p$taskId.exe";StartTime=$taskStarted};$taskProcess|Add-Member ScriptMethod WaitForExit {param([int]$Milliseconds)return $true};$script:processes[$taskId]=$taskProcess}
$script:cim=@([pscustomobject]@{ProcessId=51;ParentProcessId=0},[pscustomobject]@{ProcessId=52;ParentProcessId=51})
$taskRecord=[pscustomobject]@{Id=51;Path='C:\task\p51.exe';Started=$taskStarted.ToUniversalTime().ToString('o');Owned=$true}
Stop-Recorded $taskRecord 'C:\task\p51.exe'
if(($script:stopped -join ',') -ne '52,51'){throw "wrong tree order: $($script:stopped -join ',')"}
'''
        result = self.run_script(body)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_unverified_child_prevents_root_termination(self):
        names = ('Test-OwnedRecord', 'Process-Matches', 'Stop-Descendants', 'Stop-Recorded')
        functions = '\n'.join(powershell_function(self.world, name) for name in names)
        body = functions + r'''
$script:processes=@{};$script:stopped=@();$taskStarted=[DateTime]::UtcNow
function Get-Process { param([int]$Id) if($script:processes.ContainsKey($Id)){return $script:processes[$Id]};throw 'gone' }
function Get-CimInstance { param([string]$ClassName) return @($script:cim|Where-Object{$script:processes.ContainsKey([int]$_.ProcessId)}) }
function Stop-Process { param([int]$Id,[switch]$Force) $script:stopped+=$Id;$script:processes.Remove($Id) }
$taskRoot=[pscustomobject]@{Id=61;Path='C:\task\root.exe';StartTime=$taskStarted};$taskRoot|Add-Member ScriptMethod WaitForExit {param([int]$Milliseconds)return $true}
$taskChild=[pscustomobject]@{Id=62;Path=$null;StartTime=$taskStarted};$taskChild|Add-Member ScriptMethod WaitForExit {param([int]$Milliseconds)return $true}
$script:processes[61]=$taskRoot;$script:processes[62]=$taskChild
$script:cim=@([pscustomobject]@{ProcessId=61;ParentProcessId=0},[pscustomobject]@{ProcessId=62;ParentProcessId=61})
$taskRecord=[pscustomobject]@{Id=61;Path='C:\task\root.exe';Started=$taskStarted.ToUniversalTime().ToString('o');Owned=$true}
$taskFailed=$false;try{Stop-Recorded $taskRecord 'C:\task\root.exe'}catch{$taskFailed=$true}
if(-not $taskFailed -or $script:stopped.Count -ne 0){throw 'unverified child did not fail closed'}
'''
        result = self.run_script(body)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_launch_record_recovers_process_lost_before_state_save(self):
        names = ('Test-OwnedRecord', 'Process-Matches', 'Preserve-CorruptState', 'Read-LaunchRecord', 'Recover-LaunchRecords')
        functions = '\n'.join(powershell_function(self.world, name) for name in names)
        body = functions + r'''
$Mode='solo';$taskExecutable='C:\task\server.exe';$taskPython='C:\task\python.exe'
function Stop-Recorded { param($Record,[string]$ExpectedPath) if($ExpectedPath -ne 'C:\task\server.exe'){throw 'wrong expected path'};$script:stopped+=$Record.Id }
$script:stopped=@();[IO.Directory]::CreateDirectory((Join-Path $PSScriptRoot 'runtime'))|Out-Null
$taskPath=Join-Path $PSScriptRoot 'runtime\owned-solo-server.json'
@{id=71;path='C:\task\server.exe';started='2026-01-01T00:00:00.000000Z'} | ConvertTo-Json | Set-Content -LiteralPath $taskPath -Encoding UTF8
Recover-LaunchRecords $null
if(($script:stopped -join ',') -ne '71'){throw 'launch record was not recovered'}
if(Test-Path -LiteralPath $taskPath){throw 'recovered launch record was not removed'}
'''
        result = self.run_script(body)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_delayed_service_port_waits_and_foreign_owner_fails(self):
        functions = '\n'.join(powershell_function(self.world, name) for name in ('Process-Matches', 'Test-PortOwner', 'Wait-Ready'))
        body = functions + r'''
function Process-Matches { param($Record,[string]$ExpectedPath) return $true }
function Test-Port { param([string]$Address,[int]$Port) $script:probes++;return $script:probes -ge 3 }
function Test-PortOwner { param([string]$Address,[int]$Port,$Record) return $true }
function Start-Sleep { param([int]$Milliseconds) $script:sleeps++ }
$script:probes=0;$script:sleeps=0
Wait-Ready '127.0.0.1' 18080 @{Id=1} 5
if($script:probes -ne 3 -or $script:sleeps -ne 2){throw 'delayed port was not retried'}
$script:probes=0;$script:sleeps=0
function Test-Port { param([string]$Address,[int]$Port) return $true }
function Test-PortOwner { param([string]$Address,[int]$Port,$Record) return $false }
$taskFailed=$false;try{Wait-Ready '127.0.0.1' 18080 @{Id=1} 2}catch{$taskFailed=$true}
if(-not $taskFailed){throw 'foreign listener was accepted'}
'''
        result = self.run_script(body)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_stop_with_corrupt_state_returns_nonzero_and_keeps_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'World-Control.ps1'
            script.write_text(self.world, encoding='utf-8-sig')
            runtime = root / 'runtime'
            runtime.mkdir()
            state = runtime / 'world-state-solo.json'
            state.write_text('{broken', encoding='utf-8')
            result = subprocess.run([str(POWERSHELL), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script), '-Action', 'Stop', '-Mode', 'solo'],
                                    capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(list(runtime.glob('world-state-solo.json.corrupt-*.json')))
            self.assertTrue(list(runtime.glob('lifecycle-error-solo-*.json')))

    def test_firewall_rules_are_allow_only_and_host_port_scoped(self):
        source = (REPO / 'Lan-Firewall.ps1').read_text(encoding='utf-8-sig')
        self.assertIn("-Action Allow", source)
        self.assertIn("-LocalPort 19080,19200", source)
        self.assertIn("-LocalPort 19100", source)
        self.assertIn("profiles\\host\\server\\Lolo.exe", source)
        self.assertNotIn('-Action Block', source)
        self.assertNotIn('18080', source)
        self.assertNotIn('20080', source)
        self.assertIn('Remove-CreatedRules', source)
        self.assertIn('Write-FirewallResult $false', source)


if __name__ == '__main__':
    unittest.main()
