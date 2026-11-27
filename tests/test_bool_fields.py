"""Regressions for boolean field serialization; all data synthetic."""
import gzip
import json
import sqlite3
import sys
import unittest
from contextlib import closing
from pathlib import Path
import tempfile

REPO = Path(__file__).resolve().parent.parent
# The suite imports sibling test modules, so tests/ must be importable.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO))

import capture_import
from test_official_import import make_database, make_zip


def profile(root):
    database = make_database(root / "profiles/solo/db/lolo.db")
    return root / "profiles/solo", database


class BooleanSerializationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.profile_path, self.database = profile(self.root)

    def test_bool_model_converts_numbers_and_strings(self):
        self.assertIs(capture_import.model("bool", 1), True)
        self.assertIs(capture_import.model("bool", 0), False)
        self.assertIs(capture_import.model("bool", "1"), True)
        self.assertIs(capture_import.model("bool", "true"), True)
        self.assertIs(capture_import.model("bool", "0"), False)
        self.assertIs(capture_import.model("bool", True), True)
        self.assertIs(capture_import.model("bool", False), False)

    def test_dye_scheme_flag_is_stored_as_json_true(self):
        main = {
            "status": "StatusCode_OK", "player_id": 5550001, "player_label": 5550001,
            "player_name": "Bool", "level": 10,
            "characters": [{"character_id": 101001, "character_appearance": {"badge": 5000}}],
            "team": {"char1": 101001},
        }
        notice = {"items": [{"main_item": {"item_id": 4200373, "item_tag": 6,
                                           "outfit": {"outfit_id": 4200373,
                                                      "dye_schemes": [{"scheme_index": 0, "is_un_lock": 1},
                                                                      {"scheme_index": 1, "is_un_lock": 0}]}}}]}
        import zipfile
        source = self.root / "bool.zip"
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("json/00001_s2c_1006_PlayerMainDataRsp.json", json.dumps(main))
            archive.writestr("json/00002_s2c_200_PackNotice.json", json.dumps(notice))
        # No equipment list responses: allow the incomplete package explicitly.
        capture_import.import_capture(source, self.profile_path, 0, True, True)

        with closing(sqlite3.connect(self.database)) as connection:
            payload = json.loads(gzip.decompress(
                connection.execute("SELECT bin_data FROM of_game WHERE user_id=5550001").fetchone()[0]).decode("utf-8"))
        schemes = payload["item"]["itemFashionMap"]["4200373"]["dyeSchemes"]
        self.assertIs(schemes["0"]["isUnLock"], True)
        self.assertIs(schemes["1"]["isUnLock"], False)
        # The stored JSON must contain real booleans, not 1/0.
        raw = json.dumps(payload)
        self.assertIn('"isUnLock": true', raw)
        self.assertIn('"isUnLock": false', raw)
        self.assertNotIn('"isUnLock": 1', raw)

    def test_boolean_conversion_survives_missing_flag(self):
        # A missing flag stays absent instead of being invented as false.
        self.assertIsNone(capture_import.model("bool", None))

if __name__ == "__main__":
    unittest.main()
