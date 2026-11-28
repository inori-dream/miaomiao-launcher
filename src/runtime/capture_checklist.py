"""Evidence-based capture checklist and non-disruptive live ETL snapshot observer.
Never stops PktMon. Snapshot failures are unknown, not proof of missing data.
Only successfully decoded server responses contribute to collected categories.
"""
from __future__ import annotations
import argparse
from collections import Counter
import ctypes
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))
_PARENT = BASE.parent
INSTALL_ROOT = _PARENT if (BASE.name.lower() == 'app' and any((
    (_PARENT / 'OverFieldLauncher.exe').is_file(),
    (_PARENT / 'backup-tool').is_dir(),
    (_PARENT / 'server-kit').is_dir(),
    (_PARENT / 'profiles').is_dir(),
    (_PARENT / 'runtime').is_dir(),
))) else BASE
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
    ('fishing','钓鱼与图鉴',['FishingResultNotice','@fishing_meta'],'打开钓鱼界面，钓一条鱼并确认进包'),
    ('gathering','采集与图鉴',['GatherRsp','@gathering_meta'],'打开采集界面，采集一处资源并确认进包'),
]
RELEVANT = (
    {n for _,_,targets,_ in GROUPS for n in targets if not n.startswith('@')}
    | {'PackNotice','GetAllCharacterEquipRsp','SceneDataNotice',
       'GetLifeSkillRsp','GetLifeAchieveRsp','GetOneTypeLifePictorialBookCountRsp'}
)
LIFE_TYPES = {'fishing': 2, 'gathering': 6}
STATES = {'collected':'已采集','partial':'未见装备列表','empty':'已收到（列表为空）','waiting':'尚未采集','missing':'未采集到','unknown':'暂时无法判断'}
CAPTURE_INCOMPLETE_WARNING = '检测到抓包缺口或未解析的流尾部，备份可能不完整；仍可保存已解码账号 ZIP。'
CAPTURE_INCOMPLETE_WARNING_EN = 'A TCP capture gap or undecoded stream tail was found. The backup may be incomplete; the decoded account ZIP can still be saved.'
MULTI_ACCOUNT_WARNING = '本次流量包含多个账号，清单仅表示本次见过这些响应；不能作为单账号完整备份，请按账号分别采集。'
MULTI_ACCOUNT_WARNING_EN = 'This traffic contains multiple accounts. The checklist shows only responses seen in this capture and is not a complete single-account backup; capture each account separately.'


def atomic_json(path, data):
    """Write JSON atomically, surviving transient Windows AV/reader locks."""
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    payload=json.dumps(data,ensure_ascii=False)
    temporary=path.with_name(f'{path.name}.{os.getpid()}.{uuid.uuid4().hex[:6]}.tmp')
    try:
        temporary.write_text(payload,encoding='utf-8')
        for attempt in range(8):
            try:
                temporary.replace(path)
                return
            except OSError as error:
                if getattr(error,'winerror',None) not in (5,32,33) and not isinstance(error,PermissionError):
                    raise
                if attempt < 7:
                    time.sleep(0.02*(attempt+1))
        # Target handle held without FILE_SHARE_DELETE: overwrite in place as last resort.
        with path.open('w',encoding='utf-8') as stream:
            stream.write(payload)
            stream.flush()
    finally:
        temporary.unlink(missing_ok=True)


def _append_observer_log(live_dir, message):
    try:
        stamp=dt.datetime.now().strftime('%H:%M:%S')
        with (Path(live_dir)/'observer.log').open('a',encoding='utf-8') as handle:
            handle.write(f'[{stamp}] {message}\n')
    except OSError:
        pass


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
    if name in ('GetLifeInfoRsp', 'GetLifeSkillRsp', 'GetLifeAchieveRsp', 'GetOneTypeLifePictorialBookCountRsp'):
        life_raw = (body.get('life_type') or (body.get('life_base_info') or {}).get('life_type')
                    or (body.get('life_achieve') or {}).get('life_type'))
        life_map = {'LIFE_TYPE_Fishing': 2, 'LIFE_TYPE_Gathering': 6, 2: 2, 6: 6, '2': 2, '6': 6}
        kind = life_map.get(life_raw)
        if kind == 2:
            counts['@fishing_meta'] += 1
        elif kind == 6:
            counts['@gathering_meta'] += 1


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
        if warning and not warning_en:
            warning_en=equipment_warning_en(counts)
    return {'phase':phase,'readable':readable,'updatedAt':updated or dt.datetime.now().isoformat(timespec='seconds'),
            'warning':warning,'warningEn':warning_en,'items':items,'counts':dict(counts),'emptyCounts':dict(empty),
            'livePackets':live_packets}


