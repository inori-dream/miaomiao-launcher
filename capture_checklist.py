"""Evidence-based capture checklist and non-disruptive live ETL snapshot observer.
Never stops PktMon. Snapshot failures are unknown, not proof of missing data.
Only successfully decoded server responses contribute to collected categories.
"""
from __future__ import annotations
import argparse
from collections import Counter
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

BASE = Path(__file__).resolve().parent
GROUPS = [
    ('login','登录基础信息',['PlayerLoginRsp'],'重新登录公服'),
    ('main','UID、等级与任务主数据',['PlayerMainDataRsp'],'登录并进入场景'),
    ('characters','角色与外观',['@characters'],'打开角色、穿搭界面'),
    ('bag','背包物品',['@bag'],'打开背包并翻页'),
    ('weapon','武器',['GetWeaponRsp','@weapon'],'打开武器、装备界面'),
    ('armor','护甲',['GetArmorRsp','@armor'],'打开护甲、装备界面'),
    ('poster','装备海报',['GetPosterRsp','@poster'],'打开海报装备界面'),
    ('pet','宠物列表',['GetPetRsp'],'打开宠物、仓库界面'),
    ('garden','家园与家具',['GetGardenInfoRsp','@garden'],'进入自己的家园，等待加载'),
    ('mail','邮件列表',['GetMailsRsp'],'打开邮件界面'),
    ('gacha','卡池信息',['GachaListRsp'],'打开抽卡界面'),
    ('gacha_records','抽卡记录',['GachaRecordRsp'],'打开抽卡历史并翻页'),
    ('achieve','成就',['GetAchieveGroupListRsp','GetAchieveOneGroupRsp'],'打开成就各分组'),
    ('manual','图鉴',['ManualListRsp','GetCharacterAchievementListRsp'],'打开图鉴界面'),
    ('explore','探索与收集',['GetCollectItemIdsRsp','GetCollectMoonInfoRsp','ExploreInitRsp'],'打开探索、收藏界面'),
    ('friend','好友列表',['FriendRsp','FriendListRsp'],'打开好友界面'),
    ('archive','档案与生活技能',['GetArchiveInfoRsp','GetLifeInfoRsp'],'打开档案、生活技能界面'),
]
RELEVANT = {n for _,_,targets,_ in GROUPS for n in targets if not n.startswith('@')} | {'PackNotice','GetAllCharacterEquipRsp','SceneDataNotice'}
STATES = {'collected':'已采集','partial':'未见装备列表','empty':'已收到（列表为空）','waiting':'尚未采集','missing':'未采集到','unknown':'暂时无法判断'}
CAPTURE_INCOMPLETE_WARNING = '检测到抓包缺口或未解析的流尾部，备份可能不完整；仍可保存已解码账号 ZIP。'
CAPTURE_INCOMPLETE_WARNING_EN = 'A TCP capture gap or undecoded stream tail was found. The backup may be incomplete; the decoded account ZIP can still be saved.'
MULTI_ACCOUNT_WARNING = '本次流量包含多个账号，清单仅表示本次见过这些响应；不能作为单账号完整备份，请按账号分别采集。'
MULTI_ACCOUNT_WARNING_EN = 'This traffic contains multiple accounts. The checklist shows only responses seen in this capture and is not a complete single-account backup; capture each account separately.'


