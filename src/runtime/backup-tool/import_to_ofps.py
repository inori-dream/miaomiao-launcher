#!/usr/bin/env python3
"""
从账号备份 ZIP 或解包目录为 of-ps 导入一个玩家，沿用原 UID 并创建登录账号。
"""

from __future__ import annotations

import argparse
import getpass
import json
import pickle
import secrets
import sqlite3
import sys
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from pathlib import Path
from zipfile import BadZipFile, Path as ZipPath, ZipFile

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OFPS_DIR = SCRIPT_DIR.parent / "of-ps"
OFPS_DIR: Path | None = None
IMPORT_ERRORS = (OSError, ValueError, sqlite3.Error, BadZipFile, RuntimeError, OverflowError, zlib.error)
MAX_PACKET_BYTES = 64 * 1024 * 1024


def is_ofps_directory(path: Path) -> bool:
    return all(
        (path / name).is_file()
        for name in ("main.py", "config.py", "proto/net_pb2.py", "utils/db.py")
    )


def configure_ofps(directory: Path | None = None) -> None:
    global OFPS_DIR, Config, net_pb2, ofps_db, IMPORT_ERRORS
    if OFPS_DIR is not None and directory is None:
        return
    directory = (directory or DEFAULT_OFPS_DIR).expanduser().resolve()
    if not is_ofps_directory(directory):
        raise ValueError(f"请选择含 main.py 和 config.py 的 of-ps 目录。当前路径 {directory}")
    if OFPS_DIR is not None:
        if OFPS_DIR != directory:
            raise ValueError("请重新启动导入工具来切换 of-ps 目录。")
        return

    sys.path.insert(0, str(directory))
    try:
        from google.protobuf.message import DecodeError
        from config import Config
        from proto import net_pb2
        import utils.db as ofps_db
    except ImportError as exc:
        sys.path.remove(str(directory))
        raise ValueError(f"无法加载 of-ps。请使用已安装服务端依赖的 Python 环境。{exc}") from exc
    IMPORT_ERRORS += (DecodeError,)
    OFPS_DIR = directory


def choose_ofps_directory() -> Path | None:
    import tkinter as tk
    from tkinter import filedialog

    window = tk.Tk()
    window.withdraw()
    try:
        path = filedialog.askdirectory(parent=window, title="选择含 main.py 的 of-ps 目录", mustexist=True)
        return Path(path) if path else None
    finally:
        window.destroy()


def find_bins(raw_dir: Path | ZipPath, suffix_name: str) -> list[Path | ZipPath]:
    suffix = f"_{suffix_name}.bin"
    files = (p for p in raw_dir.iterdir() if p.name.endswith(suffix) and p.is_file())
    return sorted(files, key=packet_order)


def packet_order(path: Path | ZipPath) -> tuple[int, str]:
    sequence = path.name.partition("_")[0]
    return (int(sequence) if sequence.isascii() and sequence.isdecimal() else 0, path.name)


def read_backup_member(path: Path | ZipPath, limit: int = MAX_PACKET_BYTES) -> bytes:
    with path.open("rb") as source:
        data = source.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"备份文件过大，无法读取 {path.name}")
    return data


def read_identity(backup: Path | ZipPath) -> net_pb2.PlayerMainDataRsp:
    raw_dir = backup / "raw"
    if not raw_dir.is_dir():
        raise ValueError("请选择账号备份 ZIP 或含 raw 目录的解包结果。")
    main_data = None
    for packet in find_bins(raw_dir, "PlayerMainDataRsp"):
        data = net_pb2.PlayerMainDataRsp.FromString(read_backup_member(packet))
        if not data.player_id:
            continue
        if main_data is not None and main_data.player_id != data.player_id:
            raise ValueError("备份中有多个玩家 UID，请按账号分别备份。")
        main_data = data

    if main_data is None:
        raise ValueError("备份中缺少带有效 UID 的 PlayerMainDataRsp。请重新备份账号。")
    summary = backup / "summary.json"
    if summary.is_file():
        metadata = json.loads(read_backup_member(summary, limit=1024 * 1024))
        if not isinstance(metadata, dict) or metadata.get("player_uids", [main_data.player_id]) != [main_data.player_id]:
            raise ValueError("summary.json 里的玩家 UID 与原始账号数据不一致。")
    return main_data


