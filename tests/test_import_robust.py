"""Regressions for undecoded / malformed capture shapes; all data is synthetic."""
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
# SIBLING_TESTS: this suite imports sibling test modules by name.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import capture_import
from test_official_import import make_database


def build_zip(target, main, extra=None, prefix="json/"):
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr(f"{prefix}00001_s2c_1006_PlayerMainDataRsp.json", json.dumps(main))
        for name, payload in (extra or {}).items():
            archive.writestr(f"{prefix}{name}", json.dumps(payload))
    return target


def profile(root):
    database = make_database(root / "profiles/solo/db/lolo.db")
    return root / "profiles/solo", database


class RobustImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.profile, self.database = profile(self.root)

    def run_import(self, path, uid=0, allow=False, replace=True):
        return capture_import.import_capture(path, self.profile, uid, replace, allow)

    def placeholder_main(self, uid=7654321, label=7654321):
        return {
            "status": "StatusCode_OK", "player_id": uid, "player_label": label,
            "player_name": "Synthetic", "level": 42,
            "characters": [{
                "character_id": 101001,
                "character_appearance": {"field_1": 5016, "field_2": 4255},
                "equipment_presets": [{"weapon": 787, "armors": [{"_bytes": ""}, {"field_1": 1}], "posters": [{"field_2": 555}]}],
                "outfit_presets": [{"hat": 4021021, "outfit_hide_info": {"_bytes": ""}}],
            }],
            "team": [{"char1": 101003, "char2": 403001}],
            "appearance": {"_bytes": ""},
        }

    def decoded_main(self, uid=777):
        return {
            "status": "StatusCode_OK", "player_id": uid, "player_label": uid,
            "player_name": "Decoded", "level": 50,
            "characters": [{"character_id": 101001, "character_appearance": {"badge": 5000},
                            "skill_list": [{"skill_id": 2010430, "skill_level": 1}],
                            "equipment_presets": [{"preset_index": 0, "weapon": 787,
                                                   "armors": [{"equip_type": 1, "armor_id": 88}],
                                                   "posters": [{"poster_index": 2, "poster_id": 99}]}],
                            "outfit_presets": [{"preset_index": 0, "hat": 1}]}],
            "team": {"char1": 101001},
        }

    def test_undecoded_package_is_refused_by_default(self):
        source = build_zip(self.root / "a.zip", self.placeholder_main())
        with self.assertRaisesRegex(ValueError, "解码不完整"):
            self.run_import(source)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM of_game").fetchone()[0], 0)

    def test_dict_shaped_dye_schemes_no_longer_crashes(self):
        main = self.decoded_main()
        notice = {"items": [{"main_item": {"item_id": 4200373, "item_tag": 6,
                                           "outfit": {"outfit_id": 4200373, "dye_schemes": {"field_3": 1}}}}]}
        source = build_zip(self.root / "b.zip", main, {"00002_s2c_200_PackNotice.json": notice})
        result = self.run_import(source)
        self.assertTrue(Path(result["backup"]).is_file())

    def test_list_shaped_team_is_accepted(self):
        main = self.decoded_main()
        main["team"] = [{"char1": 101003, "char2": 403001}]
        result = self.run_import(build_zip(self.root / "c.zip", main))
        self.assertEqual(result["uid"], 777)

    def test_missing_appearance_and_skills_are_tolerated(self):
        main = self.decoded_main()
        main["characters"][0].pop("character_appearance")
        main["characters"][0].pop("skill_list")
        main["appearance"] = None
        self.run_import(build_zip(self.root / "d.zip", main))

    def test_placeholder_presets_do_not_inject_fake_equipment(self):
        main = self.decoded_main()
        main["characters"][0]["equipment_presets"] = [{"weapon": 0, "armors": [{"_bytes": ""}], "posters": [{"field_1": 2}]}]
        source = build_zip(self.root / "e.zip", main)
        self.run_import(source, allow=True)
        payload = self.read_payload()
        preset = payload["character"]["characterMap"]["101001"]["equipmentPresets"]["0"]
        self.assertEqual(preset["armors"], {})
        self.assertEqual(preset["posters"], {})

    def test_incomplete_package_can_be_imported_only_when_allowed(self):
        source = build_zip(self.root / "f.zip", self.placeholder_main())
        result = self.run_import(source, allow=True)
        self.assertTrue(result["quality"]["incomplete"])
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM of_game").fetchone()[0], 1)

    def test_decoded_package_from_old_format_still_imports(self):
        result = self.run_import(build_zip(self.root / "g.zip", self.decoded_main()))
        self.assertEqual(result["uid"], 777)
        self.assertFalse(result["quality"]["incomplete"])

    def read_payload(self):
        import gzip
        with closing(sqlite3.connect(self.database)) as connection:
            row = connection.execute("SELECT bin_data FROM of_game ORDER BY rowid DESC LIMIT 1").fetchone()
        return json.loads(gzip.decompress(row[0]).decode("utf-8"))


    def test_schema_restore_renames_known_fallback_fields(self):
        import schema_restore
        item = {"item_id": 1001001, "item_tag": 2,
                "weapon": {"field_1": 1001001, "field_2": 151, "field_8": 26, "field_99": 7}}
        restored, unresolved = schema_restore.restore_item(item)
        self.assertEqual(restored["weapon"]["weapon_id"], 1001001)
        self.assertEqual(restored["weapon"]["instance_id"], 151)
        self.assertEqual(restored["weapon"]["level"], 26)
        self.assertEqual(restored["weapon"]["field_99"], 7)
        self.assertEqual(unresolved, 1)

    def test_schema_restore_leaves_named_fields_untouched(self):
        import schema_restore
        item = {"weapon": {"weapon_id": 5, "instance_id": 6, "level": 3}}
        restored, unresolved = schema_restore.restore_item(item)
        self.assertEqual(restored["weapon"], {"weapon_id": 5, "instance_id": 6, "level": 3})
        self.assertEqual(unresolved, 0)

    def test_restored_fields_reach_the_stored_payload(self):
        main = self.decoded_main()
        main["characters"][0]["equipment_presets"] = [{"weapon": 787, "armors": [{"field_1": 2, "field_2": 909}], "posters": []}]
        notice = {"items": [{"main_item": {"item_id": 1001001, "item_tag": 2,
                                           "weapon": {"field_1": 1001001, "field_2": 151, "field_8": 26}}}]}
        source = build_zip(self.root / "h.zip", main, {"00002_s2c_200_PackNotice.json": notice})
        self.run_import(source, allow=True)
        payload = self.read_payload()
        preset = payload["character"]["characterMap"]["101001"]["equipmentPresets"]["0"]
        self.assertEqual(preset["armors"]["2"]["instanceId"], 909)
        self.assertEqual(payload["item"]["itemWeaponMap"]["151"]["level"], 26)