def atomic_json(path, data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
    temporary.replace(path)


def equipment_instances(item):
    """Yield valid weapon/armor/poster instances from a bag or equipped item."""
    if not isinstance(item,dict):return
    containers=[item]
    for key in ('main_item','transformed_item'):
        value=item.get(key)
        if isinstance(value,dict):containers.append(value)
    extras=item.get('extras',[]) or []
    if isinstance(extras,list):containers.extend(value for value in extras if isinstance(value,dict))
    for container in containers:
        for kind in ('weapon','armor','poster'):
            value=container.get(kind)
            if not isinstance(value,dict):continue
            try:instance_id=int(value.get('instance_id') or 0)
            except (TypeError,ValueError):continue
            if instance_id>0:yield kind


def observe(name, body, counts, empty):
    if name not in RELEVANT or not isinstance(body,dict):return
    status=body.get('status')
    if status is not None and status not in (1,'StatusCode_OK'):return
    # A decoded protobuf response may omit an empty repeated field. Its successful
    # status is evidence the list was received, not evidence of non-empty contents.
    if name not in ('PackNotice','SceneDataNotice') and status is None and not body:return
    counts[name]+=1
    if name=='PlayerMainDataRsp':
        if int(body.get('player_id') or body.get('player_label') or 0)<=0:
            counts[name]-=1;return
        counts['@characters']+=1
        if not body.get('characters'):empty['@characters']+=1
    if name in ('PackNotice','GetAllCharacterEquipRsp'):
        counts['@bag']+=1
        if not body.get('items') and not body.get('characters'):empty['@bag']+=1
        for detail in body.get('items',[]) or []:
            for kind in equipment_instances(detail):counts['@'+kind]+=1
    if name=='SceneDataNotice':
        data=body.get('data',{})
        if isinstance(data,dict) and int(data.get('scene_id') or 0)==9999:counts['@garden']+=1
    list_fields={'GetWeaponRsp':'weapons','GetArmorRsp':'armors','GetPosterRsp':'posters','GetPetRsp':'pets','GetMailsRsp':'mails','GachaListRsp':'gachas','GachaRecordRsp':'records','FriendRsp':'info','FriendListRsp':'info'}
    key=list_fields.get(name)
    if key and not body.get(key):empty[name]+=1


def report(counts=None, empty=None, *, phase='idle', readable=False, warning='', warning_en='', updated=None, live_packets=None):
    counts=counts or {};empty=empty or {}
    final=phase=='complete'
    items=[]
    for key,label,targets,hint in GROUPS:
        evidence={n:int(counts.get(n,0)) for n in targets if counts.get(n,0)>0}
        count=sum(evidence.values())
        if count:
            if key in ('weapon','armor','poster') and set(evidence)=={'@'+key}:
                # An item instance proves the player owns/wears this equipment,
                # but it is not the dedicated list needed for a complete import.
                state='partial'
            else:
                state='empty' if all(empty.get(n,0)>=v for n,v in evidence.items()) else 'collected'
        else:state='missing' if final and readable else 'waiting' if readable else 'unknown'
        items.append({'id':key,'label':label,'state':state,'status':STATES[state],
                      'count':count,'evidence':evidence,'hint':hint})
    if not warning:
        warning=equipment_warning(counts)
    return {'phase':phase,'readable':readable,'updatedAt':updated or dt.datetime.now().isoformat(timespec='seconds'),
            'warning':warning,'warningEn':warning_en,'items':items,'counts':dict(counts),'emptyCounts':dict(empty),
            'livePackets':live_packets}


def equipment_warning(counts):
    """PackNotice gear is not the authoritative equipment list.

    A character's equipment slots point at instance ids that only GetWeaponRsp /
    GetArmorRsp / GetPosterRsp enumerate. Seeing gear in PackNotice marks the group
    as collected, which reads as complete while most equipped instances are absent,
    so say so explicitly before the player trusts the capture.
    """
    if not counts:return ''
    missing=[]
    for group,list_name in (('weapon','GetWeaponRsp'),('armor','GetArmorRsp'),('poster','GetPosterRsp')):
        if counts.get('@'+group) and not counts.get(list_name):missing.append(list_name)
    if not missing:return ''
    return ('武器／护甲／海报只看到了背包快照，缺少 '+'、'.join(missing)+
            ' 响应：角色装备栏引用的实例大多不在包里，导入后装备会不全。'
            '请打开对应的装备界面翻页后再采集。')


def final_report(export):
    counts=Counter();empty=Counter();export=Path(export)
    warnings=[];warnings_en=[]
    summary=export/'summary.json'
    if summary.is_file():
        try:
            metadata=json.loads(summary.read_text(encoding='utf-8-sig'))
            if isinstance(metadata,dict):
                uids=metadata.get('player_uids',[])
                if isinstance(uids,list) and len(uids)>1:
                    warnings.append(MULTI_ACCOUNT_WARNING);warnings_en.append(MULTI_ACCOUNT_WARNING_EN)
                incomplete=metadata.get('incomplete_streams')
                if isinstance(incomplete,list) and incomplete:
                    warnings.append(CAPTURE_INCOMPLETE_WARNING);warnings_en.append(CAPTURE_INCOMPLETE_WARNING_EN)
        except (OSError,ValueError,TypeError):pass
    directory=export/'json'
    if not directory.is_dir():
        message='未找到解码JSON，无法确认清单完整性。'
        return report(phase='complete',readable=False,warning=' '.join([message,*warnings]),
                      warning_en=' '.join(['No decoded JSON was found, so checklist completeness cannot be confirmed.',*warnings_en]))
    for path in sorted(directory.glob('*.json')):
        match=re.search(r'_s2c_\d+_([A-Za-z0-9_]+)\.json$',path.name)
        if not match or match.group(1) not in RELEVANT:continue
        try:observe(match.group(1),json.loads(path.read_text(encoding='utf-8-sig')),counts,empty)
        except (ValueError,TypeError,OSError):continue
    return report(counts,empty,phase='complete',readable=True,warning=' '.join(warnings),
                  warning_en=' '.join(warnings_en))


def load_decoder(tool):
    tool=Path(tool);sys.path.insert(0,str(tool));sys.path.insert(0,str(tool/'proto_gen'))
    spec=importlib.util.spec_from_file_location('live_capture_decoder',tool/'parse_pcap.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    class BoundedProcess:
        @staticmethod
        def run(*args,**kwargs):
            kwargs.setdefault('timeout',12)
            kwargs.setdefault('creationflags',subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            if kwargs.get('text'):
                kwargs.setdefault('encoding','utf-8');kwargs.setdefault('errors','replace')
            return subprocess.run(*args,**kwargs)
    module.subprocess=BoundedProcess
    return module


def scan_pcap(decoder,path,stop=None):
    counts=Counter();empty=Counter();cmd_map=decoder.load_cmd_map()
    path=decoder.prepare_capture(Path(path))
    streams=decoder.list_tcp_streams(path)
    for sid in streams[:16]:
        if stop is not None and stop.is_file():break
        node0,node1,b01,b10=decoder.extract_tcp_stream_directions(path,sid)
        port0=node0.rsplit(':',1)[-1];port1=node1.rsplit(':',1)[-1]
        if port1 in decoder.GAME_PORTS:data=b10
        elif port0 in decoder.GAME_PORTS:data=b01
        else:
            # The Steam build gets its game port at runtime, so the port cannot
            # identify the server side; take whichever direction parses.
            data=b10 if decoder.looks_like_game_stream(b10) or not decoder.looks_like_game_stream(b01) else b01
        if not data:continue
        packets,_=decoder.parse_game_stream(data)
        for packet in packets:
            name=cmd_map.get(packet['msg_id'],'')
            if name not in RELEVANT or packet.get('decompress_err'):continue
            body,error=decoder.decode_proto_body(name,packet['body'])
            if not error:
                try:observe(name,body,counts,empty)
                except (ValueError,TypeError):continue
    return counts,empty,len(streams)


def read_packet_counters():
    """Total packets seen by the running PktMon session, or None if unavailable."""
    pktmon = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/PktMon.exe'
    try:
        process = subprocess.Popen([str(pktmon), 'counters'], stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        raw, _ = process.communicate(timeout=8)
    except (OSError, subprocess.SubprocessError):
        return None
    if process.returncode:
        return None
    text = raw.decode('utf-8', errors='replace')
    total = 0
    for line in text.splitlines():
        fields = [part.strip() for part in line.split('|')]
        if len(fields) != 2:
            continue
        for side in fields:
            values = re.findall(r'[\d,]{1,15}', side)
            if len(values) >= 2:
                try:
                    total += int(values[0].replace(',', ''))
                except ValueError:
                    pass
    return total or None


def snapshot_pcap(decoder, etl, live):
    """Convert a copy of the live ETL; return (state, pcap). Never touches the session."""
    with tempfile.TemporaryDirectory(prefix='snapshot-', dir=live) as temporary:
        copied = Path(temporary) / 'capture.etl'
        pcap = copied.with_suffix('.pcapng')
        shutil.copyfile(etl, copied)
        pktmon = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/PktMon.exe'
        result = subprocess.run([str(pktmon), 'etl2pcap', str(copied), '--out', str(pcap)],
                                capture_output=True, timeout=20,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if result.returncode or not pcap.is_file():
            return 'unreadable', None
        if pcap.stat().st_size <= 256:
            return 'buffered', None
        if not decoder.list_tcp_streams(pcap):
            return 'buffered', None
        return 'readable', pcap


def watch(capture_root):
    """Read finished segments only. Each segment is flushed when its trace stops."""
    root = Path(capture_root).resolve(); live = root / 'live-progress'; live.mkdir(exist_ok=True)
    stop = root / 'live-progress-stop.flag'; target = live / 'progress.json'
    counts = Counter(); empty = Counter(); readable = False
    scanned = set(); notice = '正在等待第一个落盘分段。'
    atomic_json(target, report(phase='capturing', warning=notice))
    decoder = load_decoder(BASE / 'backup-tool')
    deadline = time.monotonic() + 4 * 3600
    while not stop.is_file() and time.monotonic() < deadline:
        live_packets = read_packet_counters()
        try:
            segments = sorted(root.glob('segment_*.pcapng'))
            fresh = [p for p in segments if p.name not in scanned and p.stat().st_size > 256]
            for path in fresh:
                found, zero, streams = scan_pcap(decoder, path, stop)
                scanned.add(path.name)
                if streams:
                    readable = True
                    for key, value in found.items():
                        counts[key] += value
                        empty[key] += zero[key]
            if readable:
                notice = ''
            elif fresh:
                notice = '分段已落盘，但其中没有识别到游戏协议包；请确认登录后进入了场景。'
            elif live_packets:
                notice = ('PktMon 已记录 ' + format(live_packets, ',') + ' 个数据包；'
                          '当前分段仍在缓冲，约十多秒后写入磁盘即可核对。')
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            notice = '分段读取暂时失败，稍后自动重试；原始数据不受影响。'
        if stop.is_file():
            break
        atomic_json(target, report(counts, empty, phase='capturing', readable=readable,
                                   warning=notice, live_packets=live_packets))
        waited = 0.0
        while waited < 3.0:
            if stop.is_file():
                break
            time.sleep(0.5); waited += 0.5


def progress(state):
    capture_root=Path(state.get('capturePath','')) if state.get('capturePath') else None
    if state.get('phase')=='complete' and state.get('export'):
        export=Path(state['export']);cache=export/'capture-checklist.json'
        if not cache.is_file():atomic_json(cache,final_report(export))
        return json.loads(cache.read_text(encoding='utf-8-sig'))
    if capture_root:
        path=capture_root/'live-progress/progress.json'
        if path.is_file():
            try:return json.loads(path.read_text(encoding='utf-8-sig'))
            except (OSError,ValueError):pass
    return report(phase='capturing' if state.get('active') else state.get('phase','idle'),
                  warning='采集开始后核对实际响应；快照不可读时显示暂无法判断，不会误报已采集。')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['watch'])
    parser.add_argument('--capture-root',type=Path,required=True);args=parser.parse_args();watch(args.capture_root)

if __name__=='__main__':main()
