"""Offline tests for official capture import: UID choice, conflicts, and DB writes.

Uses synthetic capture ZIPs and throwaway databases so a real account DB is never touched.
"""
import json
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import capture_import
import official_import


def make_zip(target: Path, main: dict, prefix: str = "") -> Path:
    payload = [json.dumps(main)]
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr(f"{prefix}json/00001_s0_s2c_1006_PlayerMainDataRsp.json", payload[0])
    return target


def make_database(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(
            """
            CREATE TABLE of_user(user_id INTEGER PRIMARY KEY, sdk_uid INTEGER UNIQUE, ban INTEGER);
            CREATE TABLE of_game(user_id INTEGER PRIMARY KEY, created_at TEXT, updated_at TEXT, bin_data BLOB);
            CREATE TABLE of_game_basic(user_id INTEGER PRIMARY KEY, nick_name TEXT, level INTEGER);
            """
        )
    return path


def main_data(uid: int, name: str = "Test", label: int | None = None) -> dict:
    return {
        "status": "StatusCode_OK",
        "player_id": uid,
        "player_label": label if label is not None else uid,
        "player_name": name,
        "level": 50,
        "characters": [{"character_id": 101001, "level": 1,
                        "character_appearance": {"badge": 5000}}],
        "team": {"char1": 101001},
    }


class OfficialImportTests(unittest.TestCase):
    def test_capture_uid_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = make_database(root / "profiles/solo/db/lolo.db")
            source = make_zip(root / "account_Test_777.zip", main_data(777))
            plan = official_import.plan(source, db, login_uid=999)
            self.assertEqual(plan["uid"], 777)
            self.assertEqual(plan["uidSource"], "capture")
            self.assertFalse(plan["conflict"])

    def test_display_label_preferred_over_internal_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = make_database(root / "profiles/solo/db/lolo.db")
            source = make_zip(root / "account_Test_5.zip", main_data(555, label=444))
            plan = official_import.plan(source, db, login_uid=None)
            self.assertEqual(plan["uid"], 444)

    def test_falls_back_to_login_uid_when_capture_has_none(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = make_database(root / "profiles/solo/db/lolo.db")
            data = main_data(0, label=0)
            data.pop("player_id")
            source = make_zip(root / "account_Test_0.zip", data)
            plan = official_import.plan(source, db, login_uid=12345)
            self.assertEqual(plan["uid"], 12345)
            self.assertEqual(plan["uidSource"], "login")

    def test_missing_uid_and_no_login_uid_is_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = make_database(root / "profiles/solo/db/lolo.db")
            data = main_data(0, label=0)
            data.pop("player_id")
            source = make_zip(root / "account_Test_0.zip", data)
            with self.assertRaises(ValueError):
                official_import.plan(source, db, login_uid=None)

    def test_existing_progress_is_a_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = make_database(root / "profiles/solo/db/lolo.db")
            with closing(sqlite3.connect(db)) as connection, connection:
                connection.execute("INSERT INTO of_user VALUES(777,777,0)")
                connection.execute("INSERT INTO of_game VALUES(777,'a','b',X'01')")
                connection.execute("INSERT INTO of_game_basic VALUES(777,'Old',9)")
            source = make_zip(root / "account_Test_777.zip", main_data(777))
            plan = official_import.plan(source, db, login_uid=None)
            self.assertTrue(plan["conflict"])
            self.assertIn("777", plan["conflictWith"])
            with self.assertRaises(ValueError) as caught:
                official_import.import_account(source, db, None)
            self.assertIn("冲突", str(caught.exception))

    def test_import_creates_account_and_preserves_others(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = make_database(root / "profiles/solo/db/lolo.db")
            with closing(sqlite3.connect(db)) as connection, connection:
                connection.execute("INSERT INTO of_user VALUES(111,111,0)")
                connection.execute("INSERT INTO of_game VALUES(111,'a','b',X'AB')")
                connection.execute("INSERT INTO of_game_basic VALUES(111,'Other',7)")
            source = make_zip(root / "account_Test_777.zip", main_data(777, "Owner"))
            result = official_import.import_account(source, db, None)
            self.assertEqual(result["uid"], 777)
            with closing(sqlite3.connect(db)) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM of_game WHERE user_id=111").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT nick_name FROM of_game_basic WHERE user_id=777").fetchone()[0], "Owner")
                self.assertIsNotNone(connection.execute("SELECT bin_data FROM of_game WHERE user_id=777").fetchone())

    def test_import_accepts_export_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = make_database(root / "profiles/solo/db/lolo.db")
            source = make_zip(root / "account_Test_777.zip", main_data(777), prefix="export/")
            plan = official_import.plan(source, db, None)
            self.assertEqual(plan["uid"], 777)

    def test_unsafe_path_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = make_database(root / "profiles/solo/db/lolo.db")
            bad = root / "bad.zip"
            with zipfile.ZipFile(bad, "w") as archive:
                archive.writestr("../escape.json", "{}")
            with self.assertRaises(ValueError):
                official_import.read_identity(bad)

    def test_capture_identity_without_personal_data(self):
        with tempfile.TemporaryDirectory() as directory:
            source = make_zip(Path(directory) / "account_Test_777.zip", main_data(777))
            player, entries = official_import.read_identity(source)
            self.assertEqual(official_import.captured_uid(player), 777)
            self.assertEqual(len(player.get("characters", [])), 1)
            self.assertEqual(len(entries["PlayerMainDataRsp"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
