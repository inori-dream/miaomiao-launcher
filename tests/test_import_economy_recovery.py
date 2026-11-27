"""Synthetic regressions for LA-001..LA-003; no live saves or services."""
from __future__ import annotations

import contextlib
import gzip
import io
import json
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / 'backup-tool'))
sys.path.insert(0, str(REPO / 'backup-tool/proto_gen'))

import capture_import
import import_to_ofps
import net_pb2
from test_official_import import main_data, make_database

MAIN_MEMBER = 'json/00001_s2c_1006_PlayerMainDataRsp.json'
PACK_MEMBER = 'json/00002_s2c_200_PackNotice.json'
MAX_INT64 = (1 << 63) - 1


def pack_item(item_id=10, tag=9, num=1, **main_extra):
    main = {'item_id': item_id, 'item_tag': tag, 'base_item': {'item_id': item_id, 'num': num}}
    main.update(main_extra)
    return {'main_item': main}


def write_capture_configs(profile: Path):
    resources = profile / 'Resource/Excel'
    resources.mkdir(parents=True, exist_ok=True)
    (resources / 'Item.json').write_text(json.dumps({'Item': {'Datas': [
        {'ID': 10, 'NewBagItemTag': 9},
        {'ID': 11, 'NewBagItemTag': 9},
        {'ID': 1000001, 'NewBagItemTag': 2},
        {'ID': 4200373, 'NewBagItemTag': 21},
    ]}}), encoding='utf-8')
    (resources / 'Character.json').write_text(json.dumps({'Character': {'Datas': [
        {'ID': 101001},
    ]}}), encoding='utf-8')


class CaptureEconomyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.profile = self.root / 'profile'
        self.database = make_database(self.profile / 'db/lolo.db')
        write_capture_configs(self.profile)
        self.configs = capture_import.load_authoritative_configs(self.profile)

    def package(self, items=None, name='capture.zip'):
        path = self.root / name
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(MAIN_MEMBER, json.dumps(main_data(777)))
            if items is not None:
                archive.writestr(PACK_MEMBER, json.dumps({'items': items}))
        return path

    def assert_no_save(self):
        with closing(sqlite3.connect(self.database)) as connection:
            for table in ('of_user', 'of_game', 'of_game_basic'):
                self.assertEqual(connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
        self.assertFalse((self.profile / 'accounts/777').exists())

    def test_malicious_negative_unknown_overflow_and_tag_are_rejected_before_write(self):
        cases = {
            'negative': pack_item(num=-5),
            'unknown': pack_item(item_id=999999999),
            'overflow': pack_item(num=MAX_INT64 + 1),
            'wrong-tag': pack_item(tag=8),
            'base-id-mismatch': {'main_item': {'item_id': 11, 'item_tag': 9,
                                                'base_item': {'item_id': 10, 'num': 1}}},
        }
        for name, item in cases.items():
            with self.subTest(name=name):
                source = self.package([item], name=f'{name}.zip')
                with self.assertRaises(ValueError):
                    capture_import.import_capture(source, self.profile, 777)
                self.assert_no_save()

    def test_zero_and_max_int64_base_counts_are_accepted(self):
        for index, count in enumerate((0, MAX_INT64)):
            with self.subTest(count=count):
                profile = self.root / f'profile-{index}'
                database = make_database(profile / 'db/lolo.db')
                write_capture_configs(profile)
                source = self.root / f'count-{index}.zip'
                with zipfile.ZipFile(source, 'w') as archive:
                    archive.writestr(MAIN_MEMBER, json.dumps(main_data(778 + index)))
                    archive.writestr(PACK_MEMBER, json.dumps({'items': [pack_item(num=count)]}))
                capture_import.import_capture(source, profile, 778 + index)
                with closing(sqlite3.connect(database)) as connection:
                    payload = connection.execute('SELECT bin_data FROM of_game WHERE user_id=?',
                                                 (778 + index,)).fetchone()[0]
                saved = json.loads(gzip.decompress(payload))
                self.assertEqual(saved['item']['itemBaseInfo']['10']['num'], count)

    def test_unknown_character_and_equipment_instance_are_rejected(self):
        unknown_character = main_data(777)
        unknown_character['characters'] = [{'character_id': 999999999}]
        with self.assertRaisesRegex(ValueError, 'unknown item|unknown'):
            capture_import.build_player(unknown_character, {}, configs=self.configs)
        weapon = {
            'main_item': {
                'item_id': 1000001, 'item_tag': 2,
                'weapon': {'weapon_id': 1000001, 'instance_id': 0},
            }
        }
        with self.assertRaisesRegex(ValueError, 'instance'):
            capture_import.build_player(main_data(777), {'PackNotice': [{'items': [weapon]}]},
                                        configs=self.configs)

    def test_economy_invariant_failure_never_calls_database_writer(self):
        source = self.package([pack_item()])
        with patch.object(capture_import, 'validate_player_economy',
                          side_effect=ValueError('synthetic invariant failure')), \
             patch.object(capture_import, '_write_database') as writer:
            with self.assertRaisesRegex(ValueError, 'invariant'):
                capture_import.import_capture(source, self.profile, 777)
        writer.assert_not_called()
        self.assert_no_save()


class CaptureRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.profile = self.root / 'profile'
        self.database = make_database(self.profile / 'db/lolo.db')
        write_capture_configs(self.profile)
        source = self.root / 'capture.zip'
        with zipfile.ZipFile(source, 'w') as archive:
            archive.writestr(MAIN_MEMBER, json.dumps(main_data(777)))
        self.source = source

    def journal(self):
        journals = list((self.profile / 'runtime/imports').glob('task-*/import-journal.json'))
        self.assertEqual(len(journals), 1)
        return json.loads(journals[0].read_text(encoding='utf-8'))

    def test_publish_failure_is_journaled_and_same_import_retry_completes(self):
        with patch.object(capture_import.os, 'rename', side_effect=OSError('synthetic publish failure')):
            with self.assertRaisesRegex(OSError, 'publish failure'):
                capture_import.import_capture(self.source, self.profile, 777)
        self.assertEqual(self.journal()['state'], 'db-committed')
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM of_game WHERE user_id=777').fetchone()[0], 1)
        self.assertFalse((self.profile / 'accounts/777').exists())
        result = capture_import.import_capture(self.source, self.profile, 777)
        self.assertEqual(result['uid'], 777)
        self.assertTrue((self.profile / 'accounts/777/import-report.json').is_file())
        self.assertEqual(list((self.profile / 'runtime/imports').glob('task-*')), [])

    def test_foreign_destination_causes_rollback_without_overwrite(self):
        with patch.object(capture_import.os, 'rename', side_effect=OSError('synthetic publish failure')):
            with self.assertRaises(OSError):
                capture_import.import_capture(self.source, self.profile, 777)
        destination = self.profile / 'accounts/777'
        destination.mkdir(parents=True)
        marker = destination / 'foreign.txt'
        marker.write_text('keep', encoding='utf-8')
        capture_import.recover_import(self.profile)
        self.assertEqual(marker.read_text(encoding='utf-8'), 'keep')
        with closing(sqlite3.connect(self.database)) as connection:
            for table in ('of_user', 'of_game', 'of_game_basic'):
                self.assertEqual(connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
        self.assertEqual(list((self.profile / 'runtime/imports').glob('task-*')), [])

    def test_crash_after_commit_before_journal_update_is_recoverable(self):
        with patch.object(capture_import.os, 'rename', side_effect=OSError('synthetic publish failure')):
            with self.assertRaises(OSError):
                capture_import.import_capture(self.source, self.profile, 777)
        journal_path = next((self.profile / 'runtime/imports').glob('task-*/import-journal.json'))
        journal = json.loads(journal_path.read_text(encoding='utf-8'))
        journal['state'] = 'staged'
        journal['createdUserMapping'] = None
        journal_path.write_text(json.dumps(journal), encoding='utf-8')
        capture_import.recover_import(self.profile)
        self.assertTrue((self.profile / 'accounts/777/import-report.json').is_file())


class OfpsImportValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ofps = self.root / 'ofps'
        resources = self.ofps / 'resources/data'
        resources.mkdir(parents=True)
        (resources / 'Item.json').write_text(json.dumps({'item': {'datas': [
            {'i_d': 10, 'new_bag_item_tag': 9},
            {'i_d': 11, 'new_bag_item_tag': 9},
            {'i_d': 1000001, 'new_bag_item_tag': 2},
        ]}}), encoding='utf-8')
        (resources / 'Character.json').write_text(json.dumps({'character': {'datas': [
            {'i_d': 101001},
        ]}}), encoding='utf-8')
        self.old_globals = (import_to_ofps.OFPS_DIR, getattr(import_to_ofps, 'Config', None),
                            getattr(import_to_ofps, 'net_pb2', None), getattr(import_to_ofps, 'ofps_db', None))
        import_to_ofps.OFPS_DIR = self.ofps
        import_to_ofps.Config = SimpleNamespace(IN_MEMORY=False)
        import_to_ofps.net_pb2 = net_pb2
        import_to_ofps.ofps_db = SimpleNamespace(init_schema=None)
        self.addCleanup(self.restore_globals)
        self.database = self.root / 'ofps.db'
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.executescript('''
                CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT, password TEXT, user_token TEXT);
                CREATE TABLE players(player_id INTEGER PRIMARY KEY, register_time INTEGER,
                    unlock_functions BLOB, team BLOB, is_online INTEGER);
            ''')
        self.backup = self.root / 'backup'
        self.raw = self.backup / 'raw'
        self.raw.mkdir(parents=True)
        player = net_pb2.PlayerMainDataRsp(player_id=777, player_name='Synthetic')
        (self.raw / '00001_PlayerMainDataRsp.bin').write_bytes(player.SerializeToString())
        self.configs = import_to_ofps.load_import_configs(self.ofps)

    def restore_globals(self):
        (import_to_ofps.OFPS_DIR, import_to_ofps.Config,
         import_to_ofps.net_pb2, import_to_ofps.ofps_db) = self.old_globals

    def item(self, item_id=10, tag=9, num=1):
        detail = net_pb2.ItemDetail()
        detail.main_item.item_id = item_id
        detail.main_item.item_tag = tag
        detail.main_item.base_item.item_id = item_id
        detail.main_item.base_item.num = num
        return detail

    def weapon(self, instance_id=42):
        detail = net_pb2.ItemDetail()
        detail.main_item.item_id = 1000001
        detail.main_item.item_tag = 2
        detail.main_item.weapon.weapon_id = 1000001
        detail.main_item.weapon.instance_id = instance_id
        return detail

    def write_pack(self, *items):
        notice = net_pb2.PackNotice()
        notice.items.extend(items)
        (self.raw / '00002_PackNotice.bin').write_bytes(notice.SerializeToString())

    def write_mail(self, *items):
        response = net_pb2.GetMailsRsp()
        mail = response.mails.add(mail_id=1)
        mail.reward.extend(items)
        (self.raw / '00003_GetMailsRsp.bin').write_bytes(response.SerializeToString())

    def assert_rejected_without_insert(self, pattern):
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, pattern):
                import_to_ofps.import_backup(self.backup, 'synthetic', 'password', db_path=self.database)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM users').fetchone()[0], 0)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM players').fetchone()[0], 0)

    def test_negative_unknown_tag_and_duplicate_instance_are_rejected_before_insert(self):
        cases = (
            ('negative', (self.item(num=-1),), '数量'),
            ('unknown', (self.item(item_id=999999999),), '未知物品'),
            ('tag', (self.item(tag=8),), 'tag'),
            ('duplicate', (self.weapon(), self.weapon()), '重复物品实例'),
        )
        for name, items, pattern in cases:
            with self.subTest(name=name):
                for path in self.raw.glob('0000[23]_*.bin'):
                    path.unlink()
                self.write_pack(*items)
                self.assert_rejected_without_insert(pattern)

    def test_int64_overflow_bit_pattern_is_rejected_before_insert(self):
        # PackNotice.items length, ItemDetail.main_item length and BaseItem
        # length are updated around an unsigned varint of 2**63. Protobuf
        # interprets that int64 bit pattern as MinInt64, which must be rejected.
        def varint(value):
            result = bytearray()
            while value >= 0x80:
                result.append((value & 0x7f) | 0x80)
                value >>= 7
            result.append(value)
            return bytes(result)
        raw = (b'\x12\x15\x0a\x13\x08\x0a\x10\x09\x2a\x0d\x08\x0a\x10'
               + varint(1 << 63))
        (self.raw / '00002_PackNotice.bin').write_bytes(raw)
        self.assert_rejected_without_insert('数量')

    def test_mail_attachments_use_the_same_validation(self):
        self.write_pack(self.item())
        self.write_mail(self.item(item_id=999999999))
        self.assert_rejected_without_insert('未知物品')

    def test_mail_negative_and_duplicate_instance_attachments_are_rejected(self):
        cases = (
            ('negative', (self.item(num=-1),), '数量'),
            ('duplicate', (self.weapon(), self.weapon()), '重复物品实例'),
            ('aggregate-overflow', (self.item(num=1), self.item(num=MAX_INT64)), '数量'),
        )
        for name, items, pattern in cases:
            with self.subTest(name=name):
                mail_path = self.raw / '00003_GetMailsRsp.bin'
                mail_path.unlink(missing_ok=True)
                self.write_pack(self.item())
                self.write_mail(*items)
                self.assert_rejected_without_insert(pattern)

    def test_zero_and_max_int64_counts_are_valid(self):
        for count in (0, MAX_INT64):
            with self.subTest(count=count):
                self.assertEqual(import_to_ofps.validate_import_item_detail(
                    self.item(num=count), self.configs, 'synthetic'), 0)

    def test_valid_backup_still_imports(self):
        database = self.root / 'valid.db'
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.executescript('''
                CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT, password TEXT, user_token TEXT);
                CREATE TABLE players(
                    player_id INTEGER PRIMARY KEY, player_name TEXT, level INTEGER, exp INTEGER,
                    sex INTEGER, world_level INTEGER, head INTEGER, team_leader_badge INTEGER,
                    sign TEXT, guild_name TEXT, character_id INTEGER, garden_like_num INTEGER,
                    register_time INTEGER, create_time INTEGER, region_name TEXT,
                    client_log_server_token TEXT, server_time_zone INTEGER,
                    phone_background INTEGER, birthday TEXT, is_hide_birthday INTEGER,
                    hide_value INTEGER, account_type INTEGER, unlock_functions BLOB, team BLOB,
                    avatar_frame INTEGER, pendant INTEGER, pet_instance_id INTEGER,
                    last_login_time INTEGER, is_online INTEGER);
                CREATE TABLE month_card(player_id INTEGER PRIMARY KEY, over_due_time INTEGER, reward_days INTEGER);
                CREATE TABLE items(player_id INTEGER, item_id INTEGER, item_detail_blob BLOB);
                CREATE TABLE instance(player_id INTEGER PRIMARY KEY, instance_id INTEGER);
                CREATE TABLE garden_info(player_id INTEGER PRIMARY KEY, like_num INTEGER, access_num INTEGER,
                    furniture_num INTEGER, furniture_limit_num INTEGER, is_open INTEGER);
                CREATE TABLE life_info(player_id INTEGER PRIMARY KEY, life_blob BLOB);
            ''')
        self.write_pack(self.item(num=5))
        with contextlib.redirect_stdout(io.StringIO()):
            player = import_to_ofps.import_backup(
                self.backup, 'synthetic', 'password', db_path=database)
        self.assertEqual(player.player_id, 777)
        with closing(sqlite3.connect(database)) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM users').fetchone()[0], 1)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM players').fetchone()[0], 1)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM items').fetchone()[0], 2)


if __name__ == '__main__':
    unittest.main()
