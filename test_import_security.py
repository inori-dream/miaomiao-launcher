"""Synthetic security regressions for archive import, schema copy, and privacy."""
from __future__ import annotations

import contextlib
import gzip
import io
import json
import sqlite3
import sys
import tempfile
import unittest
import warnings
import zipfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

import capture_import
import capture_zip
import official_import
import privacy_check
import schema_restore
from test_official_import import main_data, make_database

MAIN_MEMBER = 'json/00001_s2c_1006_PlayerMainDataRsp.json'


class SecurityTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.profile = self.root / 'profiles/solo'
        self.database = make_database(self.profile / 'db/lolo.db')

    def package(self, main=None, extra=None, summary=None, name='capture.zip'):
        path = self.root / name
        main = main if main is not None else main_data(777)
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(MAIN_MEMBER, json.dumps(main))
            for member, payload in (extra or {}).items():
                archive.writestr(member, payload)
            if summary is not None:
                archive.writestr('summary.json', json.dumps(summary))
        return path

    def row_count(self, table='of_game'):
        with closing(sqlite3.connect(self.database)) as connection:
            return connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]

    def assert_no_save(self):
        self.assertEqual(self.row_count(), 0)
        self.assertEqual(self.row_count('of_game_basic'), 0)


class ArchiveSecurityTests(SecurityTestCase):
    def test_traversal_absolute_windows_and_symlink_members_are_rejected(self):
        for index, name in enumerate(('../escape.json', '/absolute.json', 'C:/drive.json',
                                      'json\\backslash.json', 'json/../escape.json')):
            with self.subTest(name=name):
                source = self.package(name=f'path-{index}.zip')
                with zipfile.ZipFile(source, 'a') as archive:
                    archive.writestr(name, '{}')
                if chr(92) in name:
                    # ZipInfo normalizes backslashes on Windows; mutate both
                    # local and central names to model a genuinely hostile ZIP.
                    source.write_bytes(source.read_bytes().replace(
                        b'json/backslash.json', name.encode('ascii')))
                with self.assertRaises(ValueError):
                    capture_import.read_capture(source)
        link = self.root / 'link.zip'
        info = zipfile.ZipInfo('json/link.json')
        info.create_system = 3
        info.external_attr = (0o120777 << 16)
        with zipfile.ZipFile(link, 'w') as archive:
            archive.writestr(MAIN_MEMBER, json.dumps(main_data(777)))
            archive.writestr(info, '../outside')
        with self.assertRaisesRegex(ValueError, 'symbolic'):
            capture_import.read_capture(link)
        self.assertFalse((self.root / 'escape.json').exists())

    def test_duplicate_and_case_ambiguous_members_are_rejected(self):
        source = self.package()
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            with zipfile.ZipFile(source, 'a') as archive:
                archive.writestr(MAIN_MEMBER, '{}')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            capture_import.read_capture(source)
        ambiguous = self.root / 'ambiguous.zip'
        with zipfile.ZipFile(ambiguous, 'w') as archive:
            archive.writestr(MAIN_MEMBER, json.dumps(main_data(777)))
            archive.writestr('JSON/00001_s2c_1006_PlayerMainDataRsp.json', '{}')
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            capture_import.read_capture(ambiguous)

    def test_duplicate_json_fields_are_rejected(self):
        source = self.root / 'duplicate-json.zip'
        raw = '{"status":"StatusCode_OK","player_id":777,"player_id":888}'
        with zipfile.ZipFile(source, 'w') as archive:
            archive.writestr(MAIN_MEMBER, raw)
        with self.assertRaisesRegex(ValueError, 'duplicate fields'):
            capture_import.read_capture(source)

    def test_oversized_member_is_rejected_from_metadata(self):
        source = self.package(extra={'json/00002_s2c_200_PackNotice.json': b' ' * 2048})
        with patch.object(capture_zip, 'MAX_MEMBER_UNCOMPRESSED', 1024):
            with self.assertRaisesRegex(ValueError, 'too large'):
                capture_import.read_capture(source)

    def test_streamed_decompression_is_bounded(self):
        source = self.package(extra={'json/00002_s2c_200_PackNotice.json': b' ' * 4096})
        with capture_zip.open_secure_zip(source) as archive:
            member = archive.infolist()[1]
            with patch.object(capture_zip, 'MAX_MEMBER_UNCOMPRESSED', 1024):
                with self.assertRaisesRegex(ValueError, 'safety limit'):
                    capture_zip.read_member(archive, member)

    def test_total_expansion_and_compression_ratio_are_bounded(self):
        source = self.package(extra={'json/00002_s2c_200_PackNotice.json': b' ' * 2048})
        with patch.object(capture_zip, 'MAX_TOTAL_UNCOMPRESSED', 1024):
            with self.assertRaisesRegex(ValueError, 'too much data'):
                capture_import.read_capture(source)
        bomb = self.root / 'ratio.zip'
        with zipfile.ZipFile(bomb, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('json/large.json', b'A' * 8192)
        with patch.object(capture_zip, 'MAX_COMPRESSION_RATIO', 2), \
             patch.object(capture_zip, 'COPY_CHUNK', 0):
            with self.assertRaisesRegex(ValueError, 'ratio'):
                capture_import.read_capture(bomb)

    def test_raw_network_capture_and_client_requests_are_rejected(self):
        for index, member in enumerate(('capture.pcapng', 'json/00002_c2s_1001_LoginReq.json',
                                        'requests/login.json')):
            source = self.package(extra={member: b'{}'}, name=f'raw-{index}.zip')
            with self.assertRaises(ValueError):
                capture_import.read_capture(source)

    def test_incomplete_summary_is_opt_in_only(self):
        source = self.package(summary={'incomplete_stream_count': 1, 'trailing_bytes_count': 9,
                                       'decode_fallback_count': 2})
        with self.assertRaisesRegex(ValueError, '解码不完整'):
            capture_import.import_capture(source, self.profile, 777)
        self.assert_no_save()
        result = capture_import.import_capture(source, self.profile, 777, allow_incomplete=True)
        self.assertTrue(result['quality']['incomplete'])
        self.assertEqual(result['quality']['incompleteStreams'], 1)
        self.assertEqual(self.row_count(), 1)

    def test_malformed_summary_metadata_is_not_coerced(self):
        source = self.package(summary={'incomplete_stream_count': 'not-a-count'})
        with self.assertRaisesRegex(ValueError, 'completeness metadata'):
            capture_import.import_capture(source, self.profile, 777, allow_incomplete=True)
        self.assert_no_save()


class SecretAndLogTests(SecurityTestCase):
    def test_sensitive_fields_bearer_values_and_binary_keys_are_rejected(self):
        secret = 'synthetic-' + 'credential-value'
        bearer = 'Bear' + 'er ' + 'A' * 24
        private_key = b'-----BEGIN ' + b'PRIVATE KEY-----\nsynthetic-material'
        cases = (
            ('json/00002_s2c_200_PackNotice.json', json.dumps({'access_token': secret})),
            ('json/00002_s2c_200_PackNotice.json', json.dumps({'message': bearer})),
            ('json/00002_s2c_200_PackNotice.bin', private_key),
        )
        for index, (member, payload) in enumerate(cases):
            with self.subTest(member=member):
                source = self.package(extra={member: payload}, name=f'secret-{index}.zip')
                with self.assertRaisesRegex(ValueError, 'sensitive credentials') as caught:
                    capture_import.import_capture(source, self.profile, 777, allow_incomplete=True)
                self.assertNotIn(secret, str(caught.exception))
                self.assertNotIn(bearer, str(caught.exception))
                self.assertNotIn('synthetic-material', str(caught.exception))
        self.assert_no_save()

    def test_secret_scanner_reports_categories_not_values(self):
        token = 'gh' + 'p_' + 'A' * 36
        jwt = '.'.join(('eyJ' + 'A' * 12, 'B' * 12, 'C' * 12))
        findings = privacy_check.scan_bytes((token + ' ' + jwt).encode())
        self.assertIn('github-token', findings)
        self.assertIn('jwt', findings)
        self.assertNotIn(token, json.dumps(sorted(findings)))

    def test_errors_and_cli_paths_are_redacted(self):
        secret = 'token=' + 'do-not-log-this'
        windows_path = 'C:' + '\\Users\\' + 'private-user\\profile\\lolo.db'
        message = privacy_check.safe_error(OSError(f'{secret} at {windows_path}'))
        self.assertNotIn('do-not-log-this', message)
        self.assertNotIn('private-user', message)
        self.assertNotIn(windows_path, message)
        json_secret = '{"access_token":"' + 'json-credential' + '"}'
        redacted_json = privacy_check.safe_error(ValueError(json_secret))
        self.assertNotIn('json-credential', redacted_json)
        pem = ('-----BEGIN ' + 'PRIVATE KEY-----\nprivate-key-body\n' +
               '-----END ' + 'PRIVATE KEY-----')
        redacted_pem = privacy_check.safe_error(ValueError(pem))
        self.assertNotIn('private-key-body', redacted_pem)
        output = io.StringIO()
        argv = ['official_import.py', 'plan', '--file', windows_path,
                '--database', str(self.database)]
        with patch.object(sys, 'argv', argv), contextlib.redirect_stdout(output):
            self.assertEqual(official_import.main(), 1)
        self.assertNotIn('private-user', output.getvalue())
        self.assertNotIn(windows_path, output.getvalue())


class NoOverwriteAndTaskTests(SecurityTestCase):
    def test_replace_flag_cannot_overwrite_direct_import(self):
        source = self.package()
        capture_import.import_capture(source, self.profile, 777)
        with closing(sqlite3.connect(self.database)) as connection:
            before = connection.execute('SELECT bin_data FROM of_game WHERE user_id=777').fetchone()[0]
            name_before = connection.execute('SELECT nick_name FROM of_game_basic WHERE user_id=777').fetchone()[0]
        backups_before = set((self.profile / 'backups').iterdir())
        report_before = (self.profile / 'accounts/777/import-report.json').read_bytes()
        changed = self.package(main_data(777, 'Replacement'), name='changed.zip')
        with self.assertRaisesRegex(ValueError, 'never overwritten'):
            capture_import.import_capture(changed, self.profile, 777, replace=True)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                'SELECT bin_data FROM of_game WHERE user_id=777').fetchone()[0], before)
            self.assertEqual(connection.execute(
                'SELECT nick_name FROM of_game_basic WHERE user_id=777').fetchone()[0], name_before)
        self.assertEqual(set((self.profile / 'backups').iterdir()), backups_before)
        self.assertEqual((self.profile / 'accounts/777/import-report.json').read_bytes(), report_before)

    def test_replace_flag_cannot_overwrite_official_import(self):
        source = self.package(name='account.zip')
        official_import.import_account(source, self.database, None)
        changed = self.package(main_data(777, 'Replacement'), name='account-changed.zip')
        with self.assertRaisesRegex(ValueError, '禁止覆盖'):
            official_import.import_account(changed, self.database, None, replace=True)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute(
                'SELECT nick_name FROM of_game_basic WHERE user_id=777').fetchone()[0], 'Test')

    def test_orphan_basic_row_and_uid_mapping_conflict_block_import(self):
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("INSERT INTO of_game_basic(user_id,nick_name) VALUES(777,'Orphan')")
        source = self.package()
        with self.assertRaisesRegex(ValueError, 'never overwritten'):
            capture_import.import_capture(source, self.profile, 777)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("DELETE FROM of_game_basic WHERE user_id=777")
            connection.execute('INSERT INTO of_user VALUES(1,777,0)')
        with self.assertRaisesRegex(ValueError, 'mapping'):
            capture_import.import_capture(source, self.profile, 777)
        self.assert_no_save()

    def test_expected_uid_mismatch_is_rejected(self):
        source = self.package()
        with self.assertRaisesRegex(ValueError, 'differs'):
            capture_import.import_capture(source, self.profile, 778)
        self.assert_no_save()

    def test_import_temporaries_stay_in_task_and_are_cleaned_on_failure(self):
        outside = self.root / 'system-temp'
        outside.mkdir()
        source = self.package(extra={'json/00002_s2c_200_PackNotice.json':
                                     json.dumps({'access_token': 'synthetic-secret'})})
        with patch.object(capture_import.tempfile, 'tempdir', str(outside)):
            with self.assertRaises(ValueError):
                capture_import.import_capture(source, self.profile, 777)
        self.assertEqual(list(outside.iterdir()), [])
        task_root = self.profile / 'runtime/imports'
        self.assertTrue(task_root.is_dir())
        self.assertEqual(list(task_root.iterdir()), [])
        self.assert_no_save()

    def test_official_import_uses_task_local_flattening_and_original_hash(self):
        outside = self.root / 'system-temp'
        outside.mkdir()
        source = self.package(name='official.zip')
        with patch.object(capture_import.tempfile, 'tempdir', str(outside)):
            result = official_import.import_account(source, self.database, None)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(list((self.profile / 'runtime/imports').iterdir()), [])
        self.assertEqual(result['sha256'], capture_zip.archive_sha256(source))
        self.assertEqual(self.row_count(), 1)


