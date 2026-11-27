"""Regression tests for TCP gap detection using the real packaged TShark."""
import contextlib
import io
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import os
ROOT=Path(__file__).parent
TOOL=ROOT
TSHARK=Path(os.environ.get('OVERFIELD_TSHARK') or (TOOL/'tools/tshark/tshark.exe'))
import sys
sys.path.insert(0,str(TOOL));sys.path.insert(0,str(TOOL/'proto_gen'))
import parse_pcap as parser


def checksum(data):
    if len(data)%2:data+=b'\0'
    total=sum(struct.unpack('!%dH'%(len(data)//2),data))
    while total>>16:total=(total&0xffff)+(total>>16)
    return (~total)&0xffff


def packet(src,dst,sport,dport,seq,ack,payload,flags=0x18):
    import socket
    tcp=struct.pack('!HHIIBBHHH',sport,dport,seq,ack,5<<4,flags,65535,0,0)+payload
    ip=struct.pack('!BBHHHBBH4s4s',0x45,0,20+len(tcp),1,0,64,6,0,socket.inet_aton(src),socket.inet_aton(dst))
    ip=ip[:10]+struct.pack('!H',checksum(ip))+ip[12:]
    pseudo=socket.inet_aton(src)+socket.inet_aton(dst)+struct.pack('!BBH',0,6,len(tcp))
    tcp=tcp[:16]+struct.pack('!H',checksum(pseudo+tcp))+tcp[18:]
    return b'\x02'*6+b'\x04'*6+b'\x08\x00'+ip+tcp


def write_pcap(path,frames):
    with path.open('wb') as f:
        f.write(struct.pack('<IHHiIII',0xa1b2c3d4,2,4,0,0,65535,1))
        for i,frame in enumerate(frames):
            f.write(struct.pack('<IIII',1700000000+i,0,len(frame),len(frame)));f.write(frame)


def game_body(status=1,player_id=777):
    import net_pb2
    return net_pb2.PlayerMainDataRsp(status=status,player_id=player_id,player_label=player_id).SerializeToString()


class GapDetectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not TSHARK.is_file():raise unittest.SkipTest('packaged TShark missing')
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        p=patch.object(parser,'TSHARK',TSHARK);p.start();self.addCleanup(p.stop)
    def gap_capture(self,second_chunk=19):
        path=self.root/'gap.pcap'
        write_pcap(path,[packet('10.0.0.1','10.0.0.2',40000,11003,1000,1,b'HELLO12345'),
                         packet('10.0.0.1','10.0.0.2',40000,11003,1000+5+second_chunk,1,b'AFTER'),
                         packet('10.0.0.2','10.0.0.1',11003,40000,1,1000+5+second_chunk+5,b'RESPONSE')])
        return path
    def test_real_tshark_gap_is_detected_and_suffix_dropped(self):
        path=self.gap_capture()
        node0,node1,forward,backward,gaps=parser.follow_tcp_stream(path,0)
        self.assertEqual(forward,b'HELLO12345')
        self.assertEqual(backward,b'RESPONSE')
        self.assertEqual(gaps,[0])
    def test_gap_only_recorded_when_prefix_absent(self):
        path=self.gap_capture(second_chunk=0)
        _,_,forward,_,gaps=parser.follow_tcp_stream(path,0)
        self.assertEqual(forward,b'HELLO12345')
        self.assertEqual(gaps,[])
    def test_marker_prefix_preserves_real_bytes(self):
        marker=b'[5 bytes missing in capture file]\x00'
        output=(b'\n===================================================================\n'
                b'Follow: tcp,raw\nFilter: tcp.stream eq 0\nNode 0: 1.1.1.1:1\nNode 1: 2.2.2.2:11003\n'
                +(b'ABCDEF'+marker).hex().encode()+b'\n'
                b'\t'+b'RESPONSE'.hex().encode()+b'\n'
                b'===================================================================\n')
        result=subprocess.CompletedProcess([],0,output.decode(),'')
        with patch.object(parser.subprocess,'run',return_value=result):
            _,_,forward,backward,gaps=parser.follow_tcp_stream(self.root/'unused.pcap',0)
        self.assertEqual(forward,b'ABCDEF')
        self.assertEqual(gaps,[0])
        self.assertEqual(backward,b'RESPONSE')
    def test_repeated_gap_same_direction_does_not_reassemble(self):
        marker=b'[3 bytes missing in capture file]\x00'
        lines=[b'AAA',marker,b'BBB',marker,b'CCC']
        output=(b'\nFollow: tcp,raw\nNode 0: 1:1\nNode 1: 2:11003\n'+b'\n'.join(x.hex().encode() for x in lines)+b'\n====\n')
        result=subprocess.CompletedProcess([],0,output.decode(),'')
        with patch.object(parser.subprocess,'run',return_value=result):
            _,_,forward,_,gaps=parser.follow_tcp_stream(self.root/'unused.pcap',0)
        self.assertEqual(forward,b'AAA')
        self.assertEqual(gaps,[0])
    def test_gap_is_reported_in_summary_and_zip_still_written(self):
        path=self.gap_capture()
        body=game_body()
        header=struct.pack('>HH',len(parser.net_pb2.PacketHead(msg_id=1006,body_len=len(body),flag=0).SerializeToString()),0)
        # Craft a valid frame prefix before the gap, so the ZIP has real data.
        head=parser.net_pb2.PacketHead(msg_id=1006,body_len=len(body),flag=0).SerializeToString()
        frame=struct.pack('>H',len(head))+head+body
        write_pcap(path,[packet('10.0.0.2','10.0.0.1',11003,40000,1,1,frame),
                         packet('10.0.0.2','10.0.0.1',11003,40000,1+len(frame)+9,1,b'ORPHAN'),
                         packet('10.0.0.1','10.0.0.2',40000,11003,1,1+len(frame)+14,b'')])
        out=self.root/'export'
        with contextlib.redirect_stdout(io.StringIO()):
            parser.analyze_pcap(path,out)
        summary=json.loads((out/'summary.json').read_text(encoding='utf-8'))
        self.assertGreaterEqual(summary['tcp_gap_count'],1)
        # A gap with no surviving tail is reported in the gap list; trailing_streams
        # only carries directions that also have undecoded bytes left over.
        self.assertTrue(any(row.get('tcp_gap') for row in summary['incomplete_streams']))
        self.assertTrue(any(row.get('tcp_gap') for row in summary['tcp_gap_streams']))
        self.assertTrue((out/summary['account_zip']).is_file())


if __name__=='__main__':unittest.main()
