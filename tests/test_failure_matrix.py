"""Deterministic, offline failure matrix for launcher hardening issues 92-96.

Every fixture lives under the agent evidence directory. Process APIs and package
signatures are injected; no bundled executable, credential, or real save is used.
"""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import random
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile

REPO = Path(__file__).resolve().parent
EVIDENCE = Path(r'C:\OverFieldLab\evidence\launcher-agent')
sys.path.insert(0, str(REPO))

import capture_import
import client_identity
import service_runner
import update_apply
from test_official_import import main_data, make_database
from test_update_apply import Fixture, idle


class EvidenceTestCase(unittest.TestCase):
    def setUp(self):
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='failure-matrix-', dir=EVIDENCE)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)


class QuickSDKFailureMatrixTests(EvidenceTestCase):
    def setUp(self):
        super().setUp()
        self.executable = self.root / 'profiles/solo/client/launcher.exe'
        self.stdout = self.root / 'client-stdout.log'
        self.stderr = self.root / 'client-stderr.log'
        self.token = 'deadbeef'
        self.result = self.root / f'quicksdk-start-{self.token}.json'
        self.pid = self.root / f'quicksdk-pid-{self.token}.json'
        self.evidence = self.root / f'quicksdk-failure-{self.token}.json'

    def assert_evidence(self, error_type):
        self.assertTrue(self.evidence.is_file(), 'startup/validation failure evidence missing')
        record = json.loads(self.evidence.read_text(encoding='utf-8'))
        self.assertEqual(record['type'], error_type)
        if not hasattr(self, 'worker_started'):
            self.assertIsNone(record['workerPid'])
        self.assertEqual(record['cleanupErrors'], [])
        self.assertFalse(self.result.exists())
        self.assertFalse(self.pid.exists())
        return record

    def test_validation_failures_never_spawn_and_publish_evidence(self):
        cases = (
            ('port-text', {'LOLO_DISPATCH_PORT': 'not-a-port'}, ValueError),
            ('port-range', {'LOLO_DISPATCH_PORT': '65536'}, ValueError),
            ('uid-text', {'LOLO_SDK_UID': 'not-a-uid'}, ValueError),
            ('uid-range', {'LOLO_SDK_UID': '0'}, ValueError),
        )
        for name, overrides, error_type in cases:
            with self.subTest(name=name):
                environment = {'LOLO_DISPATCH_HOST': '127.0.0.1',
                               'LOLO_DISPATCH_PORT': '18080', 'LOLO_SDK_UID': '1000000'}
                environment.update(overrides)
                def validate(client, host, port, uid):
                    if not 1 <= port <= 65535 or not 1 <= uid <= 0xffffffff:
                        raise ValueError('synthetic range validation failure')
                with patch('taptap_private_runner.validated_config', side_effect=validate) as check, \
                     patch.object(service_runner.uuid, 'uuid4', return_value=types.SimpleNamespace(hex=self.token)), \
                     patch.dict(sys.modules, {'frida': types.ModuleType('frida')}), \
                     patch.object(service_runner.subprocess, 'Popen') as spawn:
                    with self.assertRaises(error_type):
                        service_runner.launch_quicksdk(self.executable, self.root, self.stdout,
                                                       self.stderr, environment)
                if name in ('port-text', 'uid-text'):
                    check.assert_not_called()
                spawn.assert_not_called()
                self.assert_evidence(error_type.__name__)
                self.evidence.unlink()

    def test_adapter_validation_and_missing_dependency_fail_before_spawn(self):
        for name, error_type in (('validation', ValueError), ('dependency', ModuleNotFoundError)):
            with self.subTest(name=name):
                validation = (ValueError('synthetic invalid client') if name == 'validation' else None)
                with patch('taptap_private_runner.validated_config', side_effect=validation), \
                     patch.object(service_runner.uuid, 'uuid4', return_value=types.SimpleNamespace(hex=self.token)), \
                     patch.dict(sys.modules, {'frida': None}), \
                     patch.object(service_runner.subprocess, 'Popen') as spawn:
                    with self.assertRaises(error_type):
                        service_runner.launch_quicksdk(self.executable, self.root, self.stdout,
                                                       self.stderr, {})
                spawn.assert_not_called()
                self.assert_evidence(error_type.__name__)
                self.evidence.unlink()

    def test_unready_complete_identity_must_match_owned_executable(self):
        class Worker:
            pid = 10
            def poll(self): return None
            def terminate(self): pass
            def wait(self, timeout=None): return 0

        self.worker_started = True
        foreign = {'pid': 4242, 'path': str(self.root / 'foreign.exe'), 'startedTicks': 123}
        self.result.write_text(json.dumps({'ready': False, 'process': foreign}), encoding='utf-8')
        with patch('taptap_private_runner.validated_config', return_value={}), \
             patch.dict(sys.modules, {'frida': types.ModuleType('frida')}), \
             patch.object(service_runner.uuid, 'uuid4', return_value=types.SimpleNamespace(hex=self.token)), \
             patch.object(service_runner.subprocess, 'Popen', return_value=Worker()), \
             patch.object(service_runner, 'process_record',
                          side_effect=RuntimeError('executable path mismatch')) as verify, \
             patch.object(service_runner, 'terminate_process_tree') as terminate:
            with self.assertRaisesRegex(RuntimeError, 'did not become ready'):
                service_runner.launch_quicksdk(self.executable, self.root, self.stdout,
                                               self.stderr, {})
        verify.assert_called_once_with(4242, str(self.executable.resolve()), 123)
        terminate.assert_not_called()
        record = self.assert_evidence('RuntimeError')
        self.assertIsNone(record['process'])
        self.assertEqual(record['unverifiedPid'], 4242)

    def test_spawn_worker_exit_timeout_and_worker_cleanup_failure_matrix(self):
        class Worker:
            pid = 10
            returncode = None
            def poll(self): return self.returncode
            def terminate(self): self.returncode = 0
            def wait(self, timeout=None): return 0

        for mode in ('spawn', 'exit', 'timeout', 'worker-cleanup'):
            with self.subTest(mode=mode):
                worker = Worker()
                if mode == 'exit':
                    worker.returncode = 7
                if mode == 'worker-cleanup':
                    self.result.write_text('{"ready":false}', encoding='utf-8')
                    def fail_terminate():
                        raise OSError('synthetic worker cleanup failure')
                    worker.terminate = fail_terminate
                spawn_error = OSError('synthetic spawn failure') if mode == 'spawn' else None
                with patch('taptap_private_runner.validated_config', return_value={}), \
                     patch.dict(sys.modules, {'frida': types.ModuleType('frida')}), \
                     patch.object(service_runner.uuid, 'uuid4', return_value=types.SimpleNamespace(hex=self.token)), \
                     patch.object(service_runner.subprocess, 'Popen', side_effect=spawn_error,
                                  return_value=worker), \
                     patch.object(service_runner.time, 'monotonic',
                                  side_effect=[0, 181] if mode == 'timeout' else [0, 0]):
                    with self.assertRaises((OSError, RuntimeError, TimeoutError,
                                            service_runner.ProcessCleanupError)):
                        service_runner.launch_quicksdk(self.executable, self.root, self.stdout,
                                                       self.stderr, {})
                record = json.loads(self.evidence.read_text(encoding='utf-8'))
                self.assertFalse(self.result.exists())
                self.assertFalse(self.pid.exists())
                if mode == 'spawn':
                    self.assertIsNone(record['workerPid'])
                else:
                    self.assertEqual(record['workerPid'], 10)
                if mode == 'timeout':
                    self.assertEqual(record['type'], 'TimeoutError')
                if mode == 'worker-cleanup':
                    self.assertEqual(record['type'], 'RuntimeError')
                    self.assertTrue(any('worker cleanup failure' in item
                                        for item in record['cleanupErrors']))
                self.evidence.unlink()


