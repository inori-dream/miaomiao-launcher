from build.project_layout import source_file
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import sqlite3
import gzip
import zipfile
import os
import subprocess
import sys
import ctypes
from contextlib import closing

from mitmproxy import http
import lolo_redirect
import repair_version
import capture_import
import room_gateway


class VersionRepairTests(unittest.TestCase):
    def test_backup_and_idempotence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = root / 'OverField Game/launcher_Data'
            for relative, version in [
                ('StreamingAssets/yoo/DefaultPackage/PackageManifest_DefaultPackage.version', '2026-10-21-14-05-50'),
                ('yoo/DefaultPackage/ManifestFiles/PackageManifest_DefaultPackage.version', '2026-11-07-22-39-35'),
            ]:
                path = client / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(version, encoding='utf-8')
            target = root / 'LoloServer/data/ClientVersion.json'
            target.parent.mkdir(parents=True)
            before = b'{"VersionMap":{"other":"unchanged"}}'
            target.write_bytes(before)
            dry_run = repair_version.repair(root, False)
            self.assertFalse(dry_run['applied'])
            self.assertEqual(target.read_bytes(), before)
            applied = repair_version.repair(root, True)
            self.assertEqual(Path(applied['backup']).read_bytes(), before)
            self.assertFalse(target.read_bytes().startswith(b'\xef\xbb\xbf'))
            mapping = json.loads(target.read_text())['VersionMap']
            self.assertEqual(mapping['other'], 'unchanged')
            self.assertEqual(mapping['2026-10-21-14-05-50'], '2026-10-21-14-05-50_2026-11-07-22-39-35')
            self.assertFalse(repair_version.repair(root, True)['applied'])
            self.assertEqual(len(list((root / 'LoloServer/backup').glob('*.json'))), 1)

    def test_bad_manifest_never_changes_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bad = root / 'OverField Game/launcher_Data/StreamingAssets/yoo/DefaultPackage/PackageManifest_DefaultPackage.version'
            bad.parent.mkdir(parents=True)
            bad.write_text('bad-version')
            with self.assertRaises(ValueError):
                repair_version.repair(root, True)
            self.assertFalse((root / 'LoloServer/data/ClientVersion.json').exists())


class ForwardingTests(unittest.TestCase):
    def test_only_game_destinations(self):
        cases = [
            ('DSP-PROD-OF.INUTAN.COM.', 18881, '/dispatch/region_info', True),
            ('sdkapi-sz.inutan.com', 443, '/sdk/token', True),
            ('14.103.225.205', 18881, '/dispatch/region_info', True),
            ('14.103.224.57', 18881, '/dispatch/client_hot_update?x=1', True),
            ('14.103.224.57', 443, '/dispatch/region_info', False),
            ('14.103.224.57', 18881, '/other', False),
            ('api.example.com', 443, '/v1/responses', False),
            ('sdkapi.inutan.com.evil.example', 443, '/sdk/token', False),
        ]
        for host, port, path, expected in cases:
            with self.subTest(host=host, port=port, path=path):
                self.assertEqual(lolo_redirect.is_target(host, port, path), expected)

    def test_credentials_preserved_but_not_logged(self):
        flow = http.HTTPFlow(None, None)
        flow.request = http.Request.make('POST', 'http://dsp-prod-of.inutan.com:18881/dispatch/region_info?token=secret-value', b'private-body')
        lolo_redirect.request(flow)
        self.assertEqual(flow.request.host, '127.0.0.1')
        self.assertEqual(flow.request.port, 8080)
        self.assertIn('token=secret-value', flow.request.path)
        self.assertEqual(flow.request.content, b'private-body')
        flow.response = http.Response.make(200, b'private-response')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            lolo_redirect.response(flow)
        self.assertIn('status=200', output.getvalue())
        for secret in ('secret-value', 'private-body', 'private-response'):
            self.assertNotIn(secret, output.getvalue())
            self.assertNotIn(secret, flow.metadata['lolo_origin'])


