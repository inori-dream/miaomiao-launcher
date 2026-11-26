"""Synthetic evidence classification tests. Never opens a real capture or DB."""
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
        data=self.items(readable=True);self.assertEqual(data['weapon']['state'],'collected')
        self.assertEqual(data['armor']['state'],'waiting')
    def test_bag_only_gear_warns_that_equipment_is_incomplete(self):
        """PackNotice gear marks the group collected but is not the equipment list."""
        self.event('PackNotice',{'items':[{'main_item':{
            'weapon':{'instance_id':42,'weapon_id':1},
            'armor':{'instance_id':43,'armor_id':2},
            'poster':{'instance_id':44,'poster_id':3}}}]})
        data=self.items(readable=True)
        for group in ('weapon','armor','poster'):
            self.assertEqual(data[group]['state'],'collected')
        report=checklist.report(self.counts,self.empty,phase='complete',readable=True)
        for name in ('GetWeaponRsp','GetArmorRsp','GetPosterRsp'):
            self.assertIn(name,report['warning'])
        self.assertIn('装备',report['warning'])
    def test_authoritative_equipment_list_suppresses_the_warning(self):
        self.event('PackNotice',{'items':[{'main_item':{'weapon':{'instance_id':42,'weapon_id':1}}}]})
        self.event('GetWeaponRsp',{'weapons':[{'instance_id':42,'weapon_id':1}]})
        self.event('GetArmorRsp',{'armors':[]})
        self.event('GetPosterRsp',{'posters':[]})
        report=checklist.report(self.counts,self.empty,phase='complete',readable=True)
        self.assertEqual(report['warning'],'')
    def test_no_gear_at_all_does_not_warn(self):
        self.event('PlayerMainDataRsp',{'status':1,'player_id':7,'characters':[]})
        report=checklist.report(self.counts,self.empty,phase='complete',readable=True)
        self.assertEqual(report['warning'],'')
    def test_city_scene_does_not_count_as_home(self):
        self.event('SceneDataNotice',{'data':{'scene_id':1001}})
        self.assertEqual(self.items(readable=True)['garden']['state'],'waiting')
        self.event('SceneDataNotice',{'data':{'scene_id':9999}})
        self.assertEqual(self.items(readable=True)['garden']['state'],'collected')
    def test_final_report_ignores_request_counts_and_failed_json(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'json').mkdir()
            (root/'summary.json').write_text(json.dumps({'message_counts':{'GetWeaponRsp':99}}))
            (root/'json/1_s0_s2c_1_GetPetRsp.json').write_text(json.dumps({'status':1,'pets':[]}))
            (root/'json/2_s0_s2c_2_GetMailsRsp.json').write_text(json.dumps({'status':2}))
            result={i['id']:i for i in checklist.final_report(root)['items']}
            self.assertEqual(result['pet']['state'],'empty')
            self.assertEqual(result['weapon']['state'],'missing');self.assertEqual(result['mail']['state'],'missing')
    def test_missing_json_folder_is_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            result=checklist.final_report(d)
            self.assertTrue(all(i['state']=='unknown' for i in result['items']))


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
        tool=Path(__file__).resolve().parent/'backup-tool'
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

if __name__=='__main__':unittest.main()
