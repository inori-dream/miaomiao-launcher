#!/usr/bin/env python3
"""
OverField (开放空间) 官服 TCP 抓包无损解包与玩家数据完整性核对工具。

功能：
1. 调用 tshark 对 pcapng 中的所有 TCP 流进行精确重组（自动处理乱序、重传与分片），
   按双向独立字节流（c2s / s2c）拆包。
2. 解析游戏协议帧：[2字节 BigEndian header_len][PacketHead][Body (flag==1 时 Raw Snappy 解压)]。
3. 合并加载 Lolo/protocol/cmdid.csv 与 of-ps/network/msg_id.py 的消息名映射。
4. 双份落盘：
   - raw/*.bin  : 导出的 Protobuf 二进制。
   - json/*.json: 基于 net_pb2 解码出的可读 JSON。
   - 已知登录请求跳过，登录响应去除日志令牌。
5. 生成 summary.json、按 UID 归档的 ZIP 与终端核对清单。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import struct
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Dict, Iterable, List, Optional, Tuple
from zipfile import ZIP_DEFLATED, ZipFile

import cramjam
from google.protobuf import json_format

SCRIPT_DIR = Path(__file__).resolve().parent
PROTO_DIR = SCRIPT_DIR / "proto_gen"
TSHARK = os.environ.get("OVERFIELD_TSHARK") or str(SCRIPT_DIR / "tools" / "tshark" / "tshark.exe")
sys.path.insert(0, str(PROTO_DIR))
sys.path.insert(0, str(SCRIPT_DIR))

import net_pb2  # type: ignore  # noqa: E402
from pcap_compat import prepare_capture  # noqa: E402
from exploration import Exploration, render_report  # noqa: E402

MAX_DECOMPRESSED_BODY = 32 * 1024 * 1024
MAX_STREAM_DECOMPRESSED = 256 * 1024 * 1024


def snappy_declared_size(data: bytes) -> int:
    """Read Raw Snappy's length varint before allocating output."""
    value = 0
    for index, byte in enumerate(data[:5]):
        value |= (byte & 127) << (7 * index)
        if not byte & 128:
            return value
    raise ValueError("invalid_snappy_length")

HEADER_STRUCT = struct.Struct(">H")
# TShark injects this ASCII notice into follow,tcp,raw output at a sequence gap.
# It is a pseudo-byte line, not game payload.
TCP_GAP_NOTICE = re.compile(rb"\[[0-9]+ bytes missing in capture file\]\x00?")
GAME_PORTS = {"11001", "11002", "11003", "11004", "21001", "21002"}
LOGIN_REQUEST_IDS = {1001, 1003, 2201}
SCENE_DATA_NOTICE_ID = 1016
GARDEN_SCENE_NOTICE_IDS = {1696, 1698, 2614, 2628}

# 关键玩家数据核对项：(显示名, 匹配的消息名关键字列表)
CHECKLIST_GROUPS: List[Tuple[str, List[str]]] = [
    ("登录响应 (基础信息)", ["PlayerLoginRsp"]),
    ("主数据 (UID/等级/角色/任务/外观)", ["PlayerMainDataRsp"]),
    ("背包与物品推送", ["PackNotice", "GetAllCharacterEquipRsp"]),
    ("武器与护甲", ["GetWeaponRsp", "GetArmorRsp"]),
    ("宠物本体列表", ["GetPetRsp"]),
    ("花园信息", ["GetGardenInfoRsp"]),
    ("邮件列表", ["GetMailsRsp"]),
    ("抽卡记录/卡池", ["GachaRecordRsp", "GachaListRsp"]),
    ("成就与图鉴", ["GetAchieveGroupListRsp", "ManualListRsp", "GetCharacterAchievementListRsp"]),
    ("探索与收集 (月亮/收藏/宝箱)", ["GetCollectItemIdsRsp", "GetCollectMoonInfoRsp", "ExploreInitRsp"]),
    ("好友列表", ["FriendRsp", "FriendListRsp"]),
    ("档案/头像/海报/生活技能", ["GetArchiveInfoRsp", "UnlockHeadListRsp", "GetPosterRsp", "GetLifeInfoRsp"]),
]