class SchemaSecurityTests(SecurityTestCase):
    def make_source(self, name='source.db'):
        return self.root / name

    def test_migration_preserves_foreign_keys_views_triggers_and_versions_without_rows(self):
        source = self.make_source()
        with closing(sqlite3.connect(source)) as connection, connection:
            connection.executescript('''
                PRAGMA user_version=19;
                PRAGMA application_id=12345;
                CREATE TABLE parent(id INTEGER PRIMARY KEY);
                CREATE TABLE child(id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parent(id));
                CREATE TABLE audit(message TEXT);
                CREATE TRIGGER child_ai AFTER INSERT ON child BEGIN
                    INSERT INTO audit(message) VALUES('migrated');
                END;
                CREATE VIEW parent_view AS SELECT id FROM parent;
                INSERT INTO parent VALUES(1);
                INSERT INTO child VALUES(2,1);
                INSERT INTO audit VALUES('template-private-row');
            ''')
        target = self.root / 'schema/target.db'
        capture_import.schema_database(source, target)
        with closing(sqlite3.connect(target)) as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 19)
            self.assertEqual(connection.execute('PRAGMA application_id').fetchone()[0], 12345)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM parent').fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM child').fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM audit').fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM parent_view').fetchone()[0], 0)
            self.assertEqual(len(connection.execute('PRAGMA foreign_key_list(child)').fetchall()), 1)
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('INSERT INTO parent VALUES(10)')
            connection.execute('INSERT INTO child VALUES(20,10)')
            self.assertEqual(connection.execute('SELECT message FROM audit').fetchone()[0], 'migrated')
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute('INSERT INTO child VALUES(21,999)')
        self.assertEqual(list((target.parent / '.schema-tasks').glob('*')), [])

    def test_fts_virtual_and_shadow_tables_are_rejected(self):
        source = self.make_source('fts.db')
        with closing(sqlite3.connect(source)) as connection:
            try:
                connection.execute('CREATE VIRTUAL TABLE search USING fts5(body)')
            except sqlite3.OperationalError as error:
                self.skipTest(f'FTS5 unavailable: {error}')
            connection.execute("INSERT INTO search VALUES('template-private-row')")
        target = self.root / 'fts-target.db'
        with self.assertRaisesRegex(ValueError, 'virtual or shadow'):
            capture_import.schema_database(source, target)
        self.assertFalse(target.exists())
        self.assertFalse((self.root / '.schema-tasks').exists())

    def test_malicious_or_data_bearing_schema_objects_are_rejected(self):
        statements = (
            "CREATE VIEW unsafe AS SELECT load_extension('untrusted-extension')",
            "CREATE TABLE item(id); CREATE TRIGGER unsafe AFTER INSERT ON item BEGIN "
            "SELECT load_extension('untrusted-extension'); END",
            'CREATE TABLE child(parent_id INTEGER REFERENCES missing_parent(id))',
        )
        for index, statement in enumerate(statements):
            with self.subTest(statement=statement):
                source = self.make_source(f'malicious-{index}.db')
                with closing(sqlite3.connect(source)) as connection, connection:
                    connection.executescript(statement)
                target = self.root / f'malicious-target-{index}.db'
                with self.assertRaises((ValueError, sqlite3.Error)):
                    capture_import.schema_database(source, target)
                self.assertFalse(target.exists())

    def test_schema_publication_never_replaces_existing_target(self):
        source = self.make_source()
        with closing(sqlite3.connect(source)) as connection:
            connection.execute('CREATE TABLE item(id INTEGER)')
        target = self.root / 'existing.db'
        target.write_bytes(b'keep')
        with self.assertRaises(FileExistsError):
            capture_import.schema_database(source, target)
        self.assertEqual(target.read_bytes(), b'keep')

    def test_account_seeding_trigger_is_preserved_but_cannot_leak_during_import(self):
        source = self.make_source('seeding-trigger.db')
        with closing(sqlite3.connect(source)) as connection, connection:
            connection.executescript('''
                CREATE TABLE of_user(user_id INTEGER PRIMARY KEY, sdk_uid INTEGER UNIQUE, ban INTEGER);
                CREATE TABLE of_game(user_id INTEGER PRIMARY KEY, created_at TEXT,
                                     updated_at TEXT, bin_data BLOB);
                CREATE TABLE of_game_basic(user_id INTEGER PRIMARY KEY, nick_name TEXT, level INTEGER);
                CREATE TRIGGER seed_template AFTER INSERT ON of_user BEGIN
                    INSERT INTO of_game_basic(user_id,nick_name,level) VALUES(999,'Template',50);
                END;
                INSERT INTO of_user VALUES(42,42,0);
                INSERT INTO of_game_basic VALUES(42,'TemplateOwner',50);
            ''')
        profile = self.root / 'trigger-profile'
        database = profile / 'db/lolo.db'
        capture_import.schema_database(source, database)
        with closing(sqlite3.connect(database)) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM of_user').fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0], 1)
        with self.assertRaisesRegex(ValueError, 'triggers attempted'):
            capture_import.import_capture(self.package(), profile, 777)
        with closing(sqlite3.connect(database)) as connection:
            for table in ('of_user', 'of_game', 'of_game_basic'):
                self.assertEqual(connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
        self.assertFalse((profile / 'accounts/777').exists())


class FieldConflictTests(SecurityTestCase):
    def test_fallback_named_collision_is_rejected_in_both_orders(self):
        pairs = (
            {'field_1': 1, 'weapon_id': 2},
            {'weapon_id': 2, 'field_1': 1},
        )
        for value in pairs:
            with self.subTest(value=value):
                with self.assertRaises(schema_restore.FieldConflictError):
                    schema_restore.restore(value, 'weapon')

    def test_normalized_field_alias_conflict_is_rejected_before_write(self):
        main = main_data(777)
        main['player-id'] = main['player_id']
        source = self.package(main)
        with self.assertRaisesRegex(ValueError, 'conflicting field'):
            capture_import.import_capture(source, self.profile, 777, allow_incomplete=True)
        self.assert_no_save()

    def test_duplicate_character_and_equipment_conflicts_are_rejected(self):
        main = main_data(777)
        duplicate = dict(main['characters'][0], level=99)
        main['characters'].append(duplicate)
        source = self.package(main, name='characters.zip')
        with self.assertRaisesRegex(ValueError, 'conflicting characters'):
            capture_import.import_capture(source, self.profile, 777)
        main = main_data(777)
        weapons = [
            {'weapon_id': 1001, 'instance_id': 55, 'level': 1},
            {'weapon_id': 1001, 'instance_id': 55, 'level': 2},
        ]
        source = self.package(main, {'json/00002_s2c_200_GetWeaponRsp.json': json.dumps({'weapons': weapons})},
                              name='weapons.zip')
        with self.assertRaisesRegex(ValueError, 'conflicting equipment'):
            capture_import.import_capture(source, self.profile, 777)
        self.assert_no_save()

    def test_unknown_fallback_is_refused_unless_explicitly_allowed(self):
        main = main_data(777)
        main['characters'][0]['character_appearance'] = {'field_99': 123}
        source = self.package(main)
        with self.assertRaisesRegex(ValueError, '解码不完整'):
            capture_import.import_capture(source, self.profile, 777)
        result = capture_import.import_capture(source, self.profile, 777, allow_incomplete=True)
        self.assertGreater(result['quality']['placeholderFields'], 0)
        with closing(sqlite3.connect(self.database)) as connection:
            payload = json.loads(gzip.decompress(connection.execute(
                'SELECT bin_data FROM of_game WHERE user_id=777').fetchone()[0]))
        self.assertEqual(payload['character']['characterMap']['101001']['characterAppearance'], {'badge': 5000})

    def test_extra_inventory_fallbacks_are_restored_in_place(self):
        notice = {'items': [{'extras': [{'weapon': {'field_1': 1001, 'field_2': 55}}]}]}
        report = schema_restore.restore_capture(main_data(777), {'PackNotice': [notice]})
        self.assertEqual(report['restoredUnresolved'], 0)
        weapon = notice['items'][0]['extras'][0]['weapon']
        self.assertEqual((weapon['weapon_id'], weapon['instance_id']), (1001, 55))

    def test_placeholder_scan_fails_closed_when_budget_is_exhausted(self):
        with self.assertRaisesRegex(ValueError, 'too complex'):
            capture_import.placeholder_hits({'field_1': 1}, [0])


if __name__ == '__main__':
    unittest.main(verbosity=2)