@contextmanager
def open_backup(path: Path):
    configure_ofps()
    if path.is_dir():
        yield path / "raw", read_identity(path)
        return
    with ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("ZIP 内有重名文件，无法确认导入内容。")
        root = ZipPath(archive)
        yield root / "raw", read_identity(root)


def database_path() -> Path:
    configure_ofps()
    return (OFPS_DIR / Config.DB_PATH).resolve()


def get_instance_id_of_item(item: net_pb2.ItemDetail) -> int:
    mi = item.main_item
    for field in ("weapon", "armor", "poster", "pet"):
        if mi.HasField(field):
            sub = getattr(mi, field)
            iid = getattr(sub, "instance_id", 0)
            if iid:
                return int(iid)
    return 0


def import_backup(
    parsed_dir: Path,
    username: str,
    password: str,
    *,
    db_path: Path | None = None,
    offline: bool = False,
    expected_uid: int | None = None,
) -> net_pb2.PlayerMainDataRsp:
    username = username.strip()
    if not username or not password.strip():
        raise ValueError("请设置登录名和密码。")
    configure_ofps()
    if Config.IN_MEMORY and not offline:
        raise ValueError("of-ps 使用内存数据库，请先停服再导入。")

    target = db_path if db_path is not None else database_path()
    # 旧版服务端没有独立的 schema 初始化函数，可直接使用它已经建好的数据库。
    init_schema = getattr(ofps_db, "init_schema", None)
    if init_schema is None and not target.is_file():
        raise ValueError("请先启动一次所选的 of-ps，等它创建数据库后正常关闭，再导入。")
    with (
        open_backup(parsed_dir) as (raw_dir, pmd),
        closing(sqlite3.connect(target, timeout=10)) as conn,
        conn,
    ):
        player_id = pmd.player_id
        if expected_uid is not None and expected_uid != player_id:
            raise ValueError("备份文件的 UID 已变化，请重新选择 ZIP。")
        conn.execute("PRAGMA foreign_keys = ON")
        if init_schema is None:
            conn.execute("BEGIN IMMEDIATE")
            tables = conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name IN ('users', 'players')"
            ).fetchone()[0]
            if tables != 2:
                raise ValueError("当前数据库尚未初始化。请先启动一次 of-ps 后再导入。")
        else:
            init_schema(conn, begin_immediate=True)
        if conn.execute("SELECT 1 FROM users WHERE id = ?", (player_id,)).fetchone():
            raise ValueError(f"UID {player_id} 已有登录账号。")
        if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise ValueError("这个登录名已被使用，请换一个。")
        player = conn.execute(
            "SELECT register_time, unlock_functions, team, is_online FROM players WHERE player_id = ?",
            (player_id,),
        ).fetchone()
        # 旧导入脚本为好友创建的资料行没有注册时间和角色进度。
        if player is not None and player != (None, pickle.dumps([]), pickle.dumps((101001, 0, 0)), 0):
            raise ValueError(f"UID {player_id} 已有存档。")

        login_rsp = net_pb2.PlayerLoginRsp()
        login_files = find_bins(raw_dir, "PlayerLoginRsp")
        if login_files:
            login_rsp.ParseFromString(read_backup_member(login_files[-1]))

        # 1. 创建登录账号（绑定到真实官服 player_id）
        token = secrets.token_hex(16)
        conn.execute(
            "INSERT INTO users (id, username, password, user_token) VALUES (?, ?, ?, ?)",
            (player_id, username, password, token),
        )

        # 2. 写入 players 主表
        unlock_blob = pickle.dumps(list(pmd.unlock_functions))
        team_tuple = (
            int(pmd.team.char1) or 101001,
            int(pmd.team.char2),
            int(pmd.team.char3),
        )
        team_blob = pickle.dumps(team_tuple)
        now_ts = int(time.time())
        reg_time = int(login_rsp.register_time) if login_rsp.register_time else int(pmd.create_time or now_ts)
        region_name = login_rsp.region_name or "cn_prod_main"
        tz = int(login_rsp.server_time_zone) if login_rsp.server_time_zone else 28800

        fields = {
            "player_name": pmd.player_name or username,
            "level": pmd.level,
            "exp": pmd.exp,
            "sex": pmd.sex,
            "world_level": pmd.world_level,
            "head": pmd.head or 41101,
            "team_leader_badge": 5000,
            "sign": pmd.sign,
            "guild_name": "",
            "character_id": team_tuple[0],
            "garden_like_num": pmd.garden_likes_num,
            "register_time": reg_time,
            "create_time": pmd.create_time or reg_time,
            "region_name": region_name,
            "client_log_server_token": "",
            "server_time_zone": tz,
            "phone_background": pmd.phone_background or 8000,
            "birthday": pmd.birthday or "1992-02-25",
            "is_hide_birthday": int(pmd.is_hide_birthday),
            "hide_value": pmd.hide_value,
            "account_type": pmd.account_type or 9999,
            "unlock_functions": unlock_blob,
            "team": team_blob,
            "avatar_frame": pmd.appearance.avatar_frame,
            "pendant": pmd.appearance.pendant,
            "pet_instance_id": pmd.pet_instance_id,
            "last_login_time": now_ts * 1000,
        }
        columns = ", ".join(fields)
        placeholders = ", ".join("?" for _ in fields)
        updates = ", ".join(f"{key} = excluded.{key}" for key in fields)
        conn.execute(
            f"INSERT INTO players (player_id, {columns}) VALUES (?, {placeholders}) "
            f"ON CONFLICT(player_id) DO UPDATE SET {updates}",
            (player_id, *fields.values()),
        )

        # 月卡
        conn.execute(
            "INSERT OR REPLACE INTO month_card (player_id, over_due_time, reward_days) VALUES (?, ?, ?)",
            (player_id, int(pmd.month_card_over_due_time), int(pmd.month_card_reward_days)),
        )

        # 3. 写入角色 (29 个角色，含全部穿搭/装备预设)
        for ch in pmd.characters:
            conn.execute(
                "INSERT OR REPLACE INTO characters (player_id, character_id, character_blob) VALUES (?, ?, ?)",
                (player_id, int(ch.character_id), ch.SerializeToString()),
            )

        # 4. 写入章节与任务
        for chap in pmd.quest_detail.chapters:
            conn.execute(
                "INSERT OR REPLACE INTO chapters (player_id, chapter_id, chapter_blob) VALUES (?, ?, ?)",
                (player_id, int(chap.chapter_id), chap.SerializeToString()),
            )
        for q in pmd.quest_detail.quests:
            conn.execute(
                "INSERT OR REPLACE INTO quests (player_id, quest_id, quest_blob) VALUES (?, ?, ?)",
                (player_id, int(q.quest_id), q.SerializeToString()),
            )

        # 5. 写入全部背包物品（含 265 时装 & 132 染色方案、武器、护甲、映像等）
        max_instance_id = 10000
        stack_items_cnt = 0
        inst_items_cnt = 0
        has_item_10 = False
        has_item_11 = False

        for pack_file in find_bins(raw_dir, "PackNotice"):
            pn = net_pb2.PackNotice()
            pn.ParseFromString(read_backup_member(pack_file))
            for it in pn.items:
                blob = it.SerializeToString()
                iid = get_instance_id_of_item(it)
                if iid > 0:
                    conn.execute(
                        "INSERT OR REPLACE INTO items_s (player_id, instance_id, item_detail_blob) VALUES (?, ?, ?)",
                        (player_id, iid, blob),
                    )
                    inst_items_cnt += 1
                    if iid > max_instance_id:
                        max_instance_id = iid
                else:
                    item_id = int(it.main_item.item_id)
                    if item_id == 10:
                        has_item_10 = True
                    elif item_id == 11:
                        has_item_11 = True
                    conn.execute(
                        "INSERT OR REPLACE INTO items (player_id, item_id, item_detail_blob) VALUES (?, ?, ?)",
                        (player_id, item_id, blob),
                    )
                    stack_items_cnt += 1

        # 兜底体力(10)与精力(11)，防止 PlayerMainData.py 读取 None 报错
        for req_id, default_num in ((10, 150), (11, 800)):
            if (req_id == 10 and not has_item_10) or (req_id == 11 and not has_item_11):
                it = net_pb2.ItemDetail()
                it.main_item.item_id = req_id
                it.main_item.item_tag = net_pb2.EBagItemTag.EBagItemTag_Currency
                it.main_item.base_item.item_id = req_id
                it.main_item.base_item.num = default_num
                conn.execute(
                    "INSERT OR REPLACE INTO items (player_id, item_id, item_detail_blob) VALUES (?, ?, ?)",
                    (player_id, req_id, it.SerializeToString()),
                )

        conn.execute(
            "INSERT OR REPLACE INTO instance (player_id, instance_id) VALUES (?, ?)",
            (player_id, max_instance_id + 100),
        )

        # 6. 写入花园信息与 616 件家园家具
        garden_files = find_bins(raw_dir, "GetGardenInfoRsp")
        if garden_files:
            gr = net_pb2.GetGardenInfoRsp()
            gr.ParseFromString(read_backup_member(garden_files[-1]))
            gi = gr.garden_info
            conn.execute(
                """
                INSERT OR REPLACE INTO garden_info
                (player_id, like_num, access_num, furniture_num, furniture_limit_num, is_open)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    player_id,
                    int(gi.likes_num),
                    int(gi.access_num),
                    int(gi.furniture_num),
                    int(gi.furniture_limit_num) or 500000,
                    int(bool(gi.is_open)),
                ),
            )
        else:
            conn.execute("INSERT OR IGNORE INTO garden_info (player_id) VALUES (?)", (player_id,))

        furniture_cnt = 0
        collection_cnt = 0
        for sd_file in find_bins(raw_dir, "SceneDataNotice"):
            sdn = net_pb2.SceneDataNotice()
            sdn.ParseFromString(read_backup_member(sd_file))
            data = sdn.data
            is_garden = data.scene_id == 9999 or data.HasField("scene_garden_data")
            if is_garden and (data.channel_id or data.channel_label) != player_id:
                continue
            # 收藏品
            for col_group in data.collections:
                ctype = int(col_group.type)
                for cid, crd in col_group.item_map.items():
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO collections
                        (player_id, item_id, collection_type, item_blob)
                        VALUES (?, ?, ?, ?)
                        """,
                        (player_id, int(cid), ctype, crd.SerializeToString()),
                    )
                    collection_cnt += 1
            # 花园家具
            gmap = data.scene_garden_data.garden_furniture_info_map
            if len(gmap) > 0:
                for fid, fdetail in gmap.items():
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO furnitures
                        (scene_id, channel_id, player_id, furniture_id, furniture_detail_blob)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (9999, player_id, player_id, int(fid), fdetail.SerializeToString()),
                    )
                    furniture_cnt += 1

        # 7. 写入祝福树
        for bt_file in find_bins(raw_dir, "BlessTreeNotice"):
            bt = net_pb2.BlessTreeNotice()
            bt.ParseFromString(read_backup_member(bt_file))
            for def_id, tree_list in bt.trees.items():
                conn.execute(
                    "INSERT OR REPLACE INTO bless_tree (player_id, def_id, tree_blob) VALUES (?, ?, ?)",
                    (player_id, int(def_id), pickle.dumps(list(tree_list.tree_ids))),
                )

        # 8. 写入邮件
        mail_cnt = 0
        for mf in find_bins(raw_dir, "GetMailsRsp"):
            mr = net_pb2.GetMailsRsp()
            mr.ParseFromString(read_backup_member(mf))
            for m in mr.mails:
                conn.execute(
                    "INSERT OR REPLACE INTO mail (player_id, mail_id, mail_blob) VALUES (?, ?, ?)",
                    (player_id, int(m.mail_id), m.SerializeToString()),
                )
                mail_cnt += 1

        # 9. 写入好友列表（同时为好友插入 players 资料行，确保 Friend.py 查询不报错）
        friend_cnt = 0
        rsp_files = find_bins(raw_dir, "FriendRsp")
        # 选出包含最多好友的 FriendRsp (199位正式好友)
        best_friend_rsp: net_pb2.FriendRsp | None = None
        for rf in rsp_files:
            fr = net_pb2.FriendRsp()
            fr.ParseFromString(read_backup_member(rf))
            if best_friend_rsp is None or len(fr.info) > len(best_friend_rsp.info):
                best_friend_rsp = fr

        if best_friend_rsp is not None:
            empty_unlock = pickle.dumps([])
            empty_team = pickle.dumps((101001, 0, 0))
            for finfo in best_friend_rsp.info:
                b = finfo.info
                fid = int(b.player_id)
                if fid <= 0 or fid == player_id:
                    continue
                if conn.execute(
                    "SELECT 1 FROM users WHERE id = ? "
                    "AND NOT EXISTS (SELECT 1 FROM players WHERE player_id = users.id)",
                    (fid,),
                ).fetchone():
                    continue
                conn.execute(
                    """
                    INSERT OR IGNORE INTO players (
                        player_id, player_name, level, head, last_login_time, sex,
                        phone_background, sign, guild_name, team_leader_badge,
                        character_id, create_time, garden_like_num, account_type,
                        birthday, hide_value, avatar_frame, unlock_functions, team
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        fid,
                        b.nick_name,
                        int(b.level),
                        int(b.head) or 41101,
                        int(b.last_login_time),
                        int(b.sex),
                        int(b.phone_background) or 8000,
                        b.sign,
                        b.guild_name,
                        int(b.team_leader_badge) or 5000,
                        int(b.character_id) or 101001,
                        int(b.create_time),
                        int(b.garden_like_num),
                        int(b.account_type) or 9999,
                        b.birthday or "1992-02-25",
                        int(b.hide_value),
                        int(b.avatar_frame),
                        empty_unlock,
                        empty_team,
                    ),
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO friend
                    (player_id, friend_id, friend_status, alias, friend_tag, friend_intimacy, friend_background)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        player_id,
                        fid,
                        2,  # FriendListType_FRIEND
                        finfo.alias,
                        int(finfo.friend_tag),
                        int(finfo.friend_intimacy),
                        int(finfo.friend_background),
                    ),
                )
                friend_cnt += 1

        # 10. 用服务端的生活类型字典存储等级和熟练度。
        life_data = {
            life_type: net_pb2.LifeBaseInfo(life_type=life_type, level=1).SerializeToString()
            for life_type in net_pb2.LifeType.values()
            if life_type != net_pb2.LifeType.LIFE_TYPE_NONE
        }
        for lf in find_bins(raw_dir, "GetLifeInfoRsp"):
            lr = net_pb2.GetLifeInfoRsp()
            lr.ParseFromString(read_backup_member(lf))
            if not lr.HasField("life_base_info"):
                continue
            life_type = lr.life_base_info.life_type or lr.life_skill.life_type
            if life_type > 0:
                lr.life_base_info.life_type = life_type
                life_data[life_type] = lr.life_base_info.SerializeToString()
        conn.execute(
            "INSERT OR REPLACE INTO life_info (player_id, life_blob) VALUES (?, ?)",
            (player_id, pickle.dumps(life_data)),
        )

        # 11. 写入抽卡记录
        gacha_cnt = 0
        seen_gacha = set()
        for gf in find_bins(raw_dir, "GachaRecordRsp"):
            gr = net_pb2.GachaRecordRsp()
            gr.ParseFromString(read_backup_member(gf))
            for rec in gr.records:
                key = (int(rec.gacha_id), int(rec.item_id), int(rec.gacha_time))
                if key in seen_gacha:
                    continue
                seen_gacha.add(key)
                conn.execute(
                    "INSERT INTO gacha_record (player_id, gacha_id, item_id, gacha_time) VALUES (?, ?, ?, ?)",
                    (player_id, key[0], key[1], key[2]),
                )
                gacha_cnt += 1

    print(f"已导入 {fields['player_name']}（UID {player_id}），登录名 {username}")
    print(f"数据库 {target}")
    print(
        f"角色 {len(pmd.characters)} · 任务 {len(pmd.quest_detail.quests)} · "
        f"背包 {stack_items_cnt + inst_items_cnt} · 家园家具 {furniture_cnt} · "
        f"收藏品 {collection_cnt} · 好友 {friend_cnt} · 邮件 {mail_cnt} · 抽卡记录 {gacha_cnt}"
    )
    return pmd


def launch_window(initial_path: Path | None = None) -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    window = tk.Tk()
    window.title("of-ps 玩家存档导入")
    window.minsize(560, 320)
    panel = ttk.Frame(window, padding=20)
    panel.pack(fill="both", expand=True)
    panel.columnconfigure(1, weight=1)

    backup_path: Path | None = None
    preview_uid: int | None = None
    path_text = tk.StringVar(value="请选择玩家账号 ZIP")
    identity_text = tk.StringVar()
    username = tk.StringVar()
    password = tk.StringVar()
    confirmation = tk.StringVar()
    status = tk.StringVar()
    offline = tk.BooleanVar()
    executor = ThreadPoolExecutor(max_workers=1)

    def select_backup(path: Path) -> None:
        nonlocal backup_path, preview_uid
        backup_path = None
        preview_uid = None
        path_text.set(str(path))
        identity_text.set("")
        status.set("")
        import_button.state(["disabled"])
        try:
            with open_backup(path) as (_, identity):
                preview_uid = identity.player_id
                identity_text.set(f"{identity.player_name or '未知玩家'}（UID {preview_uid}）")
        except IMPORT_ERRORS as exc:
            messagebox.showerror("无法读取账号备份", str(exc), parent=window)
            return
        backup_path = path
        import_button.state(["!disabled"])
        username_entry.focus_set()

    def browse() -> None:
        selected = filedialog.askopenfilename(
            parent=window, title="选择玩家账号备份", filetypes=[("账号备份 ZIP", "*.zip")]
        )
        if selected:
            select_backup(Path(selected))

    def import_selected() -> None:
        if backup_path is None:
            return
        if password.get() != confirmation.get():
            messagebox.showerror("请检查密码", "两次输入的密码不一致。", parent=window)
            return
        login_name = username.get().strip()
        future = executor.submit(
            import_backup, backup_path, login_name, password.get(),
            offline=offline.get(), expected_uid=preview_uid,
        )
        status.set("正在导入玩家存档…")
        import_button.state(["disabled"])
        browse_button.state(["disabled"])

        def finish_import() -> None:
            if not future.done():
                window.after(100, finish_import)
                return
            browse_button.state(["!disabled"])
            try:
                player = future.result()
            except IMPORT_ERRORS as exc:
                status.set("")
                import_button.state(["!disabled"])
                messagebox.showerror("导入失败", str(exc), parent=window)
                return
            status.set(f"已导入 {player.player_name or login_name}（UID {player.player_id}），登录名 {login_name}")
            for field in (username, password, confirmation):
                field.set("")

        window.after(100, finish_import)

    ttk.Label(panel, text="账号备份").grid(row=0, column=0, sticky="nw", pady=6)
    ttk.Label(panel, textvariable=path_text, wraplength=350).grid(row=0, column=1, sticky="w", padx=12)
    browse_button = ttk.Button(panel, text="选择 ZIP", command=browse)
    browse_button.grid(row=0, column=2, sticky="ne")
    ttk.Label(panel, text="备份玩家").grid(row=1, column=0, sticky="w", pady=6)
    ttk.Label(panel, textvariable=identity_text).grid(row=1, column=1, columnspan=2, sticky="w", padx=12)
    ttk.Label(panel, text="目标数据库").grid(row=2, column=0, sticky="nw", pady=6)
    ttk.Label(panel, text=str(database_path()), wraplength=420).grid(
        row=2, column=1, columnspan=2, sticky="w", padx=12
    )

    for row, label, field in (
        (3, "登录名", username),
        (4, "密码", password),
        (5, "确认密码", confirmation),
    ):
        ttk.Label(panel, text=label).grid(row=row, column=0, sticky="w", pady=6)
        entry = ttk.Entry(panel, textvariable=field, show="" if field is username else "*")
        entry.grid(row=row, column=1, columnspan=2, sticky="ew", padx=(12, 0))
        if field is username:
            username_entry = entry
    if Config.IN_MEMORY:
        ttk.Checkbutton(panel, text="已停止使用内存数据库的 of-ps 服务", variable=offline).grid(
            row=6, column=1, columnspan=2, sticky="w", padx=12
        )
    ttk.Label(panel, textvariable=status, wraplength=420).grid(row=7, column=0, columnspan=2, sticky="w", pady=12)
    import_button = ttk.Button(panel, text="导入并创建账号", command=import_selected, state="disabled")
    import_button.grid(row=7, column=2, sticky="e", pady=12)

    if initial_path is not None:
        window.after_idle(lambda: select_backup(initial_path))
    try:
        window.mainloop()
    finally:
        executor.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser(description="为 of-ps 导入账号备份并创建登录账号")
    parser.add_argument(
        "backup", type=Path, nargs="?", help="玩家账号 ZIP 或含 raw 的解包目录"
    )
    parser.add_argument("--gui", action="store_true", help="打开导入窗口")
    parser.add_argument("--ofps-dir", type=Path, help="含 main.py 和 config.py 的 of-ps 目录")
    parser.add_argument("--username", help="登录名，省略时询问")
    parser.add_argument("--password", help=argparse.SUPPRESS)
    parser.add_argument("--offline", action="store_true", help="确认已停止使用内存数据库的 of-ps 服务")
    args = parser.parse_args()
    try:
        show_window = args.gui or args.backup is None
        directory = args.ofps_dir
        if directory is None and not is_ofps_directory(DEFAULT_OFPS_DIR):
            if not show_window:
                raise ValueError("请用 --ofps-dir 指定 of-ps 目录。")
            directory = choose_ofps_directory()
            if directory is None:
                return 0
        configure_ofps(directory)
        if show_window:
            launch_window(args.backup)
            return 0
        source = args.backup.resolve()
        with open_backup(source) as (_, identity):
            print(f"准备导入 {identity.player_name or '未知玩家'}（UID {identity.player_id}）")
        username = args.username if args.username is not None else input("登录名：")
        password = args.password
        if password is None:
            password = getpass.getpass("密码：")
            if password != getpass.getpass("确认密码："):
                raise ValueError("两次输入的密码不一致。")
        import_backup(source, username, password, offline=args.offline, expected_uid=identity.player_id)
        return 0
    except IMPORT_ERRORS as exc:
        print(f"导入失败：{exc}", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
