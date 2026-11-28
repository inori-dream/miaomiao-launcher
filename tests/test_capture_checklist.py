"""Synthetic evidence classification tests. Never opens a real capture or DB."""
from build.project_layout import source_file
from collections import Counter
import json
from pathlib import Path
import tempfile
import unittest
import capture_checklist as checklist

class ChecklistTests(unittest.TestCase):
    def setUp(self):self.counts=Counter();self.empty=Counter()
    def event(self,name,body):checklist.observe(name,body,self.counts,self.empty)
    def items(self,**kw):return {i['id']:i for i in checklist.report(self.counts,self.empty,**kw)['items']}
    def test_no_readable_snapshot_is_unknown_not_missing(self):
        self.assertTrue(all(i['state']=='unknown' for i in self.items(phase='capturing').values()))
        self.assertTrue(all(i['state']=='unknown' for i in self.items(phase='complete').values()))
    def test_request_and_error_response_do_not_mark_collected(self):
        self.event('GetWeaponReq',{});self.event('GetWeaponRsp',{'status':2,'weapons':[{}]})
        self.assertEqual(self.items(phase='complete',readable=True)['weapon']['state'],'missing')
    def test_successful_empty_response_is_not_missing(self):
        self.event('GetWeaponRsp',{'status':'StatusCode_OK'})
        self.assertEqual(self.items(phase='capturing',readable=True)['weapon']['state'],'empty')
    def test_main_and_roles_are_confirmed_by_valid_main(self):
        self.event('PlayerMainDataRsp',{'status':1,'player_id':777,'characters':[{'character_id':1}]})
        data=self.items(readable=True)
        self.assertEqual(data['main']['state'],'collected');self.assertEqual(data['characters']['state'],'collected')
    def test_zero_uid_main_is_not_collected(self):
        self.event('PlayerMainDataRsp',{'status':1,'player_id':0})
        self.assertEqual(self.items(phase='complete',readable=True)['main']['state'],'missing')
    def test_inventory_equipment_requires_decoded_instance(self):
        self.event('PackNotice',{'items':[{'main_item':{'weapon':{'instance_id':42,'weapon_id':1},'armor':{'field_2':43}}}]})
        rep=checklist.report(self.counts,self.empty,readable=True)
        data={i['id']:i for i in rep['items']}
        self.assertIn('Only bag snapshots were seen',rep['warningEn'])
        self.assertEqual(data['weapon']['state'],'partial')
        self.assertEqual(data['weapon']['status'],'未见装备列表')
        self.assertEqual(data['armor']['state'],'waiting')
    def test_equipped_item_instance_is_partial_until_dedicated_list_arrives(self):
        self.event('GetAllCharacterEquipRsp',{'status':1,'items':[{'main_item':{
            'armor':{'instance_id':43,'wearer_id':7},'poster':{'instance_id':44,'wearer_id':7}}}]})
        data=self.items(phase='complete',readable=True)
        self.assertEqual(data['armor']['state'],'partial');self.assertEqual(data['poster']['state'],'partial')
        self.event('GetArmorRsp',{'status':1,'armors':[]})
        self.event('GetPosterRsp',{'status':'StatusCode_OK','posters':[{'instance_id':44}]})
        data=self.items(phase='complete',readable=True)
        self.assertEqual(data['armor']['state'],'collected');self.assertEqual(data['poster']['state'],'collected')
    def test_failed_dedicated_list_does_not_upgrade_partial(self):
        self.event('PackNotice',{'items':[{'main_item':{'weapon':{'instance_id':42}}}]})
        self.event('GetWeaponRsp',{'status':2,'weapons':[{'instance_id':42}]})
        self.assertEqual(self.items(phase='complete',readable=True)['weapon']['state'],'partial')
    def test_city_scene_does_not_count_as_home(self):
        self.event('SceneDataNotice',{'data':{'scene_id':1001}})
        self.assertEqual(self.items(readable=True)['garden']['state'],'waiting')
        self.event('SceneDataNotice',{'data':{'scene_id':9999}})
        self.assertEqual(self.items(readable=True)['garden']['state'],'collected')
    def test_final_report_ignores_request_counts_and_failed_json(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'json').mkdir()
            (root/'summary.json').write_text(json.dumps({'message_counts':{'GetWeaponRsp':99}}),encoding='utf-8')
            (root/'json/1_s0_s2c_1_GetPetRsp.json').write_text(json.dumps({'status':1,'pets':[]}),encoding='utf-8')
            (root/'json/2_s0_s2c_2_GetMailsRsp.json').write_text(json.dumps({'status':2}),encoding='utf-8')
            result={i['id']:i for i in checklist.final_report(root)['items']}
            self.assertEqual(result['pet']['state'],'empty')
            self.assertEqual(result['weapon']['state'],'missing');self.assertEqual(result['mail']['state'],'missing')
    def test_final_report_warns_about_gap_without_blocking_decoded_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'json').mkdir()
            (root/'summary.json').write_text(json.dumps({
                'player_uids':[7,8],
                'incomplete_streams':[{'stream_id':0,'direction':'s2c','tcp_gap':True,'trailing_bytes':0}],
            },ensure_ascii=False),encoding='utf-8')
            (root/'json/1_s0_s2c_1_GetWeaponRsp.json').write_text(
                json.dumps({'status':1,'weapons':[{'instance_id':42}]},ensure_ascii=False),encoding='utf-8')
            data=checklist.final_report(root)
            self.assertIn('备份可能不完整',data['warning']);self.assertIn('仍可保存',data['warning'])
            self.assertIn('多个账号',data['warning'])
            self.assertIn('may be incomplete',data['warningEn']);self.assertIn('can still be saved',data['warningEn'])
            weapon=next(item for item in data['items'] if item['id']=='weapon')
            self.assertEqual(weapon['state'],'collected')
    def test_missing_json_folder_keeps_unknown_states_and_gap_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'summary.json').write_text(json.dumps({'incomplete_streams':[
                {'stream_id':0,'direction':'s2c','tcp_gap':True,'trailing_bytes':0}]}),encoding='utf-8')
            result=checklist.final_report(root)
            self.assertTrue(all(item['state']=='unknown' for item in result['items']))
            self.assertIn('备份可能不完整',result['warning'])
    def test_missing_json_folder_is_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            result=checklist.final_report(d)
            self.assertTrue(all(i['state']=='unknown' for i in result['items']))

    def test_scan_live_etl_reports_collected_without_keeping_temp_copy(self):
        from collections import Counter
        import types
        from unittest import mock

        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            etl=root/'segment_001.etl'
            etl.write_bytes(b'etl-header'*32)
            live=root/'live-progress';live.mkdir()
            seen_exists=[]

            def fake_run(cmd,**kwargs):
                Path(cmd[-1]).write_bytes(b'p'*512)
                return types.SimpleNamespace(returncode=0)

            def fake_scan(decoder,path,stop=None):
                seen_exists.append(Path(path).is_file())
                return Counter({'GetWeaponRsp':1}),Counter(),1

            with mock.patch.object(checklist.subprocess,'run',side_effect=fake_run), \
                 mock.patch.object(checklist,'scan_pcap',side_effect=fake_scan):
                state,found,zero=checklist.scan_live_etl(object(),etl,live)
            self.assertEqual(state,'readable')
            self.assertEqual(found['GetWeaponRsp'],1)
            self.assertEqual(zero,Counter())
            self.assertEqual(seen_exists,[True])
            self.assertEqual(list(live.glob('snapshot-*')),[])

    @staticmethod
    def _ipv4_tcp(src,dst,sport,dport,seq,payload,flags=0x18):
        import socket,struct
        ip=struct.pack('!BBHHHBBH4s4s',0x45,0,40+len(payload),1,0,64,6,0,socket.inet_aton(src),socket.inet_aton(dst))
        tcp=struct.pack('!HHIIH',sport,dport,seq,0,(5<<12)|flags)+b'\x00'*6
        return ip+tcp+payload

    def test_watch_never_flushes_active_etl_and_reports_passive_live_packets(self):
        import struct
        import sys
        from unittest import mock

        tool=source_file(Path(__file__).resolve().parent.parent,'backup-tool')
        sys.path.insert(0,str(tool));sys.path.insert(0,str(tool/'proto_gen'))
        import parse_pcap as real_decoder

        inv={v:k for k,v in real_decoder.load_cmd_map().items()}
        login=real_decoder.net_pb2.PlayerLoginRsp(status=1).SerializeToString()
        h_login=real_decoder.net_pb2.PacketHead(msg_id=inv['PlayerLoginRsp'],seq_id=1,packet_id=1,flag=0,body_len=len(login)).SerializeToString()
        f_login=struct.pack('>H',len(h_login))+h_login+login
        main=real_decoder.net_pb2.PlayerMainDataRsp(status=1,player_id=12345,player_label=12345).SerializeToString()
        h_main=real_decoder.net_pb2.PacketHead(msg_id=inv['PlayerMainDataRsp'],seq_id=2,packet_id=2,flag=0,body_len=len(main)).SerializeToString()
        f_main=struct.pack('>H',len(h_main))+h_main+main
        wep=real_decoder.net_pb2.GetWeaponRsp(status=1,weapons=[real_decoder.net_pb2.WeaponInstance(instance_id=55)]).SerializeToString()
        h_wep=real_decoder.net_pb2.PacketHead(msg_id=inv['GetWeaponRsp'],seq_id=3,packet_id=3,flag=0,body_len=len(wep)).SerializeToString()
        f_wep=struct.pack('>H',len(h_wep))+h_wep+wep
        stream_bytes=f_login+f_main+f_wep
        split=len(f_login)+len(f_main)//2

        class FakeObserver:
            calls=0
            def __init__(self,tracker):self.tracker=tracker
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def poll(self,duration,stop):
                FakeObserver.calls+=1
                local={'10.100.163.103','198.18.0.1'}
                if FakeObserver.calls==1:
                    self.tracker.handle_ipv4_packet(self._pkt('14.103.255.172','10.100.163.103',11003,51222,100,b'',flags=0x02),local)
                    self.tracker.handle_ipv4_packet(self._pkt('14.103.255.172','10.100.163.103',11003,51222,101+split,stream_bytes[split:]),local)
                    self.tracker.handle_ipv4_packet(self._pkt('14.103.255.172','10.100.163.103',11003,51222,101,stream_bytes[:split]),local)
                elif FakeObserver.calls==2:
                    # Identical TUN mirror on 198.18.0.1 must deduplicate
                    self.tracker.handle_ipv4_packet(self._pkt('14.103.255.172','198.18.0.1',11003,51219,200,b'',flags=0x02),local)
                    self.tracker.handle_ipv4_packet(self._pkt('14.103.255.172','198.18.0.1',11003,51219,201,stream_bytes),local)
                    stop.write_text('1',encoding='utf-8')

            @staticmethod
            def _pkt(src,dst,sp,dp,seq,payload,flags=0x18):
                return ChecklistTests._ipv4_tcp(src,dst,sp,dp,seq,payload,flags)

        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            etl=root/'segment_001.etl'
            etl.write_bytes(b'etl-placeholder'*32)
            with mock.patch.object(checklist,'load_decoder',return_value=real_decoder), \
                 mock.patch.object(checklist,'flush_pktmon_session') as flush_mock, \
                 mock.patch.object(checklist,'scan_live_etl') as scan_etl_mock, \
                 mock.patch.object(checklist,'read_packet_counters',side_effect=[120,340]), \
                 mock.patch.object(checklist,'LiveRawCapture',FakeObserver):
                checklist.watch(root)

            flush_mock.assert_not_called()
            scan_etl_mock.assert_not_called()
            progress=json.loads((root/'live-progress/progress.json').read_text(encoding='utf-8'))
            by_id={item['id']:item for item in progress['items']}
            self.assertTrue(progress['readable'])
            self.assertEqual(by_id['login']['state'],'collected')
            self.assertEqual(by_id['main']['state'],'collected')
            self.assertEqual(by_id['weapon']['state'],'collected')
            self.assertEqual(by_id['weapon']['evidence'],{'GetWeaponRsp':1})

    def test_watch_falls_back_cleanly_when_raw_sockets_receive_nothing(self):
        from unittest import mock

        class EmptyObserver:
            def __init__(self,tracker):pass
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def poll(self,duration,stop):stop.write_text('1',encoding='utf-8')

        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'segment_001.etl').write_bytes(b'etl'*16)
            with mock.patch.object(checklist,'load_decoder',return_value=mock.MagicMock(GAME_PORTS={'11003'},load_cmd_map=lambda:{})), \
                 mock.patch.object(checklist,'flush_pktmon_session') as flush_mock, \
                 mock.patch.object(checklist,'read_packet_counters',return_value=1500), \
                 mock.patch.object(checklist,'LiveRawCapture',EmptyObserver):
                checklist.watch(root)

            flush_mock.assert_not_called()
            progress=json.loads((root/'live-progress/progress.json').read_text(encoding='utf-8'))
            self.assertFalse(progress['readable'])
            self.assertEqual(progress['livePackets'],1500)
            self.assertIn('1,500',progress['warning'])

    def test_live_tcp_tracker_snappy_dynamic_port_and_same_socket_repeat(self):
        import cramjam
        import struct
        import sys
        tool=source_file(Path(__file__).resolve().parent.parent,'backup-tool')
        sys.path.insert(0,str(tool));sys.path.insert(0,str(tool/'proto_gen'))
        import parse_pcap as real_decoder

        inv={v:k for k,v in real_decoder.load_cmd_map().items()}
        raw_main=real_decoder.net_pb2.PlayerMainDataRsp(status=1,player_id=12345,player_label=12345).SerializeToString()
        comp_main=bytes(cramjam.snappy.compress_raw(raw_main))
        h_main=real_decoder.net_pb2.PacketHead(msg_id=inv['PlayerMainDataRsp'],seq_id=1,packet_id=1,flag=1,body_len=len(comp_main)).SerializeToString()
        f_main=struct.pack('>H',len(h_main))+h_main+comp_main

        raw_mail=real_decoder.net_pb2.GetMailsRsp(status=1,mails=[]).SerializeToString()
        h_mail=real_decoder.net_pb2.PacketHead(msg_id=inv['GetMailsRsp'],seq_id=2,packet_id=2,flag=0,body_len=len(raw_mail)).SerializeToString()
        f_mail=struct.pack('>H',len(h_mail))+h_mail+raw_mail

        tracker=checklist._LiveTcpTracker(real_decoder)
        local={'10.100.163.103'}
        # Dynamic Steam port (not in GAME_PORTS), mid-stream without SYN, segmented in 2 pieces
        tracker.handle_ipv4_packet(self._ipv4_tcp('150.5.131.114','10.100.163.103',29001,55000,5000,f_main[:len(f_main)//2]),local)
        tracker.handle_ipv4_packet(self._ipv4_tcp('150.5.131.114','10.100.163.103',29001,55000,5000+len(f_main)//2,f_main[len(f_main)//2:]),local)
        # Same socket sends two genuine responses; HTTPS 443 ignored
        mail_seq=5000+len(f_main)
        tracker.handle_ipv4_packet(self._ipv4_tcp('150.5.131.114','10.100.163.103',29001,55000,mail_seq,f_mail),local)
        tracker.handle_ipv4_packet(self._ipv4_tcp('150.5.131.114','10.100.163.103',29001,55000,mail_seq+len(f_mail),f_mail),local)
        tracker.handle_ipv4_packet(self._ipv4_tcp('1.2.3.4','10.100.163.103',443,55001,1,f_main),local)

        self.assertTrue(tracker.readable)
        self.assertEqual(tracker.counts['PlayerMainDataRsp'],1)
        self.assertEqual(tracker.counts['GetMailsRsp'],2)

    def test_scan_pcap_normalizes_batches_and_dedups_tun_mirror(self):
        import ipaddress
        import struct
        import sys
        tool=source_file(Path(__file__).resolve().parent.parent,'backup-tool')
        sys.path.insert(0,str(tool));sys.path.insert(0,str(tool/'proto_gen'))
        import net_pb2
        body=net_pb2.PlayerMainDataRsp(status=1,player_id=12345,player_label=12345).SerializeToString()
        head_main=net_pb2.PacketHead(msg_id=1006,seq_id=1,packet_id=1,flag=0,body_len=len(body)).SerializeToString()
        frame_main=struct.pack('>H',len(head_main))+head_main+body
        wbody=net_pb2.GetWeaponRsp(status=1,weapons=[net_pb2.WeaponInstance(instance_id=55)]).SerializeToString()
        head_wep=net_pb2.PacketHead(msg_id=1043,seq_id=2,packet_id=2,flag=0,body_len=len(wbody)).SerializeToString()
        frame_wep=struct.pack('>H',len(head_wep))+head_wep+wbody

        class FakeDecoder:
            GAME_PORTS={'11003','11004'}
            def __init__(self):
                self.batched_sids=None
            def load_cmd_map(self):
                return {1006:'PlayerMainDataRsp',1043:'GetWeaponRsp'}
            def prepare_capture(self,p):
                return p
            def normalize_pcapng(self,p):
                return Path(str(p)+'.normalized'),True
            def list_tcp_streams(self,p):
                return list(range(20))
            def extract_many_tcp_streams(self,p,sids):
                self.batched_sids=list(sids)
                out={sid:('10.0.0.1:40000','14.103.255.172:80',b'',b'',set()) for sid in sids}
                out[0]=('10.100.163.103:53273','14.103.255.172:11003',b'c2s',frame_main,set())
                out[1]=('198.18.0.1:53270','14.103.255.172:11003',b'c2s',frame_main,set())
                out[18]=('198.18.0.1:53271','14.103.255.172:11004',b'c2s-wep',frame_wep,set())
                return out
            def _is_tun_virtual_ip(self,addr):
                host=addr.rsplit(':',1)[0]
                return ipaddress.ip_address(host) in ipaddress.ip_network('198.18.0.0/15')
            def looks_like_game_stream(self,b):
                return len(b)>=2
            def parse_game_stream(self,data):
                pos=0;pkts=[]
                while pos+2<=len(data):
                    hlen=struct.unpack('>H',data[pos:pos+2])[0]
                    head=net_pb2.PacketHead();head.ParseFromString(data[pos+2:pos+2+hlen])
                    start=pos+2+hlen;end=start+head.body_len
                    pkts.append({'msg_id':head.msg_id,'body':data[start:end],'decompress_err':None})
                    pos=end
                return pkts,0
            def decode_proto_body(self,name,raw):
                msg=getattr(net_pb2,name)()
                msg.ParseFromString(raw)
                from google.protobuf import json_format
                return json_format.MessageToDict(msg,preserving_proto_field_name=True),None

        dec=FakeDecoder()
        counts,_,stream_count=checklist.scan_pcap(dec,Path('dummy.pcapng'))
        self.assertEqual(stream_count,20)
        self.assertEqual(dec.batched_sids,list(range(20)))
        self.assertEqual(counts['PlayerMainDataRsp'],1)
        self.assertEqual(counts['GetWeaponRsp'],1)


class RuntimePortTests(unittest.TestCase):
    """Channels that pick their game port at runtime must still be decoded."""
    def frame(self,msg_id,body=b'',flag=0):
        # Frame header length is big-endian, matching HEADER_STRUCT in the decoder.
        import struct
        head=net_pb2.PacketHead(msg_id=msg_id,seq_id=1,packet_id=1,flag=flag,body_len=len(body))
        raw=head.SerializeToString()
        return struct.pack('>H',len(raw))+raw+body
    def setUp(self):
        import sys
        tool=source_file(Path(__file__).resolve().parent.parent, 'backup-tool')
        sys.path.insert(0,str(tool))
        sys.path.insert(0,str(tool/'proto_gen'))
        global net_pb2
        import net_pb2
    def test_non_game_bytes_are_not_a_game_stream(self):
        import parse_pcap as decoder
        self.assertFalse(decoder.looks_like_game_stream(b''))
        self.assertFalse(decoder.looks_like_game_stream(b'\xff\xff\xff\xff'))
    def test_pick_directions_prefers_parsable_side(self):
        import parse_pcap as decoder
        good=self.frame(1001,b'\x08\x01')
        self.assertEqual(decoder.pick_directions(b'GET / HTTP/1.1\r\n\r\n',good)[1][0],'s2c')
        self.assertEqual(decoder.pick_directions(good,b'HTTP')[0][0],'s2c')
    def test_pick_directions_keeps_both_sides(self):
        import parse_pcap as decoder
        self.assertEqual([d[0] for d in decoder.pick_directions(b'\x00\x00',b'\x00\x00')],['c2s','s2c'])

    def test_parse_pcap_batched_follow_dedups_tun_mirror_and_ignores_nul_keepalive(self):
        from contextlib import redirect_stdout
        import io
        import parse_pcap as decoder
        from unittest import mock

        body=decoder.net_pb2.PlayerMainDataRsp(status=1,player_id=12345,player_label=12345).SerializeToString()
        rsp=self.frame(1006,body)
        req=self.frame(1001,b'\x08\x01')

        stdout=(
            '===================================================================\n'
            'Follow: tcp,raw\nFilter: tcp.stream eq 0\nNode 0: 14.103.255.172:11003\nNode 1: 10.100.163.103:53273\n'
            +rsp.hex()+'\n\t'+req.hex()+'\n'
            '===================================================================\n'
            '===================================================================\n'
            'Follow: tcp,raw\nFilter: tcp.stream eq 1\nNode 0: 14.103.255.172:11003\nNode 1: 198.18.0.1:53270\n'
            +rsp.hex()+'\n\t'+req.hex()+'\n'
            '===================================================================\n'
            '===================================================================\n'
            'Follow: tcp,raw\nFilter: tcp.stream eq 2\nNode 0: 14.103.83.28:21001\nNode 1: 10.100.163.103:53289\n'
            '\t00\n'
            '===================================================================\n'
        )
        sections=decoder._parse_follow_sections(stdout)
        self.assertEqual(len(sections),3)

        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            pcap=root/'capture.pcapng';pcap.write_bytes(b'\x0a\x0d\x0d\x0a'+b'\x00'*24)
            out_mixed=root/'out_mixed'
            fake_many={
                0:('14.103.255.172:11003','10.100.163.103:53273',req,rsp,set()),
                1:('14.103.255.172:11003','198.18.0.1:53270',req,rsp,set()),
                2:('14.103.83.28:21001','10.100.163.103:53289',b'\x00',b'',set()),
            }
            with redirect_stdout(io.StringIO()), \
                 mock.patch.object(decoder,'prepare_capture',side_effect=lambda p:p), \
                 mock.patch.object(decoder,'normalize_pcapng',side_effect=lambda p:(p,False)), \
                 mock.patch.object(decoder,'list_tcp_streams',return_value=[0,1,2]), \
                 mock.patch.object(decoder,'extract_many_tcp_streams',return_value=fake_many):
                decoder.analyze_pcap(pcap,out_mixed)
            summary=json.loads((out_mixed/'summary.json').read_text(encoding='utf-8'))
            self.assertEqual(summary['total_packets'],2)
            self.assertEqual(summary['incomplete_stream_count'],0)

            out_tun_only=root/'out_tun'
            fake_tun={1:('14.103.255.172:11003','198.18.0.1:53270',req,rsp,set())}
            with redirect_stdout(io.StringIO()), \
                 mock.patch.object(decoder,'prepare_capture',side_effect=lambda p:p), \
                 mock.patch.object(decoder,'normalize_pcapng',side_effect=lambda p:(p,False)), \
                 mock.patch.object(decoder,'list_tcp_streams',return_value=[1]), \
                 mock.patch.object(decoder,'extract_many_tcp_streams',return_value=fake_tun):
                decoder.analyze_pcap(pcap,out_tun_only)
            tun_summary=json.loads((out_tun_only/'summary.json').read_text(encoding='utf-8'))
            self.assertEqual(tun_summary['total_packets'],2)
            self.assertEqual(tun_summary['incomplete_stream_count'],0)

    def test_atomic_json_survives_transient_permission_error(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'progress.json'
            calls = {'n': 0}
            real_replace = Path.replace

            def flaky_replace(self_path, dst):
                calls['n'] += 1
                if calls['n'] <= 2:
                    raise PermissionError(13, 'Access is denied')
                return real_replace(self_path, dst)

            with mock.patch.object(Path, 'replace', flaky_replace):
                checklist.atomic_json(target, {'ok': True, 'livePackets': 42})
            self.assertEqual(calls['n'], 3)
            self.assertEqual(json.loads(target.read_text(encoding='utf-8'))['livePackets'], 42)

    def test_ndis_etw_event_1001_ethernet_and_vlan_extraction(self):
        import cramjam
        import struct
        import sys
        tool = source_file(Path(__file__).resolve().parent.parent, 'backup-tool')
        sys.path.insert(0, str(tool)); sys.path.insert(0, str(tool / 'proto_gen'))
        import parse_pcap as real_decoder

        inv = {v: k for k, v in real_decoder.load_cmd_map().items()}
        raw_main = real_decoder.net_pb2.PlayerMainDataRsp(status=1, player_id=54321, player_label=54321).SerializeToString()
        comp_main = bytes(cramjam.snappy.compress_raw(raw_main))
        h_main = real_decoder.net_pb2.PacketHead(msg_id=inv['PlayerMainDataRsp'], seq_id=1, packet_id=1, flag=1, body_len=len(comp_main)).SerializeToString()
        payload = struct.pack('>H', len(h_main)) + h_main + comp_main

        total = 20 + 20 + len(payload)
        ip = struct.pack('>BBHHHBBH4s4s', 0x45, 0, total, 0, 0x4000, 128, 6, 0,
                         bytes([14, 103, 255, 173]), bytes([10, 100, 163, 103]))
        tcp = struct.pack('>HHIIBBHHH', 11002, 55123, 100, 0, 0x50, 0x18, 65535, 0, 0)
        vlan_frame = b'\x11' * 6 + b'\x22' * 6 + b'\x81\x00\x00\x01\x08\x00' + ip + tcp + payload
        event_1001 = struct.pack('<III', 7, 7, len(vlan_frame)) + vlan_frame

        extracted_frag = checklist.NdisEtwCapture.parse_event_1001(event_1001)
        self.assertEqual(extracted_frag, vlan_frame)
        tracker = checklist._LiveTcpTracker(real_decoder)
        tracker.handle_link_frame(extracted_frag, {'10.100.163.103'})
        counts, _empty, readable = tracker.snapshot()
        self.assertTrue(readable)
        self.assertEqual(counts['PlayerMainDataRsp'], 1)

if __name__=='__main__':unittest.main()
