"""Regressions for undecoded / malformed capture shapes; all data is synthetic."""
import json
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

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


if __name__ == "__main__":
    unittest.main()
