"""Tests for one_click_import multi-version / partial / cross-format package support."""
from __future__ import annotations

from build.project_layout import source_file
from contextlib import closing
import gzip
import json
from pathlib import Path
import sqlite3
import struct
import sys
import tempfile
import unittest
import zipfile

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(source_file(REPO, 'backup-tool')))
sys.path.insert(0, str(source_file(REPO, 'backup-tool/proto_gen')))

import capture_import  # noqa: E402
import one_click_import  # noqa: E402
from test_official_import import main_data, make_database  # noqa: E402

HOME_DDL = """
    CREATE TABLE of_home(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, scene_id INTEGER, garden_name TEXT,
        likes_num INTEGER, access_player_num INTEGER, left_like_num INTEGER,
        is_open NUMERIC, password TEXT, password_expire_time INTEGER,
        garden_furniture_info_map BLOB, placed_character_map BLOB);
    CREATE UNIQUE INDEX idx_id ON of_home(user_id, scene_id);
"""


class OneClickImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.profile = self.root / 'profiles' / 'solo'
        self.database = make_database(self.profile / 'db' / 'lolo.db')
        with closing(sqlite3.connect(self.database)) as con, con:
            con.executescript(HOME_DDL)

    def _read_saved(self, uid):
        with closing(sqlite3.connect(self.database)) as con:
            row = con.execute('SELECT bin_data FROM of_game WHERE user_id=?', (uid,)).fetchone()
            basic = con.execute('SELECT nick_name, level FROM of_game_basic WHERE user_id=?', (uid,)).fetchone()
            home = con.execute('SELECT garden_furniture_info_map FROM of_home WHERE user_id=?', (uid,)).fetchone()
        return json.loads(gzip.decompress(row[0]).decode('utf-8')), basic, home

    def test_v149_archive_with_c2s_duplicate_keys_gaps_and_midcapture_updates_imports(self):
        zpath = self.root / 'legacy_149.zip'
        m = main_data(777888, 'Legacy149')
        dup_main = '{"status":1,"player_id":777888,"player_label":777888,"player_name":"Legacy149","level":10,"level":25,"characters":[{"character_id":101001}]}'
        with zipfile.ZipFile(zpath, 'w') as zf:
            zf.writestr('json/00001_s0_s2c_1006_PlayerMainDataRsp.json', dup_main)
            zf.writestr('json/00002_s0_c2s_1001_VerifyLoginTokenReq.json', json.dumps({'token': 'req-token-val'}))
            zf.writestr('capture.pcapng', b'\x0a\x0d\x0d\x0a' + b'\x00' * 24)
            zf.writestr('summary.json', json.dumps({
                'player_uids': [777888],
                'incomplete_stream_count': 2,
                'trailing_bytes_count': 18,
                'decode_fallback_count': 1,
            }))
            zf.writestr('json/00003_s0_s2c_1043_GetWeaponRsp.json', json.dumps({'status': 1, 'weapons': [{'weapon_id': 1001, 'instance_id': 55, 'level': 1}]}))
            zf.writestr('json/00004_s0_s2c_1043_GetWeaponRsp.json', json.dumps({'status': 1, 'weapons': [{'weapon_id': 1001, 'instance_id': 55, 'level': 9}]}))

        with self.assertRaises(ValueError):
            capture_import.import_capture(zpath, self.profile, 777888)

        info = one_click_import.plan_package(zpath, profile=self.profile)
        self.assertEqual(info['uid'], 777888)
        self.assertIn('v1.4', info['versionHint'])
        res = one_click_import.import_package(zpath, self.profile)
        self.assertEqual(res['uid'], 777888)
        saved, basic, _home = self._read_saved(777888)
        self.assertEqual(basic, ('Legacy149', 25))
        self.assertEqual(saved['item']['itemWeaponMap']['55']['level'], 9)

    def test_missing_sections_and_missing_main_rsp_left_empty(self):
        zpath = self.root / 'partial_garden_weapons.zip'
        with zipfile.ZipFile(zpath, 'w') as zf:
            zf.writestr('1/json/00010_s0_s2c_1043_GetWeaponRsp.json', json.dumps({
                'weapons': [{'weapon_id': 2001, 'instance_id': 88, 'level': 5}],
            }))
            zf.writestr('1/json/00011_s0_s2c_1016_SceneDataNotice.json', json.dumps({
                'status': 1,
                'data': {
                    'scene_id': 9999,
                    'channel_id': 1,
                    'channel_label': 1,
                    'scene_garden_data': {
                        'garden_name': '测试家园',
                        'garden_furniture_info_map': {
                            '9001': {'furniture_id': 9001, 'furniture_item_id': 501, 'pos': {'x': 10}},
                        },
                    },
                },
            }))

        info = one_click_import.inspect_package(zpath)
        self.assertTrue(info['needsUid'])
        self.assertIn('weapons', info['presentSections'])
        self.assertIn('garden', info['presentSections'])
        self.assertIn('characters', info['emptySections'])
        self.assertIn('bag', info['emptySections'])

        res = one_click_import.import_package(zpath, self.profile, uid=334455)
        self.assertEqual(res['uid'], 334455)
        saved, basic, home = self._read_saved(334455)
        self.assertEqual(saved['character']['characterMap'], {})
        self.assertEqual(saved['item']['itemBaseInfo'], {})
        self.assertEqual(saved['item']['itemWeaponMap']['88']['level'], 5)
        self.assertIsNotNone(home)
        self.assertIn('9001', json.loads(home[0].decode('utf-8')))
        self.assertEqual(basic[1], 1)

    def test_raw_protobuf_bin_only_archive_imports(self):
        import net_pb2
        body = net_pb2.PlayerMainDataRsp(status=1, player_id=998877, player_label=998877, player_name='BinPlayer', level=18).SerializeToString()
        zpath = self.root / 'raw_only_998877.zip'
        with zipfile.ZipFile(zpath, 'w') as zf:
            zf.writestr('raw/00001_s0_s2c_1006_PlayerMainDataRsp.bin', body)

        res = one_click_import.import_package(zpath, self.profile)
        self.assertEqual(res['uid'], 998877)
        _saved, basic, _home = self._read_saved(998877)
        self.assertEqual(basic, ('BinPlayer', 18))

    def test_android_v31_backup_strips_secrets_and_imports(self):
        zpath = self.root / 'android_backup.zip'
        with zipfile.ZipFile(zpath, 'w') as zf:
            zf.writestr('requests/login.json', json.dumps({'sdk_token': 'secret-tok', 'password': 'pw'}))
            zf.writestr('player-556677-20261128.json', json.dumps({
                'player': {'player_id': 556677, 'player_label': 556677, 'player_name': 'AndroidHero', 'level': 33},
                'responses': [
                    {'msg_name': 'GetArmorRsp', 'decoded': {'status': 1, 'armors': [{'armor_id': 3001, 'instance_id': 123, 'level': 7}]}},
                ],
                'access_token': 'drop-me',
            }))
        res = one_click_import.import_package(zpath, self.profile)
        self.assertEqual(res['uid'], 556677)
        saved, basic, _home = self._read_saved(556677)
        self.assertEqual(basic, ('AndroidHero', 33))
        self.assertEqual(saved['item']['itemArmorMap']['123']['level'], 7)
        account_dir = self.profile / 'accounts' / '556677'
        self.assertNotIn('drop-me', (account_dir / 'main-data.json').read_text(encoding='utf-8'))

    def test_sqlite_db_migration_and_directory_import(self):
        old_db = make_database(self.root / 'old_149.db')
        old_payload = gzip.compress(json.dumps({
            'instanceIndex': 2,
            'character': {'characterMap': {'101001': {'itemId': 101001}}, 'placedCharacters': []},
            'item': {'itemBaseInfo': {}, 'itemWeaponMap': {}, 'itemArmorMap': {}, 'itemPosterMap': {},
                     'itemFashionMap': {}, 'itemInscriptionMap': {}, 'itemPetInfoMap': {}, 'itemHeadMap': {}, 'furnitureItemMap': {}, 'instanceIndex': 2},
            'team': {'TeamInfo': {'Char1': 101001, 'Char2': 0, 'Char3': 0}},
            'scene': {'worldLevel': 2, 'cur_pet_instance_id': 0},
        }).encode('utf-8'))
        with closing(sqlite3.connect(old_db)) as con, con:
            con.execute('INSERT INTO of_user(user_id,sdk_uid,ban) VALUES(112233,112233,0)')
            con.execute("INSERT INTO of_game(user_id,created_at,updated_at,bin_data) VALUES(112233,'2026-01-01','2026-01-01',?)", (old_payload,))
            con.execute("INSERT INTO of_game_basic(user_id,nick_name,level) VALUES(112233,'OldDbUser',41)")

        res = one_click_import.import_package(old_db, self.profile)
        self.assertEqual(res['uid'], 112233)
        saved, basic, _home = self._read_saved(112233)
        self.assertEqual(basic, ('OldDbUser', 41))
        self.assertEqual(saved['scene']['worldLevel'], 2)

        # Conflict requires allow_overwrite=True
        with self.assertRaisesRegex(ValueError, 'never overwritten'):
            one_click_import.import_package(old_db, self.profile, allow_overwrite=False)
        res2 = one_click_import.import_package(old_db, self.profile, allow_overwrite=True)
        self.assertTrue(res2['overwroteExisting'])

        # Unpacked folder import
        folder = self.root / 'unpacked'
        folder.mkdir()
        (folder / 'main-data.json').write_text(json.dumps(main_data(445566, 'FolderUser')), encoding='utf-8')
        res3 = one_click_import.import_package(folder, self.profile)
        self.assertEqual(res3['uid'], 445566)

    def test_path_traversal_still_rejected(self):
        bad = self.root / 'evil.zip'
        with zipfile.ZipFile(bad, 'w') as zf:
            zf.writestr('../escape.json', '{}')
        with self.assertRaisesRegex(ValueError, '穿越'):
            one_click_import.inspect_package(bad)


if __name__ == '__main__':
    unittest.main()