def equipment_warning_en(counts):
    if not counts:return ''
    missing=[]
    for group,list_name in (('weapon','GetWeaponRsp'),('armor','GetArmorRsp'),('poster','GetPosterRsp')):
        if counts.get('@'+group) and not counts.get(list_name):missing.append(list_name)
    if not missing:return ''
    return ('Only bag snapshots were seen for weapons, armor, or posters; the '+
            ', '.join(missing)+' response is missing. Most equipped instance IDs will not be in the import. '+
            'Open and page through the corresponding equipment screens before capturing again.')


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
    source=Path(path)
    path=decoder.prepare_capture(source)
    if path==source and hasattr(decoder,'normalize_pcapng'):
        try:path,_=decoder.normalize_pcapng(path)
        except OSError:pass
    streams=decoder.list_tcp_streams(path)
    if hasattr(decoder,'extract_many_tcp_streams'):
        extracted=decoder.extract_many_tcp_streams(path,streams)
    else:
        extracted={}
        for sid in streams:
            if stop is not None and stop.is_file():break
            n0,n1,b01,b10=decoder.extract_tcp_stream_directions(path,sid)
            extracted[sid]=(n0,n1,b01,b10,set())
    seen_physical=set()
    is_tun=getattr(decoder,'_is_tun_virtual_ip',lambda _a:False)
    ordered=sorted(
        extracted.keys(),
        key=lambda sid:(1 if (is_tun(extracted[sid][0]) or is_tun(extracted[sid][1])) else 0,sid),
    )
    for sid in ordered:
        if stop is not None and stop.is_file():break
        node0,node1,b01,b10,_=extracted[sid]
        port0=node0.rsplit(':',1)[-1];port1=node1.rsplit(':',1)[-1]
        if port1 in decoder.GAME_PORTS:data=b10;c2s=b01;remote,client=node1,node0
        elif port0 in decoder.GAME_PORTS:data=b01;c2s=b10;remote,client=node0,node1
        else:
            # The Steam build gets its game port at runtime, so the port cannot
            # identify the server side; take whichever direction parses.
            if decoder.looks_like_game_stream(b10) or not decoder.looks_like_game_stream(b01):
                data,c2s,remote,client=b10,b01,node1,node0
            else:
                data,c2s,remote,client=b01,b10,node0,node1
        sig=(remote,len(c2s),len(data),c2s[:64],data[:64])
        if is_tun(client) and not is_tun(remote):
            if sig in seen_physical:continue
        else:
            seen_physical.add(sig)
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


class _WnodeHeader(ctypes.Structure):
    _fields_ = [
        ('BufferSize', ctypes.c_uint32),
        ('ProviderId', ctypes.c_uint32),
        ('HistoricalContext', ctypes.c_uint64),
        ('TimeStamp', ctypes.c_int64),
        ('Guid', ctypes.c_ubyte * 16),
        ('ClientContext', ctypes.c_uint32),
        ('Flags', ctypes.c_uint32),
    ]


class _EventTraceProperties(ctypes.Structure):
    _fields_ = [
        ('Wnode', _WnodeHeader),
        ('BufferSize', ctypes.c_uint32),
        ('MinimumBuffers', ctypes.c_uint32),
        ('MaximumBuffers', ctypes.c_uint32),
        ('MaximumFileSize', ctypes.c_uint32),
        ('LogFileMode', ctypes.c_uint32),
        ('FlushTimer', ctypes.c_uint32),
        ('EnableFlags', ctypes.c_uint32),
        ('AgeLimit', ctypes.c_int32),
        ('NumberOfBuffers', ctypes.c_uint32),
        ('FreeBuffers', ctypes.c_uint32),
        ('EventsLost', ctypes.c_uint32),
        ('BuffersWritten', ctypes.c_uint32),
        ('LogBuffersLost', ctypes.c_uint32),
        ('RealTimeBuffersLost', ctypes.c_uint32),
        ('LoggerThreadId', ctypes.c_void_p),
        ('LogFileNameOffset', ctypes.c_uint32),
        ('LoggerNameOffset', ctypes.c_uint32),
    ]


def flush_pktmon_session(session_name='PktMon'):
    """Flush buffered PktMon ETW events to its live ETL file without stopping capture."""
    if os.name != 'nt':
        return False
    try:
        prop_size = ctypes.sizeof(_EventTraceProperties)
        buf = (ctypes.c_ubyte * (prop_size + 4096))()
        props = _EventTraceProperties.from_buffer(buf)
        props.Wnode.BufferSize = ctypes.sizeof(buf)
        props.Wnode.Flags = 0x00020000  # WNODE_FLAG_TRACED_GUID
        props.LoggerNameOffset = prop_size
        props.LogFileNameOffset = prop_size + 2048
        control_trace = ctypes.windll.advapi32.ControlTraceW
        control_trace.argtypes = [ctypes.c_uint64, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32]
        control_trace.restype = ctypes.c_uint32
        return control_trace(0, session_name, ctypes.byref(buf), 3) == 0
    except (OSError, AttributeError, ValueError):
        return False


def _active_etl_path(root):
    candidates = sorted(root.glob('segment_*.etl'))
    if candidates:
        return candidates[-1]
    fallback = root / 'capture.etl'
    return fallback if fallback.is_file() else None


