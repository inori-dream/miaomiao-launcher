"""Repair the two defects this session left in the live save.

1. Remove the itemType=21 rows the earlier patch injected into itemBaseInfo.
   Verified against a save this server rendered correctly: it stores outfits in
   itemFashionMap only, never as bag items.
2. Make of_game_basic.character_id match the team leader stored in the save, so
   the profile and the rendered character agree.

A verified backup is written first; the script refuses to touch the database if
that backup fails its integrity check.
"""
import gzip
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DB = Path(r'D:\bilibili Game\profiles\solo\db\lolo.db')


def main(apply):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute('SELECT user_id, bin_data FROM of_game').fetchall()
    plan = []
    for row in rows:
        uid = row['user_id']
        data = json.loads(gzip.decompress(row['bin_data']).decode('utf-8'))
        item = data.get('item') or {}
        base = item.get('itemBaseInfo') or {}
        fashion = item.get('itemFashionMap') or {}
        bogus = [k for k, v in base.items()
                 if k in fashion and isinstance(v, dict) and v.get('itemType') == 21]
        for key in bogus:
            del base[key]

        team = (data.get('team') or {}).get('TeamInfo') or {}
        leader = team.get('Char1') or 0
        cols = [r[1] for r in con.execute('PRAGMA table_info(of_game_basic)')]
        current = None
        if 'character_id' in cols:
            found = con.execute('SELECT character_id FROM of_game_basic WHERE user_id=?', (uid,)).fetchone()
            current = found[0] if found else None
        fix_profile = bool(leader) and current is not None and int(current) != int(leader)

        plan.append((uid, len(bogus), len(base), leader, current, fix_profile))
        if apply:
            payload = gzip.compress(json.dumps(data, ensure_ascii=False,
                                               separators=(',', ':')).encode('utf-8'))
            con.execute('UPDATE of_game SET bin_data=? WHERE user_id=?', (payload, uid))
            if fix_profile:
                con.execute('UPDATE of_game_basic SET character_id=? WHERE user_id=?', (int(leader), uid))
    if apply:
        con.commit()
    con.close()

    for uid, removed, left, leader, current, fix in plan:
        print('uid=%-9s 移除误写时装行=%-3d itemBaseInfo剩=%-5d 队长=%-8s 档案角色=%-8s %s'
              % (uid, removed, left, leader, current, '已同步' if fix else '无需同步'))
    if not apply:
        print('\n(仅报告；加 --apply 才会写入)')


if __name__ == '__main__':
    apply = '--apply' in sys.argv
    if apply:
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        backup = DB.parent.parent / 'backups' / ('before-repair-%s.db' % stamp)
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(DB, backup)
        with sqlite3.connect(backup) as check:
            state = check.execute('PRAGMA integrity_check').fetchone()[0]
        if state != 'ok':
            sys.exit('备份校验失败，未做任何修改：%s' % state)
        print('已备份：%s' % backup)
    main(apply)