class MarkerJunctionFailureMatrixTests(EvidenceTestCase):
    def marker(self):
        return {
            'schema': client_identity.MARKER_SCHEMA, 'source': str(self.root / 'source'),
            'fingerprint': 'a' * 64, 'files': {}, 'copies': {}, 'links': {},
            'adapter': 'quicksdk-pc',
        }

    def test_malformed_marker_matrix_fails_closed(self):
        marker = self.root / 'client-ready.json'
        cases = [
            b'', b'{', b'[]', b'null', b'\xff',
            json.dumps(dict(self.marker(), schema=1)).encode(),
            json.dumps(dict(self.marker(), files=[])).encode(),
            json.dumps(dict(self.marker(), copies=None)).encode(),
            json.dumps(dict(self.marker(), links=[])).encode(),
            json.dumps(dict(self.marker(), source='relative')).encode(),
            json.dumps(dict(self.marker(), fingerprint='bad')).encode(),
            json.dumps(dict(self.marker(), adapter='unknown')).encode(),
            (b'[' * 2000) + (b']' * 2000),
        ]
        source = self.root / 'source'
        source.mkdir()
        for index, raw in enumerate(cases):
            with self.subTest(index=index):
                marker.write_bytes(raw)
                self.assertFalse(client_identity.cache_matches(source, marker))

    def test_marker_reparse_swap_during_read_is_rejected(self):
        marker = self.root / 'client-ready.json'
        marker.write_text(json.dumps(self.marker()), encoding='utf-8')
        with patch.object(client_identity, 'is_reparse_point', side_effect=[False, True]):
            with self.assertRaisesRegex(ValueError, 'linked|changed'):
                client_identity.read_ready_marker(marker)

    def test_reparse_tag_matrix_distinguishes_junction_and_symlink(self):
        ordinary = types.SimpleNamespace(st_file_attributes=0, st_reparse_tag=0)
        junction = types.SimpleNamespace(st_file_attributes=0x400,
                                         st_reparse_tag=0xA0000003)
        symlink = types.SimpleNamespace(st_file_attributes=0x400,
                                        st_reparse_tag=0xA000000C)
        for name, stat_result, expected in (('ordinary', ordinary, False),
                                            ('junction', junction, True),
                                            ('symlink', symlink, False)):
            with self.subTest(name=name), patch.object(client_identity.os, 'lstat',
                                                       return_value=stat_result):
                self.assertEqual(client_identity.is_junction(self.root / name), expected)

    def test_linked_whitelist_source_and_destination_fail_closed(self):
        source = self.root / 'source'
        destination = self.root / 'destination'
        source.mkdir()
        destination.mkdir()
        with patch.object(client_identity, 'is_reparse_point', side_effect=[True, True]):
            with self.assertRaisesRegex(ValueError, 'linked private-client destination'):
                client_identity.assert_private_destination(source, destination)


