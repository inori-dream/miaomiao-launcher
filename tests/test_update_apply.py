"""Updater tests: synthetic installation roots only, no network or real processes."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import update_apply as apply
from update_policy import is_managed, validate_path, validate_files


def meta(data):
    return {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def idle(root, ignore_pids=()):
    return []


class Fixture:
    def __init__(self, root, edition='CN'):
        self.root = Path(root)
        self.edition = edition
        self.old = {'official_launcher.py': b'old launcher', 'docs/old.md': b'old doc'}
        for name, data in self.old.items():
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        self.baseline = json.dumps({'schemaVersion': 1, 'version': '1.0.0', 'edition': edition,
                                    'files': {k: meta(v) for k, v in self.old.items()}}).encode()
        (self.root / 'installation-manifest.json').write_bytes(self.baseline)
        for name in ('profiles/a/save.db', 'runtime/save.db', 'launcher.settings.json',
                     'client/game.pak', 'server-kit/data/Resource/config/a.json'):
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b'user data')
        self.stage = self.root / ('runtime/updates/staging/2.0.0-' + edition)
        self.stage.mkdir(parents=True)
        self.make_package()

    def make_package(self, members=None, signed=None, baseline_mutator=None):
        members = dict(members or {'official_launcher.py': b'new launcher', 'docs/new.md': b'new doc'})
        baseline = {'schemaVersion': 1, 'version': '2.0.0', 'edition': self.edition,
                    'files': {k: meta(v) for k, v in members.items()}}
        if baseline_mutator:
            baseline_mutator(baseline)
        members['installation-manifest.json'] = json.dumps(baseline).encode()
        with zipfile.ZipFile(self.stage / 'payload.zip', 'w', zipfile.ZIP_DEFLATED) as z:
            for name, data in members.items():
                z.writestr(name, data)
        data = (self.stage / 'payload.zip').read_bytes()
        files = signed or {k: meta(v) for k, v in members.items()}
        self.manifest = {'schemaVersion': 1, 'repository': 'inori-dream/miaomiao-launcher',
                         'version': '2.0.0', 'releaseTag': 'v2.0.0', 'channel': 'stable',
                         'minUpdaterVersion': 1,
                         'assets': {self.edition: {**meta(data), 'files': files}}}
        self.save_manifest()
        (self.stage / 'manifest.sig').write_bytes(b'fixture-signature')

    def save_manifest(self):
        (self.stage / 'manifest.json').write_text(json.dumps(self.manifest), encoding='utf8')

    def verifier(self, raw, sig):
        if sig != b'fixture-signature':
            raise ValueError('invalid signature')
        return json.loads(raw)

    def run(self, **kwargs):
        return apply.apply_update(self.root, self.stage, self.edition,
                                  verifier=self.verifier, busy_finder=idle, **kwargs)

    def assert_old(self, test):
        for name, data in self.old.items():
            test.assertEqual((self.root / name).read_bytes(), data)
        test.assertEqual((self.root / 'installation-manifest.json').read_bytes(), self.baseline)
        test.assertFalse((self.root / 'docs/new.md').exists())


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='synthetic-updater-')
        self.addCleanup(self.tmp.cleanup)
        self.f = Fixture(self.tmp.name)

    def test_apply_preserves_user_data_and_removes_old_managed(self):
        result = self.f.run()
        self.assertEqual(result['status'], 'applied', result)
        self.assertEqual((self.f.root / 'official_launcher.py').read_bytes(), b'new launcher')
        self.assertFalse((self.f.root / 'docs/old.md').exists())
        for name in ('profiles/a/save.db', 'runtime/save.db', 'launcher.settings.json',
                     'client/game.pak', 'server-kit/data/Resource/config/a.json'):
            self.assertEqual((self.f.root / name).read_bytes(), b'user data')

    def test_local_changes_block(self):
        (self.f.root / 'official_launcher.py').write_bytes(b'user edit')
        result = self.f.run()
        self.assertEqual(result['status'], 'conflict', result)
        self.assertEqual((self.f.root / 'official_launcher.py').read_bytes(), b'user edit')

    def test_missing_baseline_blocks(self):
        (self.f.root / 'installation-manifest.json').unlink()
        self.assertEqual(self.f.run()['status'], 'conflict')

    def test_preexisting_unmanaged_destination_blocks(self):
        (self.f.root / 'docs/new.md').write_bytes(b'user doc')
        self.assertEqual(self.f.run()['status'], 'conflict')

    def test_busy_blocks(self):
        r = apply.apply_update(self.f.root, self.f.stage, 'CN', verifier=self.f.verifier,
                               busy_finder=lambda *a, **k: [{'reason': 'unknown'}])
        self.assertEqual(r['status'], 'busy')
        self.f.assert_old(self)

    def test_signature_and_payload_reverified(self):
        (self.f.stage / 'manifest.sig').write_bytes(b'bad')
        self.assertEqual(self.f.run()['status'], 'error')
        (self.f.stage / 'manifest.sig').write_bytes(b'fixture-signature')
        with open(self.f.stage / 'payload.zip', 'ab') as out:
            out.write(b'tampered')
        self.assertEqual(self.f.run()['status'], 'error')
        self.f.assert_old(self)

    def test_member_hash_reverified(self):
        self.f.manifest['assets']['CN']['files']['official_launcher.py']['sha256'] = '0' * 64
        self.f.save_manifest()
        self.assertEqual(self.f.run()['status'], 'error')
        self.f.assert_old(self)

    def test_baseline_mismatch(self):
        self.f.make_package(baseline_mutator=lambda b: b.update(edition='EN'))
        self.assertEqual(self.f.run()['status'], 'error')
        self.f.assert_old(self)

    def test_protected_signed_payload_rejected(self):
        for name in ('profiles/save.py', 'runtime/a.py', 'server-kit/Lolo.exe',
                     'backup-tool/tools/a.py', 'launcher.settings.json', 'client/a.py'):
            with self.subTest(name=name):
                self.f.make_package({name: b'bad'})
                self.assertEqual(self.f.run()['status'], 'error')
        self.f.assert_old(self)

    def test_unsafe_and_duplicate_zip(self):
        for name in ('../evil.py', '/evil.py', 'x\\evil.py', 'C:evil.py', 'CON.py',
                     'docs/a.md ', 'docs/a.md:ads', 'OFFICIAL_LAUNCHER.PY'):
            with self.subTest(name=name):
                self.f.make_package()
                with zipfile.ZipFile(self.f.stage / 'payload.zip', 'a') as z:
                    z.writestr(name, b'bad')
                self.f.manifest['assets']['CN'].update(meta((self.f.stage / 'payload.zip').read_bytes()))
                self.f.save_manifest()
                self.assertEqual(self.f.run()['status'], 'error')
        self.f.assert_old(self)

    def test_zip_symlink_rejected(self):
        self.f.make_package()
        with zipfile.ZipFile(self.f.stage / 'payload.zip', 'a') as z:
            info = zipfile.ZipInfo('docs/link.md')
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            z.writestr(info, b'../../outside')
        self.f.manifest['assets']['CN'].update(meta((self.f.stage / 'payload.zip').read_bytes()))
        self.f.save_manifest()
        self.assertEqual(self.f.run()['status'], 'error')

    def test_failure_at_each_replace_rolls_back(self):
        for at in range(4):
            with self.subTest(at=at), tempfile.TemporaryDirectory(prefix='synthetic-updater-') as temp:
                f = Fixture(temp)
                def fail(i, op):
                    if i == at:
                        raise OSError('simulated replacement failure')
                result = f.run(fault_hook=fail)
                self.assertEqual(result['status'], 'error', result)
                f.assert_old(self)
                self.assertEqual(f.run()['status'], 'applied')

    def test_crash_recovers_idempotently(self):
        class Crash(BaseException):
            pass
        def crash(i, op):
            if i == 2:
                raise Crash()
        with self.assertRaises(Crash):
            self.f.run(fault_hook=crash)
        result = apply.recover_update(self.f.root, busy_finder=idle)
        self.assertEqual(result, {'status': 'applied', 'recovered': True})
        self.f.assert_old(self)
        self.assertFalse(apply.recover_update(self.f.root, busy_finder=idle)['recovered'])
        self.assertEqual(self.f.run()['status'], 'applied')

    def test_corrupt_backup_fail_closed(self):
        class Crash(BaseException):
            pass
        with self.assertRaises(Crash):
            self.f.run(fault_hook=lambda *a: (_ for _ in ()).throw(Crash()))
        (self.f.root / 'runtime/updates/transaction/backup/1').write_bytes(b'bad')
        before = (self.f.root / 'docs/new.md').read_bytes()
        self.assertEqual(apply.recover_update(self.f.root, busy_finder=idle)['status'], 'error')
        self.assertEqual((self.f.root / 'docs/new.md').read_bytes(), before)

    def test_parent_identity_and_wait_no_kill(self):
        row = {'ProcessId': 123, 'ExecutablePath': str(self.f.root / 'OverFieldLauncher.exe'),
               'CreationDate': 'unique-start'}
        with patch.object(apply, '_processes', side_effect=[[row], []]):
            apply._wait_parent(123, self.f.root, 1)
        with patch.object(apply, '_processes', return_value=[{**row, 'ExecutablePath': 'C:/other.exe'}]):
            with self.assertRaises(ValueError):
                apply._wait_parent(123, self.f.root, 1)
        with patch.object(apply, '_processes', side_effect=[[row], [{**row, 'CreationDate': 'reused'}]]):
            with self.assertRaises(ValueError):
                apply._wait_parent(123, self.f.root, 1)

    def test_process_unknown_fail_closed(self):
        with patch.object(apply, '_processes', return_value=[{'ProcessId': 99999, 'ExecutablePath': None}]):
            self.assertEqual(apply.find_busy(self.f.root)[0]['reason'], 'unknown')
        with patch.object(apply, '_processes', side_effect=RuntimeError('unavailable')):
            self.assertEqual(apply.find_busy(self.f.root)[0]['reason'], 'unknown')

    def test_stage_and_root_identity(self):
        result = apply.apply_update(self.f.root, self.f.root, 'CN', busy_finder=idle, verifier=self.f.verifier)
        self.assertEqual(result['status'], 'error')
        class Crash(BaseException):
            pass
        with self.assertRaises(Crash):
            self.f.run(fault_hook=lambda *a: (_ for _ in ()).throw(Crash()))
        journal = self.f.root / 'runtime/updates/transaction/journal.json'
        data = json.loads(journal.read_bytes())
        data['root']['inode'] += 1
        journal.write_text(json.dumps(data))
        self.assertEqual(apply.recover_update(self.f.root, busy_finder=idle)['status'], 'error')

    def test_launch_uses_no_shell_and_records_parent_before_exit(self):
        row = {'ProcessId': 123, 'ExecutablePath': str(self.f.root / 'OverFieldLauncher.exe'),
               'CreationDate': 'unique-start'}
        with patch.object(apply, '_processes', return_value=[row]), patch.object(apply.subprocess, 'Popen') as popen:
            popen.return_value.pid = 456
            result = apply.launch_update(self.f.root, self.f.stage, 'CN', 123, True,
                                         verifier=self.f.verifier, busy_finder=idle)
            self.assertEqual(result['status'], 'launched', result)
            argv = popen.call_args.args[0]
            self.assertIn('--parent-identity', argv)
            self.assertIn('--restart', argv)
            self.assertNotIn('shell', popen.call_args.kwargs)
            self.assertIs(popen.call_args.kwargs['stdout'], popen.call_args.kwargs['stderr'])
            self.assertTrue(popen.call_args.kwargs['close_fds'])
        with patch.object(apply, '_processes', return_value=[]):
            apply._wait_parent(123, self.f.root, 1, row)

    def test_launch_preflight_failures_do_not_spawn(self):
        row = {'ProcessId': 123, 'ExecutablePath': str(self.f.root / 'OverFieldLauncher.exe'),
               'CreationDate': 'unique-start'}
        with patch.object(apply, '_processes', return_value=[row]), patch.object(apply.subprocess, 'Popen') as popen:
            kwargs = dict(verifier=self.f.verifier, busy_finder=idle)
            def launch(**extra):
                return apply.launch_update(self.f.root, self.f.stage, 'CN', 123, True, **{**kwargs, **extra})
            self.assertEqual(launch(busy_finder=lambda *a, **k: [{'reason': 'unknown'}])['status'], 'busy')
            (self.f.root / 'official_launcher.py').write_bytes(b'user edit')
            self.assertEqual(launch()['status'], 'conflict')
            (self.f.root / 'official_launcher.py').write_bytes(self.f.old['official_launcher.py'])
            (self.f.stage / 'manifest.sig').write_bytes(b'bad')
            self.assertEqual(launch()['status'], 'error')
            popen.assert_not_called()

    def test_system_processes_outside_root_do_not_block(self):
        rows = [{'ProcessId': 111, 'Name': 'csrss.exe', 'ExecutablePath': None, 'CommandLine': None},
                {'ProcessId': 112, 'Name': 'svchost.exe', 'ExecutablePath': None, 'CommandLine': None},
                {'ProcessId': 113, 'Name': 'python.exe', 'ExecutablePath': 'C:/other/python.exe',
                 'CommandLine': 'C:/other/worker.py'}]
        with patch.object(apply, '_processes', return_value=rows):
            self.assertEqual(apply.find_busy(self.f.root), [])
        rows.append({'ProcessId': 114, 'Name': 'Lolo.exe', 'ExecutablePath': None, 'CommandLine': None})
        with patch.object(apply, '_processes', return_value=rows):
            self.assertEqual(apply.find_busy(self.f.root)[0]['reason'], 'unknown')
        state = self.f.root / 'runtime/capture-state.json'
        state.write_text(json.dumps({'pid': 111}))
        with patch.object(apply, '_processes', return_value=rows[:1]):
            self.assertEqual(apply.find_busy(self.f.root)[0]['reason'], 'unknown')

    def test_external_privileged_interpreters_are_diagnostics_only(self):
        rows = [{'ProcessId': 9712, 'Name': 'powershell.exe', 'ExecutablePath': None, 'CommandLine': None},
                {'ProcessId': 21212, 'Name': 'python.exe', 'ExecutablePath': None, 'CommandLine': None},
                {'ProcessId': 99991, 'Name': 'svchost.exe', 'ExecutablePath': None, 'CommandLine': None}]
        for fallback in (None, 'C:\\external\\python.exe'):
            with patch.object(apply, '_processes', return_value=rows), patch.object(apply, '_query_process_image', return_value=fallback):
                diagnostics = []
                self.assertEqual(apply.find_busy(self.f.root, diagnostics=diagnostics), [])
                self.assertEqual({d['pid'] for d in diagnostics}, {9712, 21212})
                self.assertTrue(all(d['blocking'] is False for d in diagnostics))

    def test_world_state_owned_unknown_pids_block(self):
        rows = [{'ProcessId': 98000 + i, 'Name': 'python.exe', 'ExecutablePath': None, 'CommandLine': None}
                for i in range(4)]
        for directory in (self.f.root, self.f.root / 'runtime'):
            path = directory / 'world-state-CN.json'
            path.write_text(json.dumps({key: {'Id': 98000 + i, 'Owned': True}
                                        for i, key in enumerate(('Server', 'Gateway', 'Proxy', 'Client'))}))
            with patch.object(apply, '_processes', return_value=rows), patch.object(apply, '_query_process_image', return_value='C:/external/python.exe'):
                busy = apply.find_busy(self.f.root)
                self.assertEqual({b['pid'] for b in busy}, {98000, 98001, 98002, 98003})
                self.assertTrue(all(b['reason'] == 'unknown' for b in busy))
            path.unlink()

    def test_root_directory_boundaries_and_path_fallback(self):
        self.assertTrue(apply._root_in_text('D:\\en', '"D:\\en\\tools\\python.exe"'))
        self.assertTrue(apply._root_in_text('D:\\en', '--cwd="D:/en"'))
        self.assertFalse(apply._root_in_text('D:\\en', 'D:\\english\\python.exe'))
        self.assertFalse(apply._root_in_text('D:\\en', 'D:\\en-old\\python.exe'))
        rows = [{'ProcessId': 99981, 'Name': 'python.exe', 'ExecutablePath': None, 'CommandLine': None}]
        with patch.object(apply, '_processes', return_value=rows), patch.object(apply, '_query_process_image', return_value=str(self.f.root / 'runtime/python/python.exe')):
            self.assertEqual(apply.find_busy(self.f.root)[0]['reason'], 'installation_in_use')
        rows[0]['ExecutablePath'] = 'C:/external/python.exe'
        rows[0]['CommandLine'] = 'python "' + str(self.f.root / 'official_launcher.py') + '"'
        with patch.object(apply, '_processes', return_value=rows):
            self.assertEqual(apply.find_busy(self.f.root)[0]['reason'], 'installation_in_use')
        rows[0]['CommandLine'] = str(self.f.root) + '-neighbor/script.py'
        with patch.object(apply, '_processes', return_value=rows):
            self.assertEqual(apply.find_busy(self.f.root), [])

    def test_diagnostic_root_mentions_and_generic_launchers_do_not_block(self):
        root = str(self.f.root)
        rows = [{'ProcessId': 99961, 'Name': 'DeepSeek Harness.exe', 'ExecutablePath': 'E:/dsh/DeepSeek Harness.exe',
                 'CommandLine': 'context mentions ' + root + '/profiles'},
                {'ProcessId': 99962, 'Name': 'powershell.exe', 'ExecutablePath': 'C:/Windows/powershell.exe',
                 'CommandLine': 'python update_apply.py probe --root ' + root},
                {'ProcessId': 99963, 'Name': 'launcher.exe', 'ExecutablePath': None, 'CommandLine': None}]
        with patch.object(apply, '_processes', return_value=rows), patch.object(apply, '_query_process_image', return_value=None):
            self.assertEqual(apply.find_busy(self.f.root), [])
        rows[1]['CommandLine'] = 'python.exe --cwd="' + root + '" worker.py'
        with patch.object(apply, '_processes', return_value=rows), patch.object(apply, '_query_process_image', return_value=None):
            self.assertEqual(apply.find_busy(self.f.root)[0]['pid'], 99962)

    def test_dedicated_unknown_and_total_enumeration_failure_block(self):
        rows = [{'ProcessId': 99971, 'Name': 'Lolo.exe', 'ExecutablePath': None, 'CommandLine': None}]
        with patch.object(apply, '_processes', return_value=rows), patch.object(apply, '_query_process_image', return_value=None):
            self.assertEqual(apply.find_busy(self.f.root)[0]['reason'], 'unknown')
        for failure in ([], None):
            with patch.object(apply, '_processes', return_value=failure):
                self.assertEqual(apply.find_busy(self.f.root)[0]['reason'], 'unknown')
        with patch.object(apply, '_processes', side_effect=RuntimeError('CIM unavailable')):
            self.assertEqual(apply.find_busy(self.f.root)[0]['reason'], 'unknown')

    def test_unified_edition_apply_and_manifest_validation(self):
        with tempfile.TemporaryDirectory(prefix='synthetic-unified-') as temp:
            f = Fixture(temp, edition='UNIFIED')
            exe = f.root / 'OverFieldLauncher.exe'
            exe.write_bytes(b'synthetic-not-executable')
            row = {'ProcessId': 123, 'ExecutablePath': str(exe), 'CreationDate': 'unique-start'}
            with patch.object(apply, '_processes', return_value=[row]), \
                    patch.object(apply.subprocess, 'Popen') as popen:
                popen.return_value.pid = 456
                result = apply.launch_update(f.root, f.stage, 'UNIFIED', 123, True,
                                             verifier=f.verifier, busy_finder=idle)
                self.assertEqual(result['status'], 'launched', result)
            result = f.run()
            self.assertEqual(result['status'], 'applied', result)
            self.assertEqual(json.loads((f.root / 'runtime/updates/last-update.json').read_text(encoding='utf-8'))['edition'], 'UNIFIED')

    def test_edition_does_not_change_launcher_file_name(self):
        """The portable EN package ships OverFieldLauncher.exe, not -EN.exe.

        Requiring the -EN name for edition EN rejected every real EN install at
        handoff, and looked for a nonexistent file when restarting after apply.
        """
        self.addCleanup(self.tmp.cleanup) if hasattr(self, 'tmp') else None
        tmp = tempfile.TemporaryDirectory(prefix='synthetic-en-')
        self.addCleanup(tmp.cleanup)
        f = Fixture(tmp.name, edition='EN')
        # Only the plain name exists, exactly like the real portable package.
        exe = f.root / 'OverFieldLauncher.exe'
        exe.write_bytes(b'synthetic-not-executable')
        self.assertFalse((f.root / 'OverFieldLauncher-EN.exe').exists())
        row = {'ProcessId': 123, 'ExecutablePath': str(exe), 'CreationDate': 'unique-start'}
        with patch.object(apply, '_processes', return_value=[row]), \
                patch.object(apply.subprocess, 'Popen') as popen:
            popen.return_value.pid = 456
            result = apply.launch_update(f.root, f.stage, 'EN', 123, True,
                                         verifier=f.verifier, busy_finder=idle)
            self.assertEqual(result['status'], 'launched', result)
        # And apply-with-restart must relaunch the file that actually exists.
        with patch.object(apply.subprocess, 'Popen') as popen:
            result = f.run(restart=True)
            self.assertEqual(result['status'], 'applied', result)
            self.assertTrue(result.get('restarted'), result)
            launched = [c.args[0] for c in popen.call_args_list if isinstance(c.args[0], list)]
            self.assertEqual(launched, [[str(exe)]])

    def test_failure_and_child_revalidation_restart_old_edition(self):
        exe = self.f.root / 'OverFieldLauncher.exe'
        exe.write_bytes(b'synthetic-not-executable')
        with patch.object(apply.subprocess, 'Popen') as popen:
            (self.f.stage / 'manifest.sig').write_bytes(b'bad')
            result = self.f.run(restart=True)
            self.assertEqual(result['status'], 'error')
            self.assertTrue(result['restarted'])
            self.assertEqual(popen.call_args.args[0], [str(exe)])
            self.f.assert_old(self)
        (self.f.stage / 'manifest.sig').write_bytes(b'fixture-signature')
        with patch.object(apply.subprocess, 'Popen') as popen:
            def fail(i, op):
                if i == 1:
                    raise OSError('failure')
            result = self.f.run(restart=True, fault_hook=fail)
            self.assertEqual(result['status'], 'error')
            self.assertTrue(result['restarted'])
            self.f.assert_old(self)
            popen.assert_called_once()

    def test_downgrade_and_case_rename_block(self):
        self.f.make_package({'OFFICIAL_LAUNCHER.PY': b'new launcher'})
        self.assertEqual(self.f.run()['status'], 'error')
        self.f.assert_old(self)
        self.f.make_package()
        baseline = json.loads((self.f.root / 'installation-manifest.json').read_bytes())
        baseline['version'] = '3.0.0'
        (self.f.root / 'installation-manifest.json').write_text(json.dumps(baseline))
        self.assertEqual(self.f.run()['status'], 'error')

    def crash_fixture(self):
        class Crash(BaseException):
            pass
        with self.assertRaises(Crash):
            self.f.run(fault_hook=lambda *a: (_ for _ in ()).throw(Crash()))

    def test_probe_recovery_required_busy_priority(self):
        import io
        from contextlib import redirect_stdout
        self.crash_fixture()
        for busy, expected in (([], 'recovery_required'), ([{'reason': 'unknown'}], 'unknown'),
                               ([{'reason': 'installation_in_use'}], 'busy')):
            with patch.object(apply, 'find_busy', return_value=busy), redirect_stdout(io.StringIO()) as out:
                code = apply.main(['probe', '--root', str(self.f.root), '--ignore-pid', '123'])
            result = json.loads(out.getvalue())
            self.assertEqual(result['status'], expected)
            self.assertEqual(code, 1)
            self.assertEqual(result['busy'], busy)

    def test_launch_recover_readonly_and_child_recovers_after_exit(self):
        self.crash_fixture()
        row = {'ProcessId': 123, 'ExecutablePath': str(self.f.root / 'OverFieldLauncher.exe'),
               'CreationDate': 'unique-start'}
        changed = (self.f.root / 'docs/new.md').read_bytes()
        with patch.object(apply, '_processes', return_value=[row]), patch.object(apply.subprocess, 'Popen') as popen:
            popen.return_value.pid = 456
            result = apply.launch_recovery(self.f.root, 123, True, busy_finder=idle)
            self.assertEqual(result['status'], 'launched', result)
            argv = popen.call_args.args[0]
            self.assertIn('recover', argv)
            self.assertNotIn('--stage', argv)
            self.assertIn('--parent-identity', argv)
            self.assertEqual((self.f.root / 'docs/new.md').read_bytes(), changed)
        exe = self.f.root / 'OverFieldLauncher.exe'
        exe.write_bytes(b'synthetic-not-executable')
        with patch.object(apply, '_processes', return_value=[]), patch.object(apply.subprocess, 'Popen') as popen:
            result = apply.recovery_helper(self.f.root, 'CN', 123, True, parent_identity=row, busy_finder=idle)
            self.assertEqual(result['status'], 'applied', result)
            self.assertTrue(result['restarted'])
            self.f.assert_old(self)
            self.assertEqual(popen.call_args.args[0], [str(exe)])

    def test_recovery_external_edit_never_overwritten_or_spawned(self):
        self.crash_fixture()
        row = {'ProcessId': 123, 'ExecutablePath': str(self.f.root / 'OverFieldLauncher.exe'),
               'CreationDate': 'unique-start'}
        edited = self.f.root / 'docs/new.md'
        edited.write_bytes(b'user recovery edit')
        with patch.object(apply, '_processes', return_value=[row]), patch.object(apply.subprocess, 'Popen') as popen:
            result = apply.launch_recovery(self.f.root, 123, True, busy_finder=idle)
            self.assertEqual(result['status'], 'error')
            popen.assert_not_called()
        with patch.object(apply, '_processes', return_value=[]), patch.object(apply.subprocess, 'Popen') as popen:
            result = apply.recovery_helper(self.f.root, 'CN', 123, True, parent_identity=row, busy_finder=idle)
            self.assertEqual(result['status'], 'error')
            popen.assert_not_called()
        self.assertEqual(edited.read_bytes(), b'user recovery edit')
        self.assertEqual((self.f.root / 'official_launcher.py').read_bytes(), self.f.old['official_launcher.py'])

    def test_helper_before_executable_before_baseline(self):
        self.f.make_package({'OverFieldLauncher.exe': b'synthetic-new-exe',
                             'update_apply.py': b'synthetic-helper',
                             'official_launcher.py': b'new launcher'})
        order = []
        result = self.f.run(fault_hook=lambda i, op: order.append(op['path']))
        self.assertEqual(result['status'], 'applied', result)
        self.assertLess(order.index('update_apply.py'), order.index('OverFieldLauncher.exe'))
        self.assertEqual(order[-1], 'installation-manifest.json')

    def test_reparse_ancestor_blocks(self):
        # Symbolic link permissions vary on Windows. Mock lstat's Windows attribute
        # only on the synthetic docs ancestor, exercising the real ancestor walk.
        original = Path.lstat
        def linked(path):
            s = original(path)
            if path == self.f.root / 'docs':
                class Reparse:
                    st_mode = s.st_mode
                    st_file_attributes = 0x400
                return Reparse()
            return s
        with patch.object(Path, 'lstat', linked):
            self.assertEqual(self.f.run()['status'], 'error')
        self.f.assert_old(self)


class PolicyTests(unittest.TestCase):
    def test_windows_paths(self):
        for name in ('', '../x.py', '/x.py', 'x\\y.py', 'x:y.py', 'AUX.txt', 'docs/COM1.md',
                     'docs/a.', 'docs/a ', 'docs//a.py', 'docs/./a.py'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_path(name)

    def test_collision_budgets(self):
        for files in ({'a.py': meta(b'x'), 'A.py': meta(b'x')},
                      {'a.py': meta(b'x'), 'a.py/b.py': meta(b'x')},
                      {'a.py': {'size': True, 'sha256': '0' * 64}},
                      {'a.py': {'size': 2**40, 'sha256': '0' * 64}}):
            with self.assertRaises(ValueError):
                validate_files(files)

class ReplaceTests(unittest.TestCase):
    """The atomic replace must survive a transiently locked destination."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='replace-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.src = self.root / 'src.bin'
        self.dst = self.root / 'dst.bin'
        self.src.write_bytes(b'new content')
        self.dst.write_bytes(b'old content')

    def test_replaces_existing_file(self):
        apply._replace(self.src, self.dst)
        self.assertEqual(self.dst.read_bytes(), b'new content')
        self.assertFalse(self.src.exists())

    def test_retries_sharing_violation_then_succeeds(self):
        """A briefly locked destination must not fail an otherwise valid update."""
        real = apply._replace_once
        calls = {'n': 0}

        def flaky(src, dst):
            calls['n'] += 1
            if calls['n'] < 3:
                error = OSError(32, 'sharing violation')
                error.winerror = 32
                raise error
            return real(src, dst)

        with patch.object(apply, '_replace_once', flaky), \
                patch.object(apply.time, 'sleep', lambda *_: None):
            apply._replace(self.src, self.dst)
        self.assertEqual(calls['n'], 3)
        self.assertEqual(self.dst.read_bytes(), b'new content')

    def test_gives_up_after_budget(self):
        def always(src, dst):
            error = OSError(32, 'sharing violation')
            error.winerror = 32
            raise error

        with patch.object(apply, '_replace_once', always), \
                patch.object(apply.time, 'sleep', lambda *_: None):
            with self.assertRaises(OSError):
                apply._replace(self.src, self.dst)

    def test_non_transient_error_is_not_retried(self):
        calls = {'n': 0}

        def broken(src, dst):
            calls['n'] += 1
            error = OSError(2, 'file not found')
            error.winerror = 2
            raise error

        with patch.object(apply, '_replace_once', broken), \
                patch.object(apply.time, 'sleep', lambda *_: None):
            with self.assertRaises(OSError):
                apply._replace(self.src, self.dst)
        self.assertEqual(calls['n'], 1)


class AppliedNoticeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='notice-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_records_applied_version(self):
        apply._record_applied(self.root, 'CN', '9.9.9')
        doc = json.loads((self.root / 'runtime/updates/last-update.json').read_text(encoding='utf-8'))
        self.assertEqual(doc['version'], '9.9.9')
        self.assertEqual(doc['edition'], 'CN')

    def test_apply_writes_notice(self):
        f = Fixture(self.root)
        result = f.run()
        self.assertEqual(result['status'], 'applied', result)
        doc = json.loads((self.root / 'runtime/updates/last-update.json').read_text(encoding='utf-8'))
        self.assertEqual(doc['version'], '2.0.0')

    def test_notice_failure_does_not_fail_update(self):
        f = Fixture(self.root)
        real = apply._durable

        def only_notice_fails(path, data):
            if Path(path).name == 'last-update.json':
                raise OSError('disk full')
            return real(path, data)

        with patch.object(apply, '_durable', only_notice_fails):
            result = f.run()
        # The update itself must still be reported as applied.
        self.assertEqual(result['status'], 'applied', result)
        self.assertFalse((self.root / 'runtime/updates/last-update.json').exists())


class AllowlistTests(unittest.TestCase):
    def test_allowlist(self):
        for p in ('OverFieldLauncher.exe', 'OverFieldLauncher-EN.exe', 'model_schema.json',
                  'app/model_schema.json', 'app/World-Control.ps1', 'app/LocalUidSDK.dll',
                  'app/installation-manifest.json', 'app/release-build.json',
                  'backup-tool/proto_gen/net_pb2.py', 'docs/release.md',
                  'World-Control.ps1', 'Lan-Firewall.ps1', 'make_junction.ps1',
                  'backup-tool/exploration_catalog.json', 'backup-tool/RELEASE-NOTES.txt',
                  'backup-tool/THIRD-PARTY.txt', 'backup-tool/requirements.txt',
                  'backup-tool/使用说明.txt'):
            self.assertTrue(is_managed(p), p)
        for p in ('profiles/a.py', 'runtime/a.py', 'launcher.settings.user.json',
                  'app/arbitrary.ps1', 'app/backup-tool/a.py', 'profiles/app/a.py',
                  'backup-tool/tools/a.py', 'server-kit/Lolo.exe', 'client/game.py',
                  'runtime-python/a.py', 'docs/a.db', 'docs/a.db-wal',
                  'arbitrary.ps1', 'backup-tool/arbitrary_catalog.json',
                  'backup-tool/config/a.py', 'backup-tool/tools/requirements.txt'):
            self.assertFalse(is_managed(p), p)

    def test_flat_install_upgrades_to_app_layout(self):
        with tempfile.TemporaryDirectory(prefix='flat-to-app-') as temp:
            f = Fixture(temp, edition='EN')
            members = {'app/official_launcher.py': b'new app launcher', 'docs/new.md': b'new doc'}
            baseline = {'schemaVersion': 1, 'version': '2.0.0', 'edition': 'EN',
                        'files': {k: meta(v) for k, v in members.items()}}
            members['app/installation-manifest.json'] = json.dumps(baseline).encode()
            with zipfile.ZipFile(f.stage / 'payload.zip', 'w', zipfile.ZIP_DEFLATED) as z:
                for name, data in members.items():
                    z.writestr(name, data)
            data = (f.stage / 'payload.zip').read_bytes()
            files = {k: meta(v) for k, v in members.items()}
            f.manifest['assets']['EN'] = {**meta(data), 'files': files}
            f.save_manifest()
            result = f.run()
            self.assertEqual(result['status'], 'applied', result)
            self.assertEqual((f.root / 'app/official_launcher.py').read_bytes(), b'new app launcher')
            self.assertTrue((f.root / 'app/installation-manifest.json').is_file())
            self.assertFalse((f.root / 'official_launcher.py').exists())
            self.assertFalse((f.root / 'installation-manifest.json').exists())
            self.assertEqual((f.root / 'profiles/a/save.db').read_bytes(), b'user data')

    def test_sanitized_powershell_env_strips_pwsh7_modules(self):
        with patch.dict('os.environ', {
            'PSModulePath': r'C:\Program Files\PowerShell\7\Modules;C:\Windows\System32\WindowsPowerShell\v1.0\Modules;D:\PowerShell\6\Modules'
        }):
            env = apply._sanitized_powershell_env()
            value = next(v for k, v in env.items() if k.lower() == 'psmodulepath')
            cleaned = value.split(';')
            self.assertEqual(cleaned, [r'C:\Windows\System32\WindowsPowerShell\v1.0\Modules'])


if __name__ == '__main__':
    unittest.main()