class DataAndUidTests(unittest.TestCase):
    def capture(self, root):
        path=root/'capture.zip'
        main={'status':'StatusCode_OK','player_id':41,'player_label':40,'player_name':'Test','level':50,'characters':[{'character_id':101001,'level':1,'character_appearance':{'badge':5000}}],'team':{'char1':101001}}
        with zipfile.ZipFile(path,'w') as archive:archive.writestr('export/json/00001_s0_s2c_1006_PlayerMainDataRsp.json',json.dumps(main))
        return path

    def test_public_uid_and_other_accounts_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);profile=root/'profile';(profile/'db').mkdir(parents=True);database=profile/'db/lolo.db'
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.executescript('CREATE TABLE of_user(user_id INTEGER PRIMARY KEY,sdk_uid INTEGER UNIQUE,ban INTEGER); CREATE TABLE of_game(user_id INTEGER PRIMARY KEY,created_at TEXT,updated_at TEXT,bin_data BLOB); CREATE TABLE of_game_basic(user_id INTEGER PRIMARY KEY,nick_name TEXT,level INTEGER); INSERT INTO of_user VALUES(99,99,0); INSERT INTO of_game VALUES(99,"old","old",X\'010203\'); INSERT INTO of_game_basic VALUES(99,"Other",8);')
            path=self.capture(root);info=capture_import.inspect(path)
            self.assertEqual((info['uid'],info['internalId']),(40,41))
            result=capture_import.import_capture(path,profile,40)
            self.assertTrue(Path(result['backup']).exists())
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(connection.execute('SELECT bin_data FROM of_game WHERE user_id=99').fetchone()[0],b'\x01\x02\x03')
                self.assertEqual(connection.execute('SELECT level FROM of_game_basic WHERE user_id=40').fetchone()[0],50)
                payload=connection.execute('SELECT bin_data FROM of_game WHERE user_id=40').fetchone()[0]
                self.assertIn('101001',json.loads(gzip.decompress(payload))['character']['characterMap'])
            with self.assertRaises(ValueError):capture_import.import_capture(path,profile,41)
            with self.assertRaises(ValueError):capture_import.import_capture(path,profile,40)

    def test_new_profiles_never_inherit_template_accounts(self):
        import profile_manager
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);server=root/'LoloServer'
            (server/'data').mkdir(parents=True);(server/'Resource').mkdir();(server/'db').mkdir()
            (server/'Lolo.exe').write_bytes(b'synthetic-not-executable')
            (server/'config.json').write_text(json.dumps({'HttpNet':{},'GateWay':{},'LogServer':{},'DB':{}}))
            origin=server/'db/lolo.db'
            with closing(sqlite3.connect(origin)) as connection, connection:
                connection.executescript('CREATE TABLE of_user(user_id INTEGER PRIMARY KEY,sdk_uid INTEGER,ban INTEGER); CREATE TABLE of_game(user_id INTEGER PRIMARY KEY,bin_data BLOB); CREATE TABLE of_game_basic(user_id INTEGER PRIMARY KEY,nick_name TEXT,level INTEGER); INSERT INTO of_user VALUES(42,42,0); INSERT INTO of_game VALUES(42,X\'010203\'); INSERT INTO of_game_basic VALUES(42,"SyntheticOwner",50);')
            for mode in ('solo','host'):
                result=profile_manager.prepare(root,root/'profiles',mode,'192.168.1.2')
                self.assertFalse(result['imported'])
                database=Path(result['database'])
                with closing(sqlite3.connect(database)) as connection:
                    for table in ('of_user','of_game','of_game_basic'):
                        self.assertEqual(connection.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)
                capture_import.ensure_uid(database,1000000)
                profile_manager.prepare(root,root/'profiles',mode,'192.168.1.2')
                with closing(sqlite3.connect(database)) as connection:
                    self.assertEqual(connection.execute('SELECT user_id FROM of_user').fetchall(),[(1000000,)])
            with closing(sqlite3.connect(origin)) as connection:
                self.assertEqual(connection.execute('SELECT user_id FROM of_user').fetchall(),[(42,)])

    def test_unsafe_zip_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'bad.zip'
            with zipfile.ZipFile(path,'w') as archive:archive.writestr('../world-state.json','{}')
            with self.assertRaises(ValueError):capture_import.read_capture(path)

    def test_native_uid_callback(self):
        previous=os.environ.get('LOLO_SDK_UID');os.environ['LOLO_SDK_UID']='1000000'
        callback_type=ctypes.CFUNCTYPE(None,ctypes.c_char_p,ctypes.c_int);messages=[]
        callback=callback_type(lambda data,length:messages.append(json.loads(ctypes.string_at(data,length))))
        try:
            library=ctypes.CDLL(str(source_file(Path(__file__).resolve().parent.parent, 'LocalUidSDK.dll')))
            library.SDKInit.argtypes=[ctypes.c_char_p,ctypes.c_void_p,ctypes.c_int,ctypes.c_int,callback_type]
            library.SDKLogin.argtypes=[callback_type]
            self.assertEqual(library.SDKInit(b'{}',None,0,1,callback),0)
            self.assertEqual(library.SDKLogin(callback),0)
            self.assertEqual(messages[-1]['data']['uid'],'1000000')
            self.assertEqual(messages[-1]['code'],0)
        finally:
            if previous is None:os.environ.pop('LOLO_SDK_UID',None)
            else:os.environ['LOLO_SDK_UID']=previous

    def test_native_uid_rejects_invalid_values(self):
        script = """
import ctypes, json
from pathlib import Path
callback_type = ctypes.CFUNCTYPE(None, ctypes.c_char_p, ctypes.c_int)
messages = []
callback = callback_type(lambda data, length: messages.append(json.loads(ctypes.string_at(data, length))))
library = ctypes.CDLL(str(Path('LocalUidSDK.dll').resolve()))
library.SDKLogin.argtypes = [callback_type]
print(json.dumps({'code': library.SDKLogin(callback), 'messages': messages}))
"""
        for value in ('0', '4294967296', '10000000000', 'abc', '-1', ' 1000000'):
            environment = dict(os.environ, LOLO_SDK_UID=value)
            result = subprocess.run([sys.executable, '-c', script], cwd=source_file(Path(__file__).resolve().parent.parent, 'LocalUidSDK.dll').parent,
                                    env=environment, capture_output=True, text=True, check=True)
            payload = json.loads(result.stdout)
            self.assertEqual(payload['code'], -1, value)
            self.assertEqual(payload['messages'], [], value)
        environment = dict(os.environ)
        environment.pop('LOLO_SDK_UID', None)
        result = subprocess.run([sys.executable, '-c', script], cwd=source_file(Path(__file__).resolve().parent.parent, 'LocalUidSDK.dll').parent,
                                env=environment, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)['code'], -1)
        environment['LOLO_SDK_UID'] = '4294967295'
        result = subprocess.run([sys.executable, '-c', script], cwd=source_file(Path(__file__).resolve().parent.parent, 'LocalUidSDK.dll').parent,
                                env=environment, capture_output=True, text=True, check=True)
        payload = json.loads(result.stdout)
        self.assertEqual(payload['code'], 0)
        self.assertEqual(payload['messages'][-1]['data']['uid'], '4294967295')

    def test_snappy_overlap_and_invalid_copy(self):
        self.assertEqual(room_gateway.snappy(b'\x09\x08abc\x16\x03\x00'),b'abcabcabc')
        with self.assertRaises(ValueError):room_gateway.snappy(b'\x09\x16\x00\x00')


if __name__ == '__main__':
    unittest.main(verbosity=2)