def scan_live_etl(decoder, etl, live, stop=None):
    """Convert and scan a live ETL snapshot inside a temp directory; never touches the session."""
    if not etl.is_file() or etl.stat().st_size <= 0:
        return 'buffered', Counter(), Counter()
    with tempfile.TemporaryDirectory(prefix='snapshot-', dir=live) as temporary:
        copied = Path(temporary) / 'capture.etl'
        pcap = copied.with_suffix('.pcapng')
        shutil.copyfile(etl, copied)
        pktmon = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/PktMon.exe'
        result = subprocess.run([str(pktmon), 'etl2pcap', str(copied), '--out', str(pcap)],
                                capture_output=True, timeout=20,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if result.returncode or not pcap.is_file():
            return 'unreadable', Counter(), Counter()
        if pcap.stat().st_size <= 256:
            return 'buffered', Counter(), Counter()
        found, zero, streams = scan_pcap(decoder, pcap, stop)
        if not streams:
            return 'buffered', Counter(), Counter()
        return 'readable', found, zero


def _seq_delta(seq, expected):
    delta = (seq - expected) & 0xFFFFFFFF
    return delta - 0x100000000 if (delta & 0x80000000) else delta


def list_local_ipv4_addresses():
    """Return non-loopback local IPv4 addresses across physical and virtual NICs."""
    addresses = []
    seen = set()
    if os.name == 'nt':
        try:
            iphlpapi = ctypes.windll.iphlpapi
            size = ctypes.c_uint32(0)
            # ERROR_INSUFFICIENT_BUFFER = 122
            if iphlpapi.GetIpAddrTable(None, ctypes.byref(size), 0) == 122 and size.value >= 4:
                buf = (ctypes.c_ubyte * size.value)()
                if iphlpapi.GetIpAddrTable(ctypes.byref(buf), ctypes.byref(size), 0) == 0:
                    count = struct.unpack_from('<I', buf, 0)[0]
                    for idx in range(count):
                        offset = 4 + idx * 24
                        if offset + 24 > len(buf):
                            break
                        raw_ip = bytes(buf[offset:offset + 4])
                        ip = socket.inet_ntoa(raw_ip)
                        if ip != '0.0.0.0' and not ip.startswith('127.') and ip not in seen:
                            seen.add(ip)
                            addresses.append(ip)
        except (OSError, AttributeError, ValueError, struct.error):
            pass
    if not addresses:
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ip = info[4][0]
                if ip != '0.0.0.0' and not ip.startswith('127.') and ip not in seen:
                    seen.add(ip)
                    addresses.append(ip)
        except OSError:
            pass
    return addresses


class _LiveTcpStream:
    """Bounded in-memory TCP stream reassembler and frame decoder for live checklist hints."""

    def __init__(self, flow_key, tracker):
        self.flow_key = flow_key
        self.tracker = tracker
        self.next_seq = None
        self.pending = {}
        self.buf = bytearray()
        self.rejected = False
        self.last_progress = time.monotonic()

    def feed(self, seq, flags, payload):
        if self.rejected:
            return
        syn = bool(flags & 0x02)
        if syn:
            self.next_seq = (seq + 1) & 0xFFFFFFFF
        if not payload:
            return
        now = time.monotonic()
        if self.next_seq is None:
            if len(self.pending) < 64:
                existing = self.pending.get(seq)
                if existing is None or len(payload) > len(existing):
                    self.pending[seq] = payload
            anchor = next(iter(self.pending))
            ordered = sorted(self.pending, key=lambda s: _seq_delta(s, anchor))
            for cand in ordered:
                probe = bytearray()
                cur = cand
                while cur in self.pending and len(probe) < 4096:
                    seg = self.pending[cur]
                    probe.extend(seg)
                    cur = (cur + len(seg)) & 0xFFFFFFFF
                    if self.tracker.starts_with_known_frame(bytes(probe)):
                        self.next_seq = cand
                        self._drain_pending(now)
                        return
            if len(self.pending) >= 32:
                self.next_seq = ordered[0]
                self._drain_pending(now)
            return
        self._insert_segment(seq, payload, now)
        self._drain_pending(now)

    def _insert_segment(self, seq, payload, now):
        delta = _seq_delta(seq, self.next_seq)
        if delta < 0:
            trim = -delta
            if trim >= len(payload):
                return
            seq = self.next_seq
            payload = payload[trim:]
        if seq == self.next_seq:
            self.buf.extend(payload)
            self.next_seq = (self.next_seq + len(payload)) & 0xFFFFFFFF
            self.last_progress = now
            self._parse_buffer()
        else:
            if len(self.pending) < 128:
                existing = self.pending.get(seq)
                if existing is None or len(payload) > len(existing):
                    self.pending[seq] = payload

    def _drain_pending(self, now):
        while self.next_seq in self.pending:
            seg = self.pending.pop(self.next_seq)
            self._insert_segment(self.next_seq, seg, now)
        # Purge stale segments behind next_seq
        stale = [s for s in self.pending if _seq_delta(s, self.next_seq) < 0]
        for s in stale:
            seg = self.pending.pop(s)
            trim = -_seq_delta(s, self.next_seq)
            if trim < len(seg):
                self._insert_segment(self.next_seq, seg[trim:], now)
        # If a packet was dropped by Winsock/firewall and ahead segments pile up, jump cleanly
        if self.pending and (len(self.pending) >= 16 or (now - self.last_progress) > 2.5):
            candidates = sorted(self.pending)
            chosen = None
            for cand in candidates:
                if self.tracker.starts_with_known_frame(self.pending[cand]):
                    chosen = cand
                    break
            if chosen is not None:
                self.buf.clear()
                self.next_seq = chosen
                for s in [k for k in candidates if _seq_delta(k, chosen) < 0]:
                    self.pending.pop(s, None)
                self._drain_pending(now)
            elif len(self.pending) >= 64:
                self.pending.clear()

    def _parse_buffer(self):
        decoder = self.tracker.decoder
        if len(self.buf) > 8 * 1024 * 1024:
            self.buf.clear()
            return
        if not self.buf:
            return
        packets, remaining = decoder.parse_game_stream(bytes(self.buf))
        consumed = len(self.buf) - remaining
        if consumed > 0:
            del self.buf[:consumed]
        for packet in packets:
            self.tracker.record_packet(self.flow_key, packet)
        if not packets and len(self.buf) >= 2:
            head_len = decoder.HEADER_STRUCT.unpack_from(self.buf, 0)[0]
            invalid = head_len == 0 or head_len > 4096
            if not invalid and len(self.buf) >= 2 + head_len:
                if not self.tracker.starts_with_known_frame(bytes(self.buf)):
                    invalid = True
            if invalid:
                # Resynchronize past an incomplete prefix or reject non-game flow
                offset = self.tracker.find_frame_offset(bytes(self.buf))
                if offset is not None:
                    del self.buf[:offset]
                    self._parse_buffer()
                elif not self.tracker.seen_valid_stream(self.flow_key) and len(self.buf) >= 64:
                    self.rejected = True
                    self.buf.clear()
                elif len(self.buf) > 4096:
                    del self.buf[:-256]


class _LiveTcpTracker:
    """Non-intrusive passive IPv4/TCP inspector driving live checklist updates."""

    _SKIP_PORTS = frozenset((53, 80, 443, 8080, 8443))

    def __init__(self, decoder):
        self.decoder = decoder
        self.cmd_map = decoder.load_cmd_map()
        self.game_ports = {int(p) for p in getattr(decoder, 'GAME_PORTS', ())}
        self._lock = threading.Lock()
        self.streams = {}
        self.valid_flows = set()
        self.seen_by_flow = {}
        self.counts = Counter()
        self.empty = Counter()
        self.readable = False

    def snapshot(self):
        with self._lock:
            return Counter(self.counts), Counter(self.empty), bool(self.readable)

    def starts_with_known_frame(self, data):
        if len(data) < 4:
            return False
        head_len = self.decoder.HEADER_STRUCT.unpack_from(data, 0)[0]
        if head_len == 0 or head_len > 4096 or 2 + head_len > len(data):
            return False
        head = self.decoder.net_pb2.PacketHead()
        try:
            head.ParseFromString(data[2:2 + head_len])
        except Exception:
            return False
        return (
            head.msg_id in self.cmd_map
            and head.body_len <= getattr(self.decoder, 'MAX_DECOMPRESSED_BODY', 32 * 1024 * 1024)
        )

    def find_frame_offset(self, data):
        limit = min(len(data) - 4, 2048)
        for offset in range(1, max(1, limit)):
            if self.starts_with_known_frame(data[offset:]):
                return offset
        return None

    def seen_valid_stream(self, flow_key):
        return flow_key in self.valid_flows

    def record_packet(self, flow_key, packet):
        self.valid_flows.add(flow_key)
        if packet.get('decompress_err'):
            return
        name = self.cmd_map.get(packet['msg_id'], '')
        if name not in RELEVANT:
            return
        src_ip, src_port, _, _ = flow_key
        digest = hashlib.blake2b(packet['body'], digest_size=12).digest()
        sig = (src_ip, src_port, packet['msg_id'], packet['seq_id'], packet['packet_id'], digest)
        prev_flow = self.seen_by_flow.get(sig)
        if prev_flow is not None and prev_flow != flow_key:
            return
        if len(self.seen_by_flow) > 8192:
            self.seen_by_flow.clear()
        self.seen_by_flow[sig] = flow_key
        body, error = self.decoder.decode_proto_body(name, packet['body'])
        if error:
            return
        try:
            observe(name, body, self.counts, self.empty)
            if any(self.counts.values()):
                self.readable = True
        except (ValueError, TypeError):
            pass

    def _evict_if_full(self):
        if len(self.streams) < 64:
            return
        for key in [k for k, s in self.streams.items() if s.rejected]:
            self.streams.pop(key, None)
        if len(self.streams) >= 64:
            for key in [k for k, s in self.streams.items() if k not in self.valid_flows]:
                self.streams.pop(key, None)
                if len(self.streams) < 64:
                    break
        if len(self.streams) >= 64:
            oldest = min(self.streams, key=lambda k: self.streams[k].last_progress)
            self.streams.pop(oldest, None)

    def handle_ipv4_packet(self, raw_packet, local_ips):
        if len(raw_packet) < 40 or (raw_packet[0] >> 4) != 4:
            return
        if raw_packet[9] != 6:  # IPPROTO_TCP
            return
        frag_off = struct.unpack_from('!H', raw_packet, 6)[0] & 0x1FFF
        if frag_off != 0:
            return
        tot_len = struct.unpack_from('!H', raw_packet, 2)[0]
        ihl = (raw_packet[0] & 0x0F) * 4
        if ihl < 20 or tot_len < ihl + 20 or tot_len > len(raw_packet):
            return
        src_ip = socket.inet_ntoa(raw_packet[12:16])
        dst_ip = socket.inet_ntoa(raw_packet[16:20])
        if dst_ip not in local_ips or src_ip == dst_ip:
            return
        src_port, dst_port, seq, _ack, off_flags = struct.unpack_from('!HHIIH', raw_packet, ihl)
        if src_port in self._SKIP_PORTS:
            return
        doff = ((off_flags >> 12) & 0x0F) * 4
        if doff < 20 or ihl + doff > tot_len:
            return
        flags = off_flags & 0x3F
        payload = raw_packet[ihl + doff:tot_len]
        flow_key = (src_ip, src_port, dst_ip, dst_port)
        with self._lock:
            stream = self.streams.get(flow_key)
            if stream is None:
                if not payload and not (flags & 0x02):
                    return
                if src_port not in self.game_ports and not (flags & 0x02):
                    if len(payload) < 2:
                        return
                    head_len = self.decoder.HEADER_STRUCT.unpack_from(payload, 0)[0]
                    if head_len == 0 or head_len > 256:
                        return
                    if 2 + head_len <= len(payload) and not self.starts_with_known_frame(payload):
                        return
                self._evict_if_full()
                stream = _LiveTcpStream(flow_key, self)
                self.streams[flow_key] = stream
            stream.feed(seq, flags, payload)
            if flags & 0x05:  # FIN or RST
                self.streams.pop(flow_key, None)

    def handle_link_frame(self, frame, local_ips):
        """Extract IPv4 TCP packet from Ethernet, 802.1Q VLAN, bare IPv4, or 802.11 L2."""
        ipv4 = _extract_ipv4_from_frame(frame)
        if ipv4 is not None:
            self.handle_ipv4_packet(ipv4, local_ips)


def _extract_ipv4_from_frame(frame):
    """Strip Ethernet/VLAN or 802.11 headers if present, else accept bare IPv4."""
    if not frame or len(frame) < 20:
        return None
    if len(frame) >= 34:
        eth = frame[12:14]
        offset = 14
        while eth in (b'\x81\x00', b'\x88\xa8') and len(frame) >= offset + 4:
            eth = frame[offset + 2:offset + 4]
            offset += 4
        if eth == b'\x08\x00' and len(frame) >= offset + 20 and (frame[offset] >> 4) == 4:
            return frame[offset:]
    if (frame[0] >> 4) == 4 and frame[9] == 6:
        ihl = (frame[0] & 0x0F) * 4
        total = struct.unpack_from('!H', frame, 2)[0]
        if ihl >= 20 and ihl + 20 <= total <= len(frame):
            return frame
    return None


class _EtwGuid(ctypes.Structure):
    _fields_ = [
        ('Data1', ctypes.c_uint32),
        ('Data2', ctypes.c_uint16),
        ('Data3', ctypes.c_uint16),
        ('Data4', ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_uuid(cls, value):
        raw = value.bytes_le
        obj = cls()
        ctypes.memmove(ctypes.byref(obj), raw, 16)
        return obj


class _EtwWnodeHeader(ctypes.Structure):
    _fields_ = [
        ('BufferSize', ctypes.c_uint32),
        ('ProviderId', ctypes.c_uint32),
        ('HistoricalContext', ctypes.c_uint64),
        ('TimeStamp', ctypes.c_int64),
        ('Guid', _EtwGuid),
        ('ClientContext', ctypes.c_uint32),
        ('Flags', ctypes.c_uint32),
    ]


class _EtwTraceProperties(ctypes.Structure):
    _fields_ = [
        ('Wnode', _EtwWnodeHeader),
        ('BufferSize', ctypes.c_uint32),
        ('MinimumBuffers', ctypes.c_uint32),
        ('MaximumBuffers', ctypes.c_uint32),
        ('MaximumFileSize', ctypes.c_uint32),
        ('LogFileMode', ctypes.c_uint32),
        ('FlushTimer', ctypes.c_uint32),
        ('EnableFlags', ctypes.c_uint32),
        ('AgeLimit', ctypes.c_int32),
        ('NumberOfBuffers', ctypes.c_uint32),
        ('FreeBuffers', ctypes.c_uint32),
        ('EventsLost', ctypes.c_uint32),
        ('LogBuffersLost', ctypes.c_uint32),
        ('RealTimeBuffersLost', ctypes.c_uint32),
        ('LoggerThreadId', ctypes.c_void_p),
        ('LogFileNameOffset', ctypes.c_uint32),
        ('LoggerNameOffset', ctypes.c_uint32),
    ]


class _EtwEventDescriptor(ctypes.Structure):
    _fields_ = [
        ('Id', ctypes.c_uint16),
        ('Version', ctypes.c_ubyte),
        ('Channel', ctypes.c_ubyte),
        ('Level', ctypes.c_ubyte),
        ('Opcode', ctypes.c_ubyte),
        ('Task', ctypes.c_uint16),
        ('Keyword', ctypes.c_uint64),
    ]


class _EtwEventHeader(ctypes.Structure):
    _fields_ = [
        ('Size', ctypes.c_uint16),
        ('HeaderType', ctypes.c_uint16),
        ('Flags', ctypes.c_uint16),
        ('EventProperty', ctypes.c_uint16),
        ('ThreadId', ctypes.c_uint32),
        ('ProcessId', ctypes.c_uint32),
        ('TimeStamp', ctypes.c_int64),
        ('ProviderId', _EtwGuid),
        ('EventDescriptor', _EtwEventDescriptor),
        ('KernelTime', ctypes.c_uint32),
        ('UserTime', ctypes.c_uint32),
        ('ActivityId', _EtwGuid),
    ]


class _EtwBufferContext(ctypes.Structure):
    _fields_ = [
        ('ProcessorNumber', ctypes.c_ubyte),
        ('Alignment', ctypes.c_ubyte),
        ('LoggerId', ctypes.c_uint16),
    ]


class _EtwEventRecord(ctypes.Structure):
    _fields_ = [
        ('EventHeader', _EtwEventHeader),
        ('BufferContext', _EtwBufferContext),
        ('ExtendedDataCount', ctypes.c_uint16),
        ('UserDataLength', ctypes.c_uint16),
        ('ExtendedData', ctypes.c_void_p),
        ('UserData', ctypes.c_void_p),
        ('UserContext', ctypes.c_void_p),
    ]


class _EtwTimeZone(ctypes.Structure):
    _fields_ = [
        ('Bias', ctypes.c_long),
        ('StandardName', ctypes.c_wchar * 32),
        ('StandardDate', ctypes.c_uint16 * 8),
        ('StandardBias', ctypes.c_long),
        ('DaylightName', ctypes.c_wchar * 32),
        ('DaylightDate', ctypes.c_uint16 * 8),
        ('DaylightBias', ctypes.c_long),
    ]


class _EtwLogfileHeader(ctypes.Structure):
    _fields_ = [
        ('BufferSize', ctypes.c_uint32),
        ('Version', ctypes.c_uint32),
        ('ProviderVersion', ctypes.c_uint32),
        ('NumberOfProcessors', ctypes.c_uint32),
        ('EndTime', ctypes.c_int64),
        ('TimerResolution', ctypes.c_uint32),
        ('MaximumFileSize', ctypes.c_uint32),
        ('LogFileMode', ctypes.c_uint32),
        ('BuffersWritten', ctypes.c_uint32),
        ('LogInstanceGuid', _EtwGuid),
        ('LoggerName', ctypes.c_wchar_p),
        ('LogFileName', ctypes.c_wchar_p),
        ('TimeZone', _EtwTimeZone),
        ('BootTime', ctypes.c_int64),
        ('PerfFreq', ctypes.c_int64),
        ('StartTime', ctypes.c_int64),
        ('ReservedFlags', ctypes.c_uint32),
        ('BuffersLost', ctypes.c_uint32),
    ]


class _EtwTraceHeader(ctypes.Structure):
    _fields_ = [
        ('Size', ctypes.c_uint16),
        ('HeaderType_Flags', ctypes.c_uint16),
        ('Type_Level_Version', ctypes.c_uint32),
        ('ThreadId', ctypes.c_uint32),
        ('ProcessId', ctypes.c_uint32),
        ('TimeStamp', ctypes.c_int64),
        ('Guid', _EtwGuid),
        ('ClientContext_Flags', ctypes.c_uint64),
    ]


class _EtwEventTrace(ctypes.Structure):
    _fields_ = [
        ('Header', _EtwTraceHeader),
        ('InstanceId', ctypes.c_uint32),
        ('ParentInstanceId', ctypes.c_uint32),
        ('ParentGuid', _EtwGuid),
        ('MofData', ctypes.c_void_p),
        ('MofLength', ctypes.c_uint32),
        ('Context', ctypes.c_uint32),
    ]


def _make_etw_callbacks():
    if os.name != 'nt':
        return None, None
    return (
        ctypes.WINFUNCTYPE(None, ctypes.POINTER(_EtwEventRecord)),
        ctypes.WINFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p),
    )


_ETW_EVENT_CB, _ETW_BUFFER_CB = _make_etw_callbacks()


class _EtwLogfileW(ctypes.Structure):
    _fields_ = [
        ('LogFileName', ctypes.c_wchar_p),
        ('LoggerName', ctypes.c_wchar_p),
        ('CurrentTime', ctypes.c_int64),
        ('BuffersRead', ctypes.c_uint32),
        ('ProcessTraceMode', ctypes.c_uint32),
        ('CurrentEvent', _EtwEventTrace),
        ('LogfileHeader', _EtwLogfileHeader),
        ('BufferCallback', _ETW_BUFFER_CB if _ETW_BUFFER_CB is not None else ctypes.c_void_p),
        ('BufferSize', ctypes.c_uint32),
        ('Filled', ctypes.c_uint32),
        ('EventsLost', ctypes.c_uint32),
        ('EventRecordCallback', _ETW_EVENT_CB if _ETW_EVENT_CB is not None else ctypes.c_void_p),
        ('IsKernelTrace', ctypes.c_uint32),
        ('Context', ctypes.c_void_p),
    ]


class NdisEtwCapture:
    """Private real-time NDIS-PacketCapture ETW session; never touches PktMon."""

    SESSION_NAME = 'OverFieldLiveNdis'
    NDIS_PROVIDER = uuid.UUID('2ed6006e-4729-4609-b423-3ee7bcd678ef')
    INVALID_TRACE_HANDLE = 0xFFFFFFFFFFFFFFFF

    def __init__(self, tracker, local_ips=None):
        self.tracker = tracker
        self.local_ips = set(local_ips or ())
        self.active = False
        self.error = ''
        self.events_seen = 0
        self._session_handle = ctypes.c_uint64(0) if os.name == 'nt' else None
        self._trace_handle = self.INVALID_TRACE_HANDLE
        self._thread = None
        self._adv = None
        self._cb_ref = None
        self._buf_cb_ref = None
        self._stop_requested = threading.Event()

    @staticmethod
    def parse_event_1001(raw_bytes):
        """Extract Packet Fragment bytes from NDIS-PacketCapture Event 1001."""
        if not raw_bytes or len(raw_bytes) < 12:
            return None
        _miniport_if, _lower_if, frag_size = struct.unpack_from('<III', raw_bytes, 0)
        if frag_size < 20 or 12 + frag_size > len(raw_bytes):
            return None
        return raw_bytes[12:12 + frag_size]

    def _new_properties(self):
        props_size = ctypes.sizeof(_EtwTraceProperties)
        name_bytes = (len(self.SESSION_NAME) + 2) * 2
        total_size = props_size + name_bytes + 64
        buf = (ctypes.c_ubyte * total_size)()
        props = ctypes.cast(buf, ctypes.POINTER(_EtwTraceProperties)).contents
        props.Wnode.BufferSize = total_size
        props.Wnode.Flags = 0x00020000  # WNODE_FLAG_TRACED_GUID
        props.Wnode.ClientContext = 1   # QPC
        props.BufferSize = 64
        props.MinimumBuffers = 2
        props.MaximumBuffers = 16
        props.LogFileMode = 0x00000100  # EVENT_TRACE_REAL_TIME_MODE
        props.FlushTimer = 1
        props.LoggerNameOffset = props_size
        return buf, props

    def __enter__(self):
        if os.name != 'nt' or _ETW_EVENT_CB is None:
            return self
        try:
            adv = ctypes.WinDLL('advapi32', use_last_error=True)
            adv.StartTraceW.argtypes = [ctypes.POINTER(ctypes.c_uint64), ctypes.c_wchar_p, ctypes.c_void_p]
            adv.StartTraceW.restype = ctypes.c_uint32
            adv.ControlTraceW.argtypes = [ctypes.c_uint64, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint32]
            adv.ControlTraceW.restype = ctypes.c_uint32
            adv.EnableTraceEx2.argtypes = [
                ctypes.c_uint64, ctypes.POINTER(_EtwGuid), ctypes.c_uint32, ctypes.c_ubyte,
                ctypes.c_uint64, ctypes.c_uint64, ctypes.c_uint32, ctypes.c_void_p,
            ]
            adv.EnableTraceEx2.restype = ctypes.c_uint32
            adv.OpenTraceW.argtypes = [ctypes.POINTER(_EtwLogfileW)]
            adv.OpenTraceW.restype = ctypes.c_uint64
            adv.ProcessTrace.argtypes = [ctypes.POINTER(ctypes.c_uint64), ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p]
            adv.ProcessTrace.restype = ctypes.c_uint32
            adv.CloseTrace.argtypes = [ctypes.c_uint64]
            adv.CloseTrace.restype = ctypes.c_uint32
            self._adv = adv

            if not self.local_ips:
                self.local_ips = set(list_local_ipv4_addresses())

            stop_buf, _ = self._new_properties()
            adv.ControlTraceW(0, self.SESSION_NAME, ctypes.byref(stop_buf), 1)  # EVENT_TRACE_CONTROL_STOP

            start_buf, _ = self._new_properties()
            handle = ctypes.c_uint64(0)
            rc = adv.StartTraceW(ctypes.byref(handle), self.SESSION_NAME, ctypes.byref(start_buf))
            if rc != 0:
                self.error = f'StartTraceW rc={rc}'
                return self
            self._session_handle = handle

            guid = _EtwGuid.from_uuid(self.NDIS_PROVIDER)
            rc = adv.EnableTraceEx2(handle, ctypes.byref(guid), 1, 5, 0xFFFFFFFFFFFFFFFF, 0, 0, None)
            if rc != 0:
                self.error = f'EnableTraceEx2 rc={rc}'
                self._stop_session()
                return self

            @_ETW_EVENT_CB
            def on_record(rec_ptr):
                try:
                    rec = rec_ptr.contents
                    if rec.EventHeader.EventDescriptor.Id != 1001:
                        return
                    length = rec.UserDataLength
                    if not rec.UserData or length < 32:
                        return
                    raw = ctypes.string_at(rec.UserData, length)
                    frag = self.parse_event_1001(raw)
                    if frag is not None:
                        self.events_seen += 1
                        self.tracker.handle_link_frame(frag, self.local_ips)
                except Exception:
                    pass

            @_ETW_BUFFER_CB
            def on_buffer(_logfile_ptr):
                return 0 if self._stop_requested.is_set() else 1

            self._cb_ref = on_record
            self._buf_cb_ref = on_buffer
            logfile = _EtwLogfileW()
            logfile.LoggerName = self.SESSION_NAME
            logfile.ProcessTraceMode = 0x10000100  # PROCESS_TRACE_MODE_EVENT_RECORD | PROCESS_TRACE_MODE_REAL_TIME
            logfile.EventRecordCallback = on_record
            logfile.BufferCallback = on_buffer

            trace_handle = adv.OpenTraceW(ctypes.byref(logfile))
            if trace_handle == self.INVALID_TRACE_HANDLE:
                self.error = f'OpenTraceW err={ctypes.get_last_error()}'
                self._stop_session()
                return self
            self._trace_handle = trace_handle
            self.active = True
            self._thread = threading.Thread(target=self._run_trace, name='OverFieldLiveNdis', daemon=True)
            self._thread.start()
        except OSError as exc:
            self.error = str(exc)
            self._stop_session()
        return self

    def _run_trace(self):
        try:
            if self._adv is not None and self._trace_handle != self.INVALID_TRACE_HANDLE:
                arr = (ctypes.c_uint64 * 1)(self._trace_handle)
                self._adv.ProcessTrace(arr, 1, None, None)
        except Exception:
            pass

    def _stop_session(self):
        if self._adv is None:
            return
        try:
            buf, _ = self._new_properties()
            self._adv.ControlTraceW(0, self.SESSION_NAME, ctypes.byref(buf), 1)
        except Exception:
            pass
        self._session_handle = ctypes.c_uint64(0)

    def __exit__(self, *_args):
        self._stop_requested.set()
        if self._adv is not None and self._trace_handle != self.INVALID_TRACE_HANDLE:
            try:
                self._adv.CloseTrace(self._trace_handle)
            except Exception:
                pass
            self._trace_handle = self.INVALID_TRACE_HANDLE
        self._stop_session()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        self.active = False


class LiveRawCapture:
    """Best-effort elevated SIO_RCVALL observer; never touches PktMon or its ETL file."""

    def __init__(self, tracker):
        self.tracker = tracker
        self.selector = selectors.DefaultSelector()
        self.sockets = {}
        self.local_ips = set()
        self.last_refresh = 0.0
        self.errors = []

    def __enter__(self):
        self.refresh_interfaces()
        return self

    def __exit__(self, *_args):
        for ip, sock in list(self.sockets.items()):
            try:
                self.selector.unregister(sock)
            except (KeyError, ValueError):
                pass
            if hasattr(socket, 'SIO_RCVALL') and hasattr(socket, 'RCVALL_OFF'):
                try:
                    sock.ioctl(socket.SIO_RCVALL, socket.RCVALL_OFF)
                except OSError:
                    pass
            try:
                sock.close()
            except OSError:
                pass
        self.sockets.clear()
        self.selector.close()

    def refresh_interfaces(self):
        now = time.monotonic()
        if now - self.last_refresh < 5.0:
            return
        self.last_refresh = now
        if os.name != 'nt' or not hasattr(socket, 'SIO_RCVALL'):
            return
        ips = list_local_ipv4_addresses()
        self.local_ips = set(ips)
        for ip in ips:
            if ip in self.sockets:
                continue
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_IP)
                sock.bind((ip, 0))
                try:
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_HDRINCL, 1)
                except OSError:
                    pass
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
                except OSError:
                    pass
                sock.ioctl(socket.SIO_RCVALL, socket.RCVALL_ON)
                sock.setblocking(False)
                self.selector.register(sock, selectors.EVENT_READ, ip)
                self.sockets[ip] = sock
            except OSError as error:
                self.errors.append(f'{ip}:{error}')
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass

    def poll(self, duration, stop):
        deadline = time.monotonic() + duration
        while not stop.is_file():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self.refresh_interfaces()
            if not self.sockets:
                time.sleep(min(remaining, 0.2))
                continue
            events = self.selector.select(timeout=min(remaining, 0.15))
            for key, _ in events:
                sock = key.fileobj
                for _ in range(128):
                    try:
                        data, _ = sock.recvfrom(65535)
                    except BlockingIOError:
                        break
                    except OSError:
                        break
                    self.tracker.handle_ipv4_packet(data, self.local_ips)


