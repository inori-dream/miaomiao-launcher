"""Conservative, export-only exploration evidence. Never writes a database."""
import hashlib
import json
from collections import defaultdict
from pathlib import Path

PACKETS = {"SceneProcessListRsp", "GetCollectItemIdsRsp", "SceneDataNotice",
           "CollectingRsp", "CollectionRewardRsp", "GetCollectMoonInfoRsp"}


class Exploration:
    def __init__(self, proto):
        self.proto = proto
        path = Path(__file__).with_name("exploration_catalog.json")
        content = path.read_bytes()
        self.catalog = json.loads(content.decode("utf-8-sig"))
        self.catalog_sha256 = hashlib.sha256(content).hexdigest()
        self.locations = {int(item): (int(scene), int(kind))
                          for scene, row in self.catalog["maps"].items()
                          for kind, ids in row["collections"].items() for item in ids}
        self.points = defaultdict(set)
        self.items = defaultdict(set)
        self.boxes = defaultdict(set)
        self.moons = defaultdict(set)
        self.emotions = defaultdict(set)
        self.sources = defaultdict(list)
        self.unknown = set()
        self.evidence = []
        self.scenes = set()

    def add(self, name, body, source):
        if name not in PACKETS:
            return
        data = getattr(self.proto, name).FromString(body)
        if hasattr(data, "status") and data.status != self.proto.StatusCode_OK:
            return
        self.evidence.append(dict(source, message=name))
        def record(scene):
            self.sources[int(scene)].append(dict(source, message=name))
        def item(item_id):
            if not item_id:
                return
            location = self.locations.get(item_id)
            if location:
                self.items[location[0]].add(item_id)
                record(location[0])
            else:
                self.unknown.add(item_id)
        def groups(collections):
            for group in collections:
                for item_id in group.item_map:
                    item(item_id)
        if name == "SceneProcessListRsp":
            for entry in data.scene_process_list:
                if entry.scene_id:
                    self.points[entry.scene_id].add(entry.process)
                    record(entry.scene_id)
        elif name == "GetCollectItemIdsRsp":
            for value in data.item_ids:
                item(value)
        elif name == "CollectingRsp":
            groups(data.collections)
        elif name == "CollectionRewardRsp":
            item(data.collection_reward_data.item_id)
        elif name == "SceneDataNotice":
            scene = data.data
            # Visiting another player's garden is not account exploration.
            # The official server also attaches an EMPTY scene_garden_data to
            # ordinary open-world snapshots, so presence alone is not enough.
            carried_layout = (scene.HasField("scene_garden_data")
                              and scene.scene_garden_data.ByteSize() > 0)
            if scene.scene_id == 9999 or carried_layout:
                return
            if scene.scene_id:
                self.scenes.add(scene.scene_id)
                record(scene.scene_id)
                groups(scene.collections)
                configured = self.catalog["maps"].get(str(scene.scene_id), {}).get("boxes", {})
                for box in scene.treasure_boxes:
                    if box.state == self.proto.TreasureBoxState_Open:
                        if configured.get(str(box.index)) == box.box_id:
                            self.boxes[scene.scene_id].add(box.box_id)
                        else:
                            self.unknown.add(box.box_id)
        elif name == "GetCollectMoonInfoRsp" and data.scene_id:
            self.moons[data.scene_id].update(set(data.collected_moon_ids) - {0})
            self.emotions[data.scene_id].update(m.moon_id for m in data.emotion_moons
                                               if m.interacted and m.moon_id)
            record(data.scene_id)

    def summary(self, player_uids):
        ambiguous = len(player_uids) != 1
        maps = {}
        for scene in sorted(set(map(int, self.catalog["maps"])) | set(self.sources)):
            row = self.catalog["maps"].get(str(scene), {})
            collections = row.get("collections", {})
            def category(kinds, total):
                directory = {i for kind, ids in collections.items() if int(kind) in kinds for i in ids}
                observed = sorted(self.items[scene] & directory)
                return {"collected": len(observed) if observed and not ambiguous else None,
                        "observed_count": len(observed) if observed else None, "ids": observed,
                        "total": total, "catalog_id_count": len(directory) if row else None,
                        "total_verified": False,
                        "mismatch": total is not None and total != len(directory),
                        "percentage": None}
            crystals = category({8}, row.get("crystals_total"))
            item_kinds = set(map(int, collections)) - {8, 10, 11}
            items = category(item_kinds, None)
            boxes = sorted(self.boxes[scene])
            values = sorted(self.points[scene])
            maps[str(scene)] = {
                "collection_coverage": "scene_snapshot_partial" if scene in self.scenes else
                                       "observed_evidence" if self.sources[scene] else "not_captured",
                "complete": False, "sources": self.sources[scene],
                "points": {"collected": max(values) if values and not ambiguous else None,
                           "observed_values": values, "total": row.get("points_total"),
                           "total_verified": False, "percentage": None},
                "items": items, "crystals": crystals,
                "one_time_boxes": {"collected": len(boxes) if boxes and not ambiguous else None,
                                   "ids": boxes, "total": None,
                                   "catalog_id_count": len(row.get("boxes", {})) if row else None,
                                   "total_verified": False, "percentage": None},
                "moons": {"collected_ids": sorted(self.moons[scene]),
                          "interacted_emotion_ids": sorted(self.emotions[scene])}}
        return {"schema_version": 1, "resource_version": self.catalog["resource_version"],
                "catalog": dict(self.catalog["provenance"], local_catalog_sha256=self.catalog_sha256),
                "account_uid": player_uids[0] if not ambiguous else None,
                "attribution": "single_observed_uid" if not ambiguous else "unattributed_evidence",
                "merge_policy": "monotonic_observed_union_and_max_not_latest_snapshot",
                "report_available": True, "evidence_available": bool(self.evidence),
                "summary": "已生成探索证据报告；未知不当0，总量未验证，非全图完整快照。",
                "database_imported": False, "unknown_ids": sorted(self.unknown),
                "evidence": self.evidence, "maps": maps}


def render_report(report):
    lines = ["地图探索证据（非全图完整快照；总量未经原资源验证）",
             "归属: " + report["attribution"], "来源: " + report["catalog"]["source"],
             "本地目录 SHA256: " + report["catalog"]["local_catalog_sha256"]]
    for scene, row in report["maps"].items():
        c = row["crystals"]
        count = "未确认/未采到" if c["collected"] is None else str(c["collected"])
        lines.append(f"地图 {scene}: {row['collection_coverage']}；水晶已观测 {count}；声明总量 {c['total']}；目录ID数 {c['catalog_id_count']}；mismatch={c['mismatch']}")
    lines.append("未知ID: " + str(report["unknown_ids"]))
    return "\n".join(lines) + "\n"