def load_cmd_map() -> Dict[int, str]:
    """合并加载 Lolo 的 cmdid.csv 和 of-ps 的 msg_id.py。"""
    id_to_name: Dict[int, str] = {}

    ofps_msg_id = PROTO_DIR / "msg_id.txt"
    if ofps_msg_id.exists():
        pattern = re.compile(r"^\s*([A-Za-z0-9_]+)\s*=\s*(\d+)\s*$")
        for line in ofps_msg_id.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = pattern.match(line)
            if m:
                id_to_name[int(m.group(2))] = m.group(1)

    lolo_csv = PROTO_DIR / "cmdid.csv"
    if lolo_csv.exists():
        with lolo_csv.open("r", encoding="utf-8", errors="ignore", newline="") as f:
            for row in csv.reader(f):
                if len(row) >= 2 and row[1].strip().isdigit():
                    id_to_name[int(row[1].strip())] = row[0].strip()

    return id_to_name


def looks_like_game_stream(data: bytes) -> bool:
    """True when the byte stream starts with a plausible game frame header."""
    if len(data) < 4:
        return False
    head_len = HEADER_STRUCT.unpack_from(data, 0)[0]
    if head_len == 0 or head_len > 4096:
        return False
    if len(data) < 2 + head_len:
        return False
    head = net_pb2.PacketHead()
    try:
        head.ParseFromString(data[2:2 + head_len])
    except Exception:
        return False
    return head.msg_id > 0 and 0 < head.body_len <= 32 * 1024 * 1024


def pick_directions(b01: bytes, b10: bytes) -> List[Tuple[str, bytes]]:
    """Labelling is only a hint: the decoded direction decides what is s2c.

    Responses are what the checklist needs, so the direction that parses as a
    game stream first is treated as s2c and the other side as c2s.
    """
    if looks_like_game_stream(b10) or not looks_like_game_stream(b01):
        return [("c2s", b01), ("s2c", b10)]
    return [("s2c", b01), ("c2s", b10)]


def list_tcp_streams(pcap_path: Path) -> List[int]:
    """使用 tshark 列出 pcapng 中所有的 TCP stream ID。"""
    cmd = [TSHARK, "-n", "-r", str(pcap_path), "-T", "fields", "-e", "tcp.stream", "-Y", "tcp"]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        raise RuntimeError(f"tshark 读取 pcap 失败: {res.stderr.strip()}")
    streams = sorted({int(x.strip()) for x in res.stdout.splitlines() if x.strip().isdigit()})
    return streams


def follow_tcp_stream(pcap_path: Path, stream_id: int) -> Tuple[str, str, bytes, bytes, List[int]]:
    """Extract both directions and flag directions with a TShark-reported gap.

    Bytes after a gap cannot be re-aligned safely: a partial frame before it
    may otherwise join a later frame and silently hide data. Keep only the
    exact prefix before the first gap; callers still relay the original file.
    """
    cmd = [TSHARK, "-n", "-r", str(pcap_path), "-q", "-z", f"follow,tcp,raw,{stream_id}"]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        raise RuntimeError(f"tshark 重组流 {stream_id} 失败: {res.stderr.strip()}")

    node0 = ""
    node1 = ""
    chunks: List[List[bytes]] = [[], []]
    gap_directions: List[int] = []

    in_body = False
    for raw_line in res.stdout.splitlines():
        line = raw_line.rstrip("\r\n")
        if line.startswith("Node 0: "):
            node0 = line[len("Node 0: "):].strip()
            continue
        if line.startswith("Node 1: "):
            node1 = line[len("Node 1: "):].strip()
            in_body = True
            continue
        if not in_body:
            continue
        if line.startswith("===="):
            break
        if not line:
            continue
        # tab-prefixed lines are Node1 -> Node0; absent tab means Node0 -> Node1
        direction = int(line.startswith("\t"))
        if direction in gap_directions:
            continue
        hex_str = line.strip()
        if not re.fullmatch(r"[0-9a-fA-F]+", hex_str) or len(hex_str) % 2 != 0:
            continue
        chunk = bytes.fromhex(hex_str)
        notice = TCP_GAP_NOTICE.search(chunk)
        if notice:
            # Normally TShark puts the notice on its own line, but keep any
            # genuine prefix if a localized/combined line ever carries both.
            if notice.start() > 0:
                chunks[direction].append(chunk[:notice.start()])
            gap_directions.append(direction)
            continue
        chunks[direction].append(chunk)

    return node0, node1, b"".join(chunks[0]), b"".join(chunks[1]), gap_directions


