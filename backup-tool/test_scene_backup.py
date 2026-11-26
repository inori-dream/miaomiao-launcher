"""Regression tests for garden detection and own-channel scene backups."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "proto_gen"))

import net_pb2          # noqa: E402
import parse_pcap       # noqa: E402
import exploration      # noqa: E402


def scene_notice(scene_id, channel=0, furniture=0, players=0, empty_garden=False):
    notice = net_pb2.SceneDataNotice(status=1)
    notice.data.scene_id = scene_id
    if channel:
        notice.data.channel_id = channel
        notice.data.channel_label = channel
    if empty_garden:
        notice.data.scene_garden_data.SetInParent()
    for index in range(furniture):
        notice.data.scene_garden_data.garden_furniture_info_map[index].furniture_id = 100 + index
    for index in range(players):
        player = notice.data.players.add()
        player.player_id = 5000 + index
        player.player_name = f"玩家{index}"
    return notice


class GardenDetectionTests(unittest.TestCase):
    def test_empty_garden_field_on_open_world_is_not_a_garden(self):
        """Official maps carry scene_garden_data: {} and must stay map snapshots."""
        scene = scene_notice(3, channel=0, empty_garden=True).data
        self.assertTrue(scene.HasField("scene_garden_data"))
        self.assertEqual(scene.scene_garden_data.ByteSize(), 0)
        self.assertFalse(parse_pcap.is_garden_scene(scene))
        self.assertIsNone(parse_pcap.collect_garden_scene(scene, {}, {}))

    def test_real_garden_with_furniture_is_a_garden(self):
        scene = scene_notice(9999, channel=777, furniture=3).data
        self.assertTrue(parse_pcap.is_garden_scene(scene))
        layouts = {}
        self.assertEqual(parse_pcap.collect_garden_scene(scene, layouts, {}), 777)
        self.assertEqual(len(layouts[777].garden_furniture_info_map), 3)

    def test_garden_scene_id_without_furniture_still_recognized(self):
        scene = scene_notice(9999, channel=888, empty_garden=True).data
        self.assertTrue(parse_pcap.is_garden_scene(scene))
        layouts = {}
        self.assertEqual(parse_pcap.collect_garden_scene(scene, layouts, {}), 888)
        self.assertEqual(layouts, {})

    def test_garden_on_non_9999_scene_with_furniture_is_kept(self):
        scene = scene_notice(1001, channel=999, furniture=2).data
        self.assertTrue(parse_pcap.is_garden_scene(scene))

    def test_exploration_keeps_open_world_snapshot(self):
        notice = scene_notice(3, empty_garden=True)
        exp = exploration.Exploration(net_pb2)
        exp.add("SceneDataNotice", notice.SerializeToString(), {"stream_id": 0})
        report = exp.summary([17541584])
        self.assertIn("3", report["maps"], "open-world snapshot was dropped from exploration")
        row = report["maps"]["3"]
        self.assertTrue(row["sources"], "snapshot evidence was not recorded")
        self.assertNotEqual(row["collection_coverage"], "not_captured")

    def test_exploration_still_skips_visiting_another_garden(self):
        notice = scene_notice(9999, channel=666, furniture=2)
        exp = exploration.Exploration(net_pb2)
        exp.add("SceneDataNotice", notice.SerializeToString(), {"stream_id": 0})
        report = exp.summary([17541584])
        self.assertEqual(report["maps"]["9999"]["collection_coverage"], "not_captured")
        self.assertEqual(report["maps"]["9999"]["sources"], [])


class OwnSceneBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name)

    def test_only_account_channel_is_archived(self):
        scenes = {
            17541584: [scene_notice(9999, channel=17541584, furniture=2, players=1).data],
            666: [scene_notice(9999, channel=666, furniture=5).data],
        }
        with contextlib.redirect_stdout(io.StringIO()):
            result = parse_pcap.write_own_scene_backup(
                self.out, scenes, 17541584, {17541584: "我自己"})
        self.assertIsNotNone(result)
        self.assertEqual(result["owner_uid"], 17541584)
        self.assertEqual(result["scenes"], [9999])
        archive = self.out / "my-scenes" / result["zip_file"]
        self.assertTrue(archive.is_file())
        index = json.loads((self.out / "my-scenes" / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(index["owner_uid"], 17541584)
        self.assertEqual(len(index["scenes"]), 1)
        self.assertEqual(index["scenes"][0]["garden_furniture"], 2)

    def test_no_own_channel_means_no_archive(self):
        scenes = {666: [scene_notice(9999, channel=666).data]}
        self.assertIsNone(parse_pcap.write_own_scene_backup(self.out, scenes, 17541584, {}))

    def test_multiple_accounts_are_ambiguous_and_skipped(self):
        scenes = {17541584: [scene_notice(9999, channel=17541584).data]}
        self.assertIsNone(parse_pcap.write_own_scene_backup(self.out, scenes, None, {}))

    def test_repeats_keep_the_richest_snapshot(self):
        small = scene_notice(9999, channel=17541584, furniture=1).data
        rich = scene_notice(9999, channel=17541584, furniture=7, players=2).data
        with contextlib.redirect_stdout(io.StringIO()):
            result = parse_pcap.write_own_scene_backup(
                self.out, {17541584: [small, rich]}, 17541584, {17541584: "我自己"})
        self.assertEqual(len(result["scenes"]), 1)
        index = json.loads((self.out / "my-scenes" / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(index["scenes"][0]["garden_furniture"], 7)

    def test_scene_summary_counts_other_objects(self):
        notice = scene_notice(1001, channel=17541584, players=3)
        decor = notice.data.room_decor_list.add()
        decor.decor_id = 1
        with contextlib.redirect_stdout(io.StringIO()):
            result = parse_pcap.write_own_scene_backup(
                self.out, {17541584: [notice.data]}, 17541584, {17541584: "我自己"})
        index = json.loads((self.out / "my-scenes" / "index.json").read_text(encoding="utf-8"))
        row = index["scenes"][0]
        self.assertEqual(row["players"], 3)
        self.assertEqual(row["room_decor"], 1)


class AccountArchiveTests(unittest.TestCase):
    """The garden must reach the ZIP the player actually opens."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name)

    def test_garden_and_scene_files_are_included(self):
        (self.out / "gardens" / "777").mkdir(parents=True)
        (self.out / "gardens" / "gardens_index.json").write_text("[]", encoding="utf-8")
        (self.out / "gardens" / "777" / "x.json").write_text("{}", encoding="utf-8")
        (self.out / "my-scenes").mkdir(parents=True)
        (self.out / "my-scenes" / "index.json").write_text("{}", encoding="utf-8")
        base = self.out / "summary.json"
        base.write_text("{}", encoding="utf-8")

        files = parse_pcap.account_archive_files(self.out, [base])
        names = {p.relative_to(self.out).as_posix() for p in files}
        self.assertIn("summary.json", names)
        self.assertIn("gardens/gardens_index.json", names)
        self.assertIn("gardens/777/x.json", names)
        self.assertIn("my-scenes/index.json", names)

    def test_missing_folders_are_not_an_error(self):
        base = self.out / "summary.json"
        base.write_text("{}", encoding="utf-8")
        files = parse_pcap.account_archive_files(self.out, [base])
        self.assertEqual([p.name for p in files], ["summary.json"])


if __name__ == "__main__":
    unittest.main()
