#!/usr/bin/env python3
"""
OverField (开放空间) 好友家园批量抓取解析与归档工具。

用法：
  1. 从抓包中提取拜访过的家园并按屋主 UID 分别保存：
     python extract_gardens.py <pcapng路径>
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR / "proto_gen"))

import net_pb2  # type: ignore  # noqa: E402
from pcap_compat import prepare_capture  # noqa: E402
from parse_pcap import (  # type: ignore  # noqa: E402
    SCENE_DATA_NOTICE_ID,
    collect_garden_scene,
    collect_player_profile,
    decode_proto_body,
    extract_tcp_stream_directions,
    load_cmd_map,
    list_tcp_streams,
    parse_game_stream,
    write_garden_backups,
)


def extract_gardens(pcap_path: Path, out_dir: Path) -> None:
    pcap_path = prepare_capture(pcap_path)
    known_names: dict[int, str] = {}
    layouts: dict[int, net_pb2.SceneGardenData] = {}
    cmd_map = load_cmd_map()
    streams = list_tcp_streams(pcap_path)
    print(f"[信息] 发现 {len(streams)} 条 TCP 流，正在扫描家园场景包 (SceneDataNotice / 1016)...")

    for sid in streams:
        _, _, b01, b10 = extract_tcp_stream_directions(pcap_path, sid)
        for payload in (b01, b10):
            if not payload:
                continue
            pkts, _ = parse_game_stream(payload)
            for pkt in pkts:
                if pkt["decompress_err"]:
                    continue
                name = cmd_map.get(pkt["msg_id"])
                if name == "OtherPlayerInfoRsp":
                    decoded, error = decode_proto_body(name, pkt["body"])
                    if not error:
                        collect_player_profile(name, decoded, known_names)
                    continue
                if pkt["msg_id"] != SCENE_DATA_NOTICE_ID:
                    continue
                try:
                    scene = net_pb2.SceneDataNotice.FromString(pkt["body"]).data
                except Exception:
                    continue
                collect_garden_scene(scene, layouts, known_names)

    write_garden_backups(out_dir, layouts, known_names)
    if layouts:
        print(f"好友家园提取完成，共保存 {len(layouts)} 个家园配置。")
    else:
        print("[提示] 本次抓包未发现可归档的家园布局。请进入好友家园并等待家具加载。")


def main() -> None:
    parser = argparse.ArgumentParser(description="OverField 好友家园抓取与归档工具")
    parser.add_argument("pcap", type=Path, help="抓包生成的 .pcapng 文件路径")
    parser.add_argument("-o", "--out", type=Path, default=None, help="输出目录（默认在抓包同目录创建 gardens_<文件名>）")
    args = parser.parse_args()

    pcap_path = args.pcap.resolve()
    if not pcap_path.exists():
        print(f"[错误] 找不到文件: {pcap_path}", file=sys.stderr)
        sys.exit(1)

    out_dir = (
        args.out.resolve()
        if args.out
        else pcap_path.parent / f"gardens_{pcap_path.stem}"
    )
    if args.out is None and out_dir.exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        out_dir = out_dir.with_name(f"{out_dir.name}_{stamp}")
    if out_dir.exists() and (not out_dir.is_dir() or any(out_dir.iterdir())):
        parser.error("输出路径已存在且非空，请指定新的 --out 目录。")
    extract_gardens(pcap_path, out_dir)


if __name__ == "__main__":
    main()