def extract_tcp_stream_directions(pcap_path: Path, stream_id: int) -> Tuple[str, str, bytes, bytes]:
    """Compatibility wrapper: legacy callers receive the gap-safe prefix."""
    node0, node1, b01, b10, _ = follow_tcp_stream(pcap_path, stream_id)
    return node0, node1, b01, b10


def parse_game_stream(data: bytes) -> Tuple[List[dict], int]:
    """
    解析单方向的游戏 TCP 字节流。
    返回 (packet_list, remaining_bytes_count)。
    """
    packets: List[dict] = []
    pos = 0
    total = len(data)
    decompressed_bytes = 0

    while pos + 2 <= total:
        head_len = HEADER_STRUCT.unpack_from(data, pos)[0]
        if head_len == 0 or head_len > 4096:
            break
        hend = pos + 2 + head_len
        if hend > total:
            break

        head = net_pb2.PacketHead()
        try:
            head.ParseFromString(data[pos + 2 : hend])
        except Exception:
            break

        if head.msg_id <= 0 or head.body_len > 32 * 1024 * 1024:
            break

        pend = hend + head.body_len
        if pend > total:
            break

        raw_body = data[hend:pend]
        decompress_err: Optional[str] = None
        body = raw_body
        if head.flag == 1:
            try:
                declared = snappy_declared_size(raw_body)
                if declared > MAX_DECOMPRESSED_BODY or decompressed_bytes + declared > MAX_STREAM_DECOMPRESSED:
                    raise ValueError("snappy_decompression_budget_exceeded")
                decompressed_bytes += declared
                body = bytes(cramjam.snappy.decompress_raw(raw_body))
                if len(body) != declared:
                    raise ValueError("snappy_length_mismatch")
            except Exception as e:
                decompress_err = str(e)

        packets.append(
            {
                "msg_id": int(head.msg_id),
                "seq_id": int(head.seq_id),
                "packet_id": int(head.packet_id),
                "flag": int(head.flag),
                "body_len": int(head.body_len),
                "body": body,
                "decompress_err": decompress_err,
            }
        )
        pos = pend

    return packets, total - pos


def decode_proto_body(msg_name: str, body: bytes) -> Tuple[Optional[dict], Optional[str]]:
    """尝试使用 net_pb2 解码消息体为 dict。"""
    msg_cls = getattr(net_pb2, msg_name, None)
    if msg_cls is None:
        return None, "proto_class_not_found"
    try:
        msg = msg_cls()
        msg.ParseFromString(body)
        data_dict = json_format.MessageToDict(
            msg, preserving_proto_field_name=True, always_print_fields_with_no_presence=False
        )
        return data_dict, None
    except Exception as e:
        return None, str(e)


def safe_player_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s\x00-\x1f]+', "_", name).strip("_.")[:40] or "未知玩家"


def account_archive_files(out_dir: Path, base: Iterable[Path]) -> List[Path]:
    """Everything the account ZIP should contain, including garden archives.

    Gardens and own-channel scenes are written into their own subdirectories. They
    must be collected explicitly: leaving them out made the checklist report the
    garden as collected while the ZIP the user opens contained no garden at all.
    """
    files = list(base)
    for folder in ("gardens", "my-scenes"):
        directory = out_dir / folder
        if directory.is_dir():
            files.extend(sorted(path for path in directory.rglob("*") if path.is_file()))
    return files