class GardenImportTests(unittest.TestCase):
    """The garden lives in of_home, a table the importer used to skip entirely."""

    HOME_DDL = """
        CREATE TABLE of_home(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, scene_id INTEGER, garden_name TEXT,
            likes_num INTEGER, access_player_num INTEGER, left_like_num INTEGER,
            is_open NUMERIC, password TEXT, password_expire_time INTEGER,
            garden_furniture_info_map BLOB, placed_character_map BLOB);
        CREATE UNIQUE INDEX idx_id ON of_home(user_id, scene_id);
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.profile, self.database = profile(self.root)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.executescript(self.HOME_DDL)
        self.uid = 5556667

    def main(self):
        return {"status": "StatusCode_OK", "player_id": self.uid, "player_label": self.uid,
                "player_name": "Gardener", "level": 30, "characters": []}

    def garden_notice(self, furniture, channel=None, scene_id=9999):
        return {"data": {
            "scene_id": scene_id,
            "channel_id": channel if channel is not None else self.uid,
            "channel_label": channel if channel is not None else self.uid,
            "scene_garden_data": {
                "likes_num": 1234, "access_player_num": 56, "left_like_num": 7,
                "garden_name": "小花园", "is_open": True, "password": "",
                "garden_furniture_info_map": furniture,
            },
        }}

    def import_garden(self, furniture, **kwargs):
        notice = self.garden_notice(furniture, **kwargs)
        source = build_zip(self.root / "g.zip", self.main(),
                           {"00003_s2c_1016_SceneDataNotice.json": notice})
        capture_import.import_capture(source, self.profile, self.uid, True, True)

    def read_home(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM of_home WHERE user_id=?",
                                     (self.uid,)).fetchone()
        return dict(row) if row else None

    def read_payload(self):
        import gzip
        with closing(sqlite3.connect(self.database)) as connection:
            row = connection.execute(
                "SELECT bin_data FROM of_game ORDER BY rowid DESC LIMIT 1").fetchone()
        return json.loads(gzip.decompress(row[0]).decode("utf-8"))

    def test_furniture_reaches_of_home_as_json(self):
        self.import_garden({
            "29855943319617915": {"furniture_id": "29855943319617915",
                                  "furniture_item_id": 5041102,
                                  "pos": {"x": 1815, "z": -1143},
                                  "rotation": {"y": 19745}},
        })
        home = self.read_home()
        self.assertIsNotNone(home, "of_home row was not written")
        self.assertEqual(home["scene_id"], 9999)
        self.assertEqual(home["likes_num"], 1234)
        self.assertEqual(home["access_player_num"], 56)
        payload = json.loads(home["garden_furniture_info_map"].decode("utf-8"))
        self.assertEqual(len(payload), 1)
        item = payload["29855943319617915"]
        self.assertEqual(item["furnitureId"], 29855943319617915)
        self.assertEqual(item["furnitureItemId"], 5041102)
        # The server decodes Vector3 without omitempty, so every axis must exist.
        self.assertEqual(item["pos"], {"x": 1815, "y": 0, "z": -1143})
        self.assertEqual(item["rotation"], {"x": 0, "y": 19745, "z": 0})

    def test_garden_of_another_channel_is_ignored(self):
        self.import_garden({"1": {"furniture_id": "1", "furniture_item_id": 9}},
                           channel=999999)
        self.assertIsNone(self.read_home())

    def test_placed_furniture_counts_reach_the_item_map(self):
        """AddFurnitureItem increments this map; skipping it under-counts placed items."""
        main = self.main()
        main["furniture_item_info"] = [
            {"item_id": 5041102, "num": "1"},
            {"item_id": 5043911, "num": "2"},
        ]
        source = build_zip(self.root / "f.zip", main, {})
        capture_import.import_capture(source, self.profile, self.uid, True, True)
        payload = self.read_payload()
        furniture = payload["item"]["furnitureItemMap"]
        self.assertEqual(furniture["5041102"]["num"], 1)
        self.assertEqual(furniture["5043911"]["num"], 2)
        self.assertEqual(furniture["5041102"]["itemId"], 5041102)

    def test_non_garden_scene_is_ignored(self):
        self.import_garden({"1": {"furniture_id": "1", "furniture_item_id": 9}},
                           scene_id=1001)
        self.assertIsNone(self.read_home())

    def test_reimport_replaces_the_row_instead_of_duplicating(self):
        # This importer never overwrites an existing account (a save is only ever
        # replaced by deleting the profile), so the replacement path is exercised
        # on a second, empty profile to prove of_home stays single-row per garden.
        self.import_garden({"1": {"furniture_id": "1", "furniture_item_id": 9}})
        with closing(sqlite3.connect(self.database)) as connection:
            rows = connection.execute("SELECT COUNT(*) FROM of_home").fetchone()[0]
        self.assertEqual(rows, 1)
        payload = json.loads(self.read_home()["garden_furniture_info_map"].decode("utf-8"))
        self.assertEqual(list(payload), ["1"])
        # A second capture for the same UID is refused instead of silently replacing.
        with self.assertRaises(ValueError):
            self.import_garden({"2": {"furniture_id": "2", "furniture_item_id": 8}})


if __name__ == "__main__":
    unittest.main()