def watch(capture_root):
    """Observe finished segments and live IPv4/TCP traffic without flushing PktMon's ETL."""
    root = Path(capture_root).resolve(); live = root / 'live-progress'; live.mkdir(exist_ok=True)
    stop = root / 'live-progress-stop.flag'; target = live / 'progress.json'
    seg_counts = Counter(); seg_empty = Counter()
    readable = False; scanned = set()
    notice = '正在持续无间断抓包；PktMon 实时缓冲中，结束采集后自动完整解包并核对全部项目。'
    notice_en = 'Continuous capture active; PktMon live buffering is running and full decode will finish after stopping.'
    atomic_json(target, report(phase='capturing', warning=notice, warning_en=notice_en))
    tool = INSTALL_ROOT / 'backup-tool'
    if not tool.is_dir():
        tool = BASE / 'backup-tool'
    decoder = load_decoder(tool)
    tracker = _LiveTcpTracker(decoder)
    deadline = time.monotonic() + 4 * 3600
    local_ips = list_local_ipv4_addresses()
    with NdisEtwCapture(tracker, local_ips=local_ips) as etw_observer, LiveRawCapture(tracker) as observer:
        obs_sockets = getattr(observer, 'sockets', {})
        obs_errors = getattr(observer, 'errors', ())
        _append_observer_log(
            live,
            f'live observer started: ips={local_ips} etw_active={etw_observer.active} '
            f'etw_err={etw_observer.error or "none"} raw_sockets={sorted(obs_sockets.keys())} '
            f'raw_err={"; ".join(obs_errors) or "none"}',
        )
        while not stop.is_file() and time.monotonic() < deadline:
            live_packets = read_packet_counters()
            fresh = []
            try:
                segments = sorted(root.glob('segment_*.pcapng'))
                fresh = [p for p in segments if p.name not in scanned and p.stat().st_size > 256]
                for path in fresh:
                    found, zero, streams = scan_pcap(decoder, path, stop)
                    scanned.add(path.name)
                    if streams:
                        readable = True
                        for key, value in found.items():
                            seg_counts[key] += value
                            seg_empty[key] += zero[key]
                t_counts, t_empty, t_readable = tracker.snapshot()
                if t_readable:
                    readable = True
                if readable:
                    notice = ''; notice_en = ''
                elif fresh:
                    notice = '分段已落盘，但其中没有识别到游戏协议包；请确认登录后进入了场景。'
                    notice_en = 'Segment saved, but no game protocol packets were found yet; make sure you entered the world.'
                elif live_packets:
                    notice = ('PktMon 已实时捕获 ' + format(live_packets, ',') + ' 个数据包（持续无间断抓包中）；'
                              '请依次打开背包、角色、武器、护甲、海报、宠物、邮箱、抽卡记录与家园，点击「结束采集」后将自动完整解包核对。')
                    notice_en = ('PktMon has captured ' + format(live_packets, ',') + ' packets (continuous capture active); '
                                 'open Bag, Characters, Weapons, Armor, Posters, Pets, Mail, Gacha, and Garden, then click Stop Capture.')
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                t_counts, t_empty = Counter(), Counter()
                notice = '实时快照读取暂时失败，稍后自动重试；原始数据不受影响。'
                notice_en = 'Live snapshot read failed temporarily and will retry automatically; raw capture data is unaffected.'
            if stop.is_file():
                break
            counts = Counter(seg_counts)
            for key, value in t_counts.items():
                if value > counts.get(key, 0):
                    counts[key] = value
            empty = Counter(seg_empty)
            for key, value in t_empty.items():
                if value > empty.get(key, 0) and key not in seg_counts:
                    empty[key] = value
            try:
                atomic_json(target, report(counts, empty, phase='capturing', readable=readable,
                                           warning=notice, warning_en=notice_en, live_packets=live_packets))
            except OSError as error:
                _append_observer_log(live, f'progress write skipped: {error}')
            obs_local_ips = getattr(observer, 'local_ips', set())
            if etw_observer.active and obs_local_ips and etw_observer.local_ips != obs_local_ips:
                etw_observer.local_ips = set(obs_local_ips)
            observer.poll(0.6, stop)


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