def write_zip(path: Path, files: Iterable[Path]) -> None:
    with NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with ZipFile(temporary_path, "w", compression=ZIP_DEFLATED) as archive:
            for file in files:
                archive.write(file, file.relative_to(path.parent))
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def is_garden_scene(scene: net_pb2.SceneData) -> bool:
    """Only scene 9999, or a garden that actually carries furniture, is a garden.

    The official server also attaches an EMPTY scene_garden_data to ordinary
    open-world snapshots (scene 1/2/3/5/1001/...). Treating "field present" as
    "garden" moved those map snapshots into gardens/ and lost exploration data.
    """
    if scene.scene_id == 9999:
        return True
    return scene.HasField("scene_garden_data") and scene.scene_garden_data.ByteSize() > 0


def collect_player_profile(name: str, body: dict, names: dict[int, str]) -> None:
    """Resolve absent garden owners from successful profiles, by internal ID."""
    if name != "OtherPlayerInfoRsp" or not isinstance(body, dict):
        return
    status = body.get("status")
    if type(status) not in (int, str) or status not in (1, "StatusCode_OK"):
        return
    info = body.get("other_info")
    if not isinstance(info, dict):
        return
    owner, nickname = info.get("player_id"), info.get("nick_name")
    if (type(owner) is int and 0 < owner <= 0xffffffff
            and isinstance(nickname, str) and nickname.strip() and len(nickname) <= 256):
        names[owner] = nickname


def collect_garden_scene(
    scene: net_pb2.SceneData,
    layouts: dict[int, net_pb2.SceneGardenData],
    names: dict[int, str],
) -> Optional[int]:
    if not is_garden_scene(scene):
        return None
    for player in scene.players:
        if player.player_id and player.player_name:
            names.setdefault(player.player_id, player.player_name)
    owner_uid = scene.channel_id or scene.channel_label
    if owner_uid and scene.HasField("scene_garden_data") and scene.scene_garden_data.ByteSize() > 0:
        layout = scene.scene_garden_data
        previous = layouts.get(owner_uid)
        if previous is None or len(layout.garden_furniture_info_map) >= len(previous.garden_furniture_info_map):
            layouts[owner_uid] = layout
    return owner_uid


def separate_garden_packets(
    out_dir: Path, packet_stems: dict[int, list[str]], account_uid: Optional[int]
) -> dict[int, list[Path]]:
    separated: dict[int, list[Path]] = defaultdict(list)
    for owner_uid, stems in packet_stems.items():
        if owner_uid == account_uid:
            continue
        garden_dir = out_dir / "gardens" / (str(owner_uid) if owner_uid else "unknown")
        for kind, suffix in (("raw", ".bin"), ("json", ".json")):
            destination = garden_dir / kind
            destination.mkdir(parents=True, exist_ok=True)
            for stem in stems:
                source = out_dir / kind / (stem + suffix)
                if source.is_file():
                    separated[owner_uid].append(source.rename(destination / source.name))
    return separated


