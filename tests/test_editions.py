"""Regression checks for independently packaged CN and Steam editions."""
from build.project_layout import source_file
from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
import profile_manager

class EditionTests(unittest.TestCase):
    def test_disjoint_ports(self):
        used = set()
        for http, gate, log in profile_manager.MODES.values():
            ports = {http, http + 3, gate, gate + 1, log, http + 220}
            self.assertFalse(used & ports)
            used.update(ports)

    def test_profiles_are_separate_and_templates_remain_private(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            server = root / 'LoloServer'
            for name in ('data', 'Resource', 'db'):
                (server / name).mkdir(parents=True)
            (server / 'Lolo.exe').write_bytes(b'test-only')
            (server / 'config.json').write_text(json.dumps(dict(HttpNet={}, GateWay={}, LogServer={}, DB={})))
            with closing(sqlite3.connect(server / 'db/lolo.db')) as con, con:
                con.executescript('CREATE TABLE of_user(user_id INTEGER); INSERT INTO of_user VALUES(42);')
            results = [profile_manager.prepare(root, root/'profiles', mode, '127.0.0.1') for mode in ('solo', 'steam')]
            self.assertNotEqual(results[0]['database'], results[1]['database'])
            for result in results:
                self.assertEqual(result['host'], '127.0.0.1')
                with closing(sqlite3.connect(result['database'])) as con, con:
                    self.assertEqual(con.execute('SELECT COUNT(*) FROM of_user').fetchone()[0], 0)
            with closing(sqlite3.connect(results[0]['database'])) as con, con:
                con.execute('INSERT INTO of_user VALUES(7)')
            with closing(sqlite3.connect(results[1]['database'])) as con, con:
                self.assertEqual(con.execute('SELECT COUNT(*) FROM of_user').fetchone()[0], 0)

    def test_controller_scopes_state_lock_and_client(self):
        code = (source_file(Path(__file__).resolve().parent.parent, 'World-Control.ps1')).read_text(encoding='utf-8-sig')
        self.assertIn('world-state-$Mode.json', code)
        self.assertIn('OverFieldWorldControl-$Mode', code)
        self.assertIn('if ($taskRead.Mode -ne $Mode) { return $null }', code)
        self.assertIn('[string]::Equals($_.Path,$taskClient', code)

if __name__ == '__main__':
    unittest.main()