class ZipSQLiteFailureMatrixTests(EvidenceTestCase):
    def setUp(self):
        super().setUp()
        self.profile = self.root / 'profile'
        self.database = make_database(self.profile / 'db/lolo.db')
        resources = self.profile / 'Resource/Excel'
        resources.mkdir(parents=True)
        (resources / 'Item.json').write_text(json.dumps({'Item': {'Datas': [
            {'ID': 10, 'NewBagItemTag': 9}, {'ID': 101001, 'NewBagItemTag': 7},
        ]}}), encoding='utf-8')
        (resources / 'Character.json').write_text(json.dumps({'Character': {'Datas': [
            {'ID': 101001},
        ]}}), encoding='utf-8')

    def package(self, main=None, extra=None, name='capture.zip'):
        path = self.root / name
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('json/00001_s2c_1006_PlayerMainDataRsp.json',
                             json.dumps(main if main is not None else main_data(777)))
            for member, payload in (extra or {}).items():
                archive.writestr(member, json.dumps(payload))
        return path

    def assert_no_save(self):
        with closing(sqlite3.connect(self.database)) as connection:
            for table in ('of_user', 'of_game', 'of_game_basic'):
                self.assertEqual(connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
        self.assertFalse((self.profile / 'accounts/777').exists())
        self.assertEqual(list((self.profile / 'runtime/imports').glob('task-*')), [])

    def test_seeded_zip_truncation_and_bit_flip_fuzz_is_bounded(self):
        original = self.package().read_bytes()
        mutations = [original[:index] for index in (0, 1, 4, 21, len(original) // 2, len(original) - 1)]
        rng = random.Random(92)
        for _ in range(48):
            raw = bytearray(original)
            for _ in range(rng.randint(1, 3)):
                raw[rng.randrange(len(raw))] ^= 1 << rng.randrange(8)
            mutations.append(bytes(raw))
        for index, raw in enumerate(mutations):
            with self.subTest(index=index):
                path = self.root / f'fuzz-{index}.zip'
                path.write_bytes(raw)
                try:
                    capture_import.read_capture(path)
                except (ValueError, OSError, zipfile.BadZipFile, sqlite3.Error):
                    pass
        self.assert_no_save()

    def test_malformed_equipment_scalar_matrix_is_value_error_not_type_error(self):
        malformed = ([1], {'x': 1})
        for field in ('weapon', 'armor_id', 'poster_id', 'instance_id'):
            for index, value in enumerate(malformed):
                with self.subTest(field=field, value=repr(value)):
                    main = main_data(777)
                    if field == 'weapon':
                        main['characters'][0]['equipment_presets'] = [{'weapon': value}]
                    elif field == 'armor_id':
                        main['characters'][0]['equipment_presets'] = [{'armors': [{'armor_id': value}]}]
                    elif field == 'poster_id':
                        main['characters'][0]['equipment_presets'] = [{'posters': [{'poster_id': value}]}]
                    else:
                        extra = {'json/00002_s2c_301_GetWeaponRsp.json':
                                 {'weapons': [{'instance_id': value}]}}
                        source = self.package(main, extra, name=f'{field}-{index}.zip')
                        with self.assertRaises(ValueError):
                            capture_import.import_capture(source, self.profile, 777)
                        self.assert_no_save()
                        continue
                    source = self.package(main, name=f'{field}-{index}.zip')
                    with self.assertRaises(ValueError):
                        capture_import.import_capture(source, self.profile, 777)
                    self.assert_no_save()

    def test_sqlite_trigger_failure_rolls_back_every_import_row(self):
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.executescript("""
                CREATE TRIGGER reject_basic BEFORE INSERT ON of_game_basic BEGIN
                    SELECT RAISE(ABORT, 'synthetic basic failure');
                END;
            """)
        source = self.package()
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'synthetic basic failure'):
            capture_import.import_capture(source, self.profile, 777)
        self.assert_no_save()

    def test_seeded_sqlite_byte_fuzz_reports_controlled_errors(self):
        original = self.database.read_bytes()
        rng = random.Random(94)
        for index in range(32):
            with self.subTest(index=index):
                raw = bytearray(original)
                for _ in range(rng.randint(1, 5)):
                    raw[rng.randrange(len(raw))] ^= 1 << rng.randrange(8)
                path = self.root / f'db-{index}.sqlite'
                path.write_bytes(raw)
                try:
                    with closing(sqlite3.connect(path)) as connection:
                        capture_import._validate_target_schema(connection)
                        connection.execute('PRAGMA integrity_check').fetchall()
                except (ValueError, sqlite3.Error):
                    pass


class UpdaterAtomicFailureMatrixTests(EvidenceTestCase):
    def test_commit_journal_failure_rolls_back_all_replaced_files(self):
        fixture = Fixture(self.root)
        real_write = update_apply._journal_write
        failed = False
        def fail_commit(path, journal):
            nonlocal failed
            if journal.get('state') == 'committed' and not failed:
                failed = True
                raise OSError('synthetic commit journal failure')
            return real_write(path, journal)
        with patch.object(update_apply, '_journal_write', side_effect=fail_commit):
            result = fixture.run()
        self.assertEqual(result['status'], 'error', result)
        self.assertTrue(result['transaction_started'])
        fixture.assert_old(self)
        journal = json.loads((fixture.root / 'runtime/updates/transaction/journal.json').read_bytes())
        self.assertEqual(journal['state'], 'rolled_back')
        self.assertEqual(fixture.run()['status'], 'applied')

    def test_recovery_replace_failure_keeps_journal_for_idempotent_retry(self):
        fixture = Fixture(self.root)
        class Crash(BaseException):
            pass
        with self.assertRaises(Crash):
            fixture.run(fault_hook=lambda *_: (_ for _ in ()).throw(Crash()))
        real_replace = update_apply._replace
        failed = False
        def fail_restore(src, dst):
            nonlocal failed
            if Path(src).name.startswith('restore-') and not failed:
                failed = True
                raise OSError('synthetic restore failure')
            return real_replace(src, dst)
        with patch.object(update_apply, '_replace', side_effect=fail_restore):
            result = update_apply.recover_update(fixture.root, busy_finder=idle)
        self.assertEqual(result['status'], 'error')
        journal = json.loads((fixture.root / 'runtime/updates/transaction/journal.json').read_bytes())
        self.assertEqual(journal['state'], 'applying')
        self.assertEqual(update_apply.recover_update(fixture.root, busy_finder=idle)['status'], 'applied')
        fixture.assert_old(self)


class ProcessTreeFailureMatrixTests(EvidenceTestCase):
    def tree(self, depth, width=1):
        records = [{'pid': i + 1, 'path': rf'C:\task\p{i}.exe', 'startedTicks': i + 100}
                   for i in range(depth * width)]
        children = {}
        if width == 1:
            for i in range(depth - 1):
                children[i + 1] = [i + 2]
        else:
            children[1] = list(range(2, width + 1))
        return records, children

    def run_tree(self, records, children, terminate=None):
        handles = {record['pid']: record['pid'] + 1000 for record in records}
        opened = set()
        closed = []
        terminated = []
        def open_verified(record):
            opened.add(handles[record['pid']])
            return handles[record['pid']], dict(record, Started='root')
        def open_child(pid, access):
            opened.add(handles[pid])
            return handles[pid]
        def identity(handle):
            record = records[handle - 1001]
            return record['path'], record['startedTicks']
        def wait(handle, timeout):
            if timeout == 5000 and terminate is not None:
                terminate(handle)
            return 0 if timeout else service_runner._WAIT_TIMEOUT
        def kill(handle):
            terminated.append(handle)
        with patch.object(service_runner, '_record_running', return_value=True), \
             patch.object(service_runner, '_open_verified_process', side_effect=open_verified), \
             patch.object(service_runner, '_open_process', side_effect=open_child), \
             patch.object(service_runner, '_identity_from_handle', side_effect=identity), \
             patch.object(service_runner, '_child_pids', side_effect=lambda pid: children.get(pid, [])), \
             patch.object(service_runner, '_wait_for_exit', side_effect=wait), \
             patch.object(service_runner, '_terminate_handle', side_effect=kill), \
             patch.object(service_runner, '_close_process', side_effect=closed.append):
            try:
                service_runner.terminate_process_tree(records[0])
            finally:
                self.assertEqual(set(closed), opened)
                self.assertEqual(len(closed), len(opened))
        return terminated

    def test_deep_tree_limit_fails_closed_without_killing(self):
        records, children = self.tree(65)
        with self.assertRaisesRegex(service_runner.ProcessCleanupError, 'unexpectedly large'):
            self.run_tree(records, children)

    def test_wide_tree_limit_fails_closed_without_killing(self):
        records, children = self.tree(1, 1025)
        with self.assertRaisesRegex(service_runner.ProcessCleanupError, 'unexpectedly large'):
            self.run_tree(records, children)

    def test_supported_deep_tree_is_killed_child_first(self):
        records, children = self.tree(64)
        terminated = self.run_tree(records, children)
        self.assertEqual(terminated, [record['pid'] + 1000 for record in reversed(records)])

    def test_one_termination_timeout_does_not_skip_remaining_tree(self):
        records, children = self.tree(3)
        def timeout(handle):
            if handle == records[-1]['pid'] + 1000:
                raise TimeoutError('synthetic timeout')
        with self.assertRaises(service_runner.ProcessCleanupError) as caught:
            self.run_tree(records, children, timeout)
        self.assertIn('synthetic timeout', str(caught.exception))
        self.assertEqual(len(caught.exception.records), 3)


if __name__ == '__main__':
    unittest.main(verbosity=2)