def write_garden_backups(
    out_dir: Path,
    layouts: dict[int, net_pb2.SceneGardenData],
    names: dict[int, str],
    packet_files: Optional[dict[int, list[Path]]] = None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    packet_files = packet_files or {}
    index = []
    for owner_uid in sorted((layouts.keys() | packet_files.keys()) - {0}):
        owner_name = names.get(owner_uid, "未知玩家")
        safe_name = safe_player_name(owner_name)
        zip_path = out_dir / f"garden_{safe_name}_{owner_uid}.zip"
        meta = {"owner_uid": owner_uid, "owner_name": owner_name, "scene_id": 9999, "zip_file": zip_path.name}
        files = list(packet_files.get(owner_uid, []))
        layout = layouts.get(owner_uid)
        if layout is not None:
            furniture_count = len(layout.garden_furniture_info_map)
            stem = f"garden_{owner_uid}_{safe_name}_{furniture_count}items"
            bin_path = out_dir / f"{stem}.bin"
            json_path = out_dir / f"{stem}.json"
            meta.update(
                furniture_count=furniture_count,
                likes_num=layout.likes_num,
                access_player_num=layout.access_player_num,
                bin_file=bin_path.name,
                json_file=json_path.name,
            )
            bin_path.write_bytes(layout.SerializeToString())
            readable = json_format.MessageToDict(layout, preserving_proto_field_name=True)
            readable["_meta"] = meta
            json_path.write_text(json.dumps(readable, ensure_ascii=False, indent=2), encoding="utf-8")
            files.extend((bin_path, json_path))
        write_zip(zip_path, files)
        index.append(meta)
        print(f"已保存家园 ZIP {zip_path}")
    (out_dir / "gardens_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if packet_files.get(0):
        print(f"[注意] 无法确认屋主 UID 的场景数据留在 {out_dir / 'unknown'}")


def scene_owner(scene: net_pb2.SceneData) -> int:
    """Who a scene snapshot belongs to: its channel id, else channel label."""
    return int(scene.channel_id or scene.channel_label or 0)


def write_own_scene_backup(
    out_dir: Path,
    scenes: dict[int, list[net_pb2.SceneData]],
    account_uid: Optional[int],
    names: dict[int, str],
) -> Optional[dict]:
    """Archive only the player's OWN channel scenes as one browsable bundle.

    Visiting someone else's world is not this account's data, so anything whose
    owner differs from the captured account is skipped outright.
    """
    if account_uid is None or not scenes:
        return None
    mine = list(scenes.get(account_uid, ()))
    if not mine:
        return None
    target = out_dir / "my-scenes"
    target.mkdir(parents=True, exist_ok=True)
    safe_name = safe_player_name(names.get(account_uid, "未知玩家"))
    # A scene may be re-sent after each change; keep the most complete snapshot.
    richest: dict[int, net_pb2.SceneData] = {}
    for scene in mine:
        key = int(scene.scene_id)
        current = richest.get(key)
        if current is None or scene.ByteSize() >= current.ByteSize():
            richest[key] = scene
    files: List[Path] = []
    rows = []
    for key in sorted(richest):
        scene = richest[key]
        stem = f"scene_{key}"
        bin_path = target / f"{stem}.bin"
        json_path = target / f"{stem}.json"
        bin_path.write_bytes(scene.SerializeToString())
        readable = json_format.MessageToDict(scene, preserving_proto_field_name=True)
        summary = {
            "scene_id": int(scene.scene_id),
            "channel_id": int(scene.channel_id or 0),
            "channel_label": int(scene.channel_label or 0),
            "weather": readable.get("weather_type"),
            "tod_time": int(scene.tod_time or 0),
            "players": len(scene.players),
            "garden_furniture": len(scene.scene_garden_data.garden_furniture_info_map)
            if scene.HasField("scene_garden_data") else 0,
            "placed_characters": len(scene.placed_characters),
            "room_decor": len(scene.room_decor_list),
            "vehicles": len(scene.vehicle_info_list),
            "chairs": len(scene.chair_info_list),
            "camp_fires": len(scene.camp_fires),
            "bon_fires": len(scene.bon_fires),
            "treasure_boxes": len(scene.treasure_boxes),
            "collect_items": sum(len(group.item_map) for group in scene.collections),
            "areas": len(scene.areas),
        }
        readable["_summary"] = summary
        json_path.write_text(json.dumps(readable, ensure_ascii=False, indent=2), encoding="utf-8")
        files.extend((bin_path, json_path))
        rows.append(dict(summary, bin_file=bin_path.name, json_file=json_path.name))
    if not rows:
        return None
    index = target / "index.json"
    index.write_text(json.dumps({"owner_uid": account_uid, "owner_name": names.get(account_uid, ""),
                                 "scenes": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    files.append(index)
    zip_path = target / f"my_scenes_{safe_name}_{account_uid}.zip"
    write_zip(zip_path, files)
    print(f"已保存自己的频道场景 {len(rows)} 个 -> {zip_path}")
    return {"owner_uid": account_uid, "scene_count": len(rows),
            "scenes": [row["scene_id"] for row in rows], "zip_file": zip_path.name}


def analyze_pcap(pcap_path: Path, out_dir: Path) -> None:
    pcap_path = prepare_capture(pcap_path)
    cmd_map = load_cmd_map()
    raw_dir = out_dir / "raw"
    json_dir = out_dir / "json"
    raw_dir.mkdir(parents=True, exist_ok=True)
    json_dir.mkdir(parents=True, exist_ok=True)

    streams = list_tcp_streams(pcap_path)
    print(f"[信息] 发现 {len(streams)} 条 TCP 流，开始精确重组与解包...")

    global_seq = 0
    omitted_login_requests = 0
    sanitized_login_responses = 0
    msg_names_seen: Counter = Counter()
    decoded_names_seen: Counter = Counter()
    decoded_s2c_names_seen: Counter = Counter()
    trailing_streams: List[dict] = []
    exploration = Exploration(net_pb2)
    unknown_ids: Counter = Counter()
    decode_errors: List[dict] = []
    observed_player_uids: set[int] = set()
    player_highlights: dict = {}
    stream_summaries: List[dict] = []
    garden_layouts: dict[int, net_pb2.SceneGardenData] = {}
    player_names: dict[int, str] = {}
    profile_names: dict[int, str] = {}
    garden_packets: dict[int, list[str]] = defaultdict(list)
    own_scenes: dict[int, list[net_pb2.SceneData]] = defaultdict(list)

    for sid in streams:
        node0, node1, b01, b10, gap_indexes = follow_tcp_stream(pcap_path, sid)
        if not b01 and not b10 and not gap_indexes:
            continue

        # 判断哪个方向是 s2c（通常服务端端口是 11003 / 21001 等，或非内网 IP）
        p0 = node0.rsplit(":", 1)[-1] if ":" in node0 else ""
        p1 = node1.rsplit(":", 1)[-1] if ":" in node1 else ""
        if p1 in GAME_PORTS:
            directions = [("c2s", b01), ("s2c", b10)]
        elif p0 in GAME_PORTS:
            directions = [("s2c", b01), ("c2s", b10)]
        else:
            # Channels that assign game ports at runtime (the Steam build does)
            # are captured by server address, so the port no longer identifies
            # the server side. The side that actually speaks the game protocol
            # must be found by trial instead of guessed from the port.
            directions = pick_directions(b01, b10)

        stream_pkt_total = 0
        for index, (dir_label, payload) in enumerate(directions):
            tcp_gap = index in gap_indexes
            if tcp_gap:
                # Log the gap even when no frame survives before it.
                trailing_streams.append({"stream_id": sid, "direction": dir_label,
                                         "remaining_bytes": 0, "tcp_gap": True,
                                         "reason": "tcp_sequence_gap"})
            if not payload:
                continue
            pkts, remaining = parse_game_stream(payload)
            if remaining:
                trailing_streams.append({"stream_id": sid, "direction": dir_label,
                                         "remaining_bytes": remaining, "tcp_gap": tcp_gap,
                                         "reason": "incomplete_or_unrecognized_tail"})
            if not pkts:
                continue
            stream_pkt_total += len(pkts)
            current_garden_uid = 0

            for pkt in pkts:
                global_seq += 1
                msg_id = pkt["msg_id"]
                msg_name = cmd_map.get(msg_id, f"Unknown_{msg_id}")
                msg_names_seen[msg_name] += 1
                if msg_id in LOGIN_REQUEST_IDS:
                    omitted_login_requests += 1
                    continue
                if msg_id not in cmd_map:
                    unknown_ids[msg_id] += 1

                safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", msg_name)
                stem = f"{global_seq:05d}_s{sid}_{dir_label}_{msg_id}_{safe_name}"

                body = pkt["body"]
                if pkt["decompress_err"]:
                    decode_errors.append({"seq": global_seq, "msg": msg_name, "err": pkt["decompress_err"]})
                    if msg_id != 1004:
                        (raw_dir / f"{stem}.bin").write_bytes(body)
                    if msg_id == SCENE_DATA_NOTICE_ID:
                        current_garden_uid = 0
                        garden_packets[0].append(stem)
                    elif msg_id in GARDEN_SCENE_NOTICE_IDS:
                        garden_packets[current_garden_uid].append(stem)
                    continue

                if msg_id == 1004:
                    try:
                        login = net_pb2.PlayerLoginRsp.FromString(body)
                    except Exception as exc:
                        decode_errors.append({"seq": global_seq, "msg": msg_name, "err": str(exc)})
                        continue
                    if login.client_log_server_token:
                        login.ClearField("client_log_server_token")
                        body = login.SerializeToString()
                        sanitized_login_responses += 1

                (raw_dir / f"{stem}.bin").write_bytes(body)
                decoded, err = decode_proto_body(msg_name, body)
                if msg_id == SCENE_DATA_NOTICE_ID:
                    garden_uid = 0
                    if decoded is not None:
                        scene = net_pb2.SceneDataNotice.FromString(body).data
                        owner = scene_owner(scene)
                        if owner:
                            # Remember every snapshot; only the account's own
                            # channel is archived later.
                            own_scenes[owner].append(scene)
                        garden_uid = collect_garden_scene(scene, garden_layouts, player_names)
                    current_garden_uid = garden_uid or 0
                    if garden_uid is not None:
                        garden_packets[garden_uid].append(stem)
                elif msg_id in GARDEN_SCENE_NOTICE_IDS:
                    garden_packets[current_garden_uid].append(stem)
                if decoded is not None:
                    decoded_names_seen[msg_name] += 1
                    if dir_label == "s2c":
                        decoded_s2c_names_seen[msg_name] += 1
                        collect_player_profile(msg_name, decoded, profile_names)
                        exploration.add(msg_name, body, {"stream_id": sid, "direction": dir_label,
                                                       "seq": global_seq, "packet_stem": stem})
                    (json_dir / f"{stem}.json").write_text(
                        json.dumps(decoded, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    # 提取核心玩家摘要
                    if msg_name == "PlayerMainDataRsp":
                        player_uid = decoded.get("player_id", 0)
                        if player_uid <= 0:
                            continue
                        observed_player_uids.add(player_uid)
                        if decoded.get("player_name"):
                            player_names[player_uid] = decoded["player_name"]
                        player_highlights["player_id"] = player_uid
                        player_highlights["player_name"] = player_names.get(player_uid)
                        player_highlights["level"] = decoded.get("level")
                        player_highlights["world_level"] = decoded.get("world_level")
                        player_highlights["character_count"] = len(decoded.get("characters", []))
                        qd = decoded.get("quest_detail", {})
                        player_highlights["quest_count"] = len(qd.get("quests", []))
                        player_highlights["chapter_count"] = len(qd.get("chapters", []))
                    elif msg_name == "PackNotice":
                        items = decoded.get("items", []) or decoded.get("item_details", [])
                        player_highlights["pack_notice_count"] = player_highlights.get("pack_notice_count", 0) + 1
                        player_highlights["pack_items_seen"] = player_highlights.get("pack_items_seen", 0) + len(items)
                else:
                    decode_errors.append({"seq": global_seq, "msg_id": msg_id, "msg": msg_name, "err": err})

        if stream_pkt_total > 0:
            stream_summaries.append(
                {"stream_id": sid, "node0": node0, "node1": node1, "packets": stream_pkt_total}
            )

    player_uids = sorted(observed_player_uids)
    account_uid = player_uids[0] if len(player_uids) == 1 else None
    own_scene_summary = write_own_scene_backup(out_dir, own_scenes, account_uid, player_names)
    separated = separate_garden_packets(out_dir, garden_packets, account_uid)
    if separated:
        garden_layouts.pop(account_uid, None)
        write_garden_backups(
            out_dir / "gardens", garden_layouts, {**player_names, **profile_names}, separated
        )

    exploration_summary = exploration.summary(player_uids)
    exploration_path = out_dir / "exploration.json"
    exploration_text_path = out_dir / "exploration.txt"
    exploration_path.write_text(json.dumps(exploration_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    exploration_text_path.write_text(render_report(exploration_summary), encoding="utf-8")

    summary = {
        "exploration": exploration_summary,
        "exploration_report": exploration_text_path.name,
        "exploration_reports": [exploration_path.name, exploration_text_path.name],
        "successful_decode_count": sum(decoded_names_seen.values()),
        "successful_message_counts": dict(decoded_names_seen),
        "successful_s2c_message_counts": dict(decoded_s2c_names_seen),
        "trailing_streams": trailing_streams,
        "trailing_bytes_count": sum(row["remaining_bytes"] for row in trailing_streams),
        "tcp_gap_streams": [row for row in trailing_streams if row.get("tcp_gap")],
        "tcp_gap_count": sum(1 for row in trailing_streams if row.get("tcp_gap")),
        "decompression_budget": {"per_body_bytes": MAX_DECOMPRESSED_BODY,
                                 "per_direction_stream_bytes": MAX_STREAM_DECOMPRESSED},
        "pcap_file": str(pcap_path),
        "total_packets": global_seq,
        "game_streams": stream_summaries,
        "player_uids": player_uids,
        "player_highlights": player_highlights if len(player_uids) <= 1 else {},
        "message_counts": dict(msg_names_seen.most_common()),
        "unknown_msg_ids": dict(unknown_ids),
        "decode_fallback_count": len(decode_errors),
        "omitted_login_requests": omitted_login_requests,
        "sanitized_login_responses": sanitized_login_responses,
        "separate_garden_uids": sorted(separated.keys() - {0}),
        "my_scenes": own_scene_summary,
    }
    summary_path = out_dir / "summary.json"
    if account_uid is not None:
        name = player_names.get(account_uid, "未知玩家")
        player_highlights["player_name"] = name
        account_zip = out_dir / f"account_{safe_player_name(name)}_{account_uid}.zip"
        summary["account_zip"] = account_zip.name
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if account_uid is not None:
        files = account_archive_files(out_dir, [
            summary_path, exploration_path, exploration_text_path,
            *sorted(raw_dir.glob("*.bin")), *sorted(json_dir.glob("*.json"))])
        write_zip(account_zip, files)
        print(f"已保存账号 ZIP {account_zip}")

    print("\n" + "=" * 60)
    print(f"解包完成！共解析出 {global_seq} 个游戏协议包")
    print(f"输出目录: {out_dir}")
    print("=" * 60)

    if len(player_uids) > 1:
        uid_list = "、".join(str(uid) for uid in player_uids)
        print(
            f"\n[注意] 本次备份包含多个玩家 UID（{uid_list}），已保留解包结果。"
            "请按账号分别备份以生成账号 ZIP。"
        )
    elif player_uids:
        print("\n【核心账号信息已捕获】")
        for k, v in player_highlights.items():
            print(f"  - {k}: {v}")
    else:
        print("\n[注意] 尚未从 PlayerMainDataRsp (1006) 取得玩家 UID。请确认抓包期间是否重新登录并进入场景。")

    if global_seq == 0:
        print("[注意] 未识别到游戏协议包。请确认在抓包开始后重新登录并进入官服场景。")

    print("\n【关键玩家数据核对清单】")
    for label, targets in CHECKLIST_GROUPS:
        hit = {t: decoded_s2c_names_seen[t] for t in targets if decoded_s2c_names_seen[t] > 0}
        if hit:
            detail = ", ".join(f"{k} x{v}" for k, v in hit.items())
            print(f"  [OK] {label}: {detail}")
        else:
            print(f"  [--] {label}: 未捕获（可进入对应界面触发）")

    if unknown_ids:
        print(f"\n[提示] 发现 {len(unknown_ids)} 种新版本消息 ID（原始 .bin 已无损保留）: {dict(unknown_ids)}")
    print("")


def main() -> None:
    parser = argparse.ArgumentParser(description="OverField 抓包解包与玩家数据核对工具")
    parser.add_argument("pcap", type=Path, help="输入的 .pcapng 或 .pcap 文件路径")
    parser.add_argument("-o", "--out", type=Path, default=None, help="输出目录（默认在 pcap 同目录创建 parsed_<名>）")
    args = parser.parse_args()

    pcap_path = args.pcap.resolve()
    if not pcap_path.exists():
        print(f"[错误] 找不到文件: {pcap_path}", file=sys.stderr)
        sys.exit(1)

    out_dir = args.out.resolve() if args.out else pcap_path.parent / f"parsed_{pcap_path.stem}"
    if args.out is None and out_dir.exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        out_dir = out_dir.with_name(f"{out_dir.name}_{stamp}")
    if out_dir.exists() and (not out_dir.is_dir() or any(out_dir.iterdir())):
        parser.error("输出路径已存在且非空，请指定新的 --out 目录。")
    analyze_pcap(pcap_path, out_dir)


if __name__ == "__main__":
    main()
