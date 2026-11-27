"""Upstream 1.0.9 Wi-Fi compatibility regression fixtures; no real user packets."""
import hashlib
from pathlib import Path
import struct
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parent.parent / 'backup-tool'))
from pcap_compat import prepare_capture


def block(kind,body):
    body+=b'\0'*((-len(body))%4);size=len(body)+12
    return struct.pack('<II',kind,size)+body+struct.pack('<I',size)


def capture(packets):
    header=block(0x0a0d0d0a,struct.pack('<IHHq',0x1a2b3c4d,1,0,-1))
    header+=block(1,struct.pack('<HHI',1,0,65535))
    for packet in packets:
        header+=block(6,struct.pack('<IIIII',0,0,0,len(packet),len(packet))+packet)
    return header


def wifi(protected=True):
    control=0x4008 if protected else 8
    return struct.pack('<H',control)+b'\0'*22+b'\xaa\xaa\x03\0\0\0\x08\0'+b'\0'*8

class WifiCompatTests(unittest.TestCase):
    def test_wifi_corrected_copy_preserves_original(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'capture.pcapng';raw=capture([wifi()]);source.write_bytes(raw)
            result=prepare_capture(source)
            self.assertNotEqual(result,source);self.assertEqual(source.read_bytes(),raw)
            changed=result.read_bytes()
            self.assertEqual(struct.unpack_from('<H',changed,36)[0],105)
            self.assertEqual(changed[77]&0x40,0)
    def test_ethernet_is_not_rewritten(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'ethernet.pcapng';source.write_bytes(capture([b'\0'*60]))
            self.assertEqual(prepare_capture(source),source)
    def test_mixed_interface_is_not_guessed_as_wifi(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'mixed.pcapng';raw=capture([wifi(),b'\0'*60]);source.write_bytes(raw)
            self.assertEqual(prepare_capture(source),source);self.assertEqual(source.read_bytes(),raw)
    def test_malformed_input_is_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'bad.pcapng';source.write_bytes(b'not-a-pcap')
            self.assertEqual(prepare_capture(source),source)


def ipv4_tcp(src='198.18.0.1',dst='150.5.131.114',sport=51938,dport=11001,payload=b''):
    """Bare IPv4+TCP packet as recorded on a tunnel adapter."""
    total=20+20+len(payload)
    header=struct.pack('>BBHHHBBH4s4s',0x45,0,total,0,0x4000,128,6,0,
                       bytes(map(int,src.split('.'))),bytes(map(int,dst.split('.'))))
    tcp=struct.pack('>HHIIBBHHH',sport,dport,1,0,0x50,0x18,65535,0,0)
    return header+tcp+payload


class RawIpCompatTests(unittest.TestCase):
    """VPN tunnel adapters deliver bare IP packets under the Ethernet link type."""
    def test_bare_ip_capture_is_relabelled_raw(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'tunnel.pcapng';raw=capture([ipv4_tcp()]);source.write_bytes(raw)
            result=prepare_capture(source)
            self.assertNotEqual(result,source);self.assertEqual(source.read_bytes(),raw)
            self.assertEqual(struct.unpack_from('<H',result.read_bytes(),36)[0],101)
    def test_mixed_ip_and_ethernet_is_not_guessed(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'mixed-ip.pcapng'
            raw=capture([ipv4_tcp(),b'\0'*60]);source.write_bytes(raw)
            self.assertEqual(prepare_capture(source),source)
    def test_genuine_ethernet_is_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            frame=b'\xff'*6+b'\x11'*6+b'\x08\x00'+ipv4_tcp()
            source=Path(d)/'eth.pcapng';source.write_bytes(capture([frame]))
            self.assertEqual(prepare_capture(source),source)
    def test_mixed_wifi_and_bare_ip_splits_interfaces(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'mixed-wifi-ip.pcapng'
            raw=capture([wifi(protected=True),wifi(protected=False),ipv4_tcp()])
            source.write_bytes(raw)
            result=prepare_capture(source)
            self.assertNotEqual(result,source);self.assertEqual(source.read_bytes(),raw)
            changed=result.read_bytes()
            # Two IDBs after SHB: IDB 0 -> 105 (Wi-Fi), IDB 1 -> 101 (RAW IP)
            off,idbs,epbs=0,[],[]
            while off+12<=len(changed):
                kind,length=struct.unpack_from('<II',changed,off)
                if kind==1:idbs.append(struct.unpack_from('<H',changed,off+8)[0])
                elif kind==6:
                    iid,_,_,cap,_=struct.unpack_from('<IIIII',changed,off+8)
                    epbs.append((iid,changed[off+28:off+28+cap]))
                off+=length
            self.assertEqual(idbs,[105,101])
            self.assertEqual([iid for iid,_ in epbs],[0,0,1])
            self.assertEqual(epbs[0][1][1]&0x40,0)
    def test_non_ethernet_linktype_is_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            header=block(0x0a0d0d0a,struct.pack('<IHHq',0x1a2b3c4d,1,0,-1))
            header+=block(1,struct.pack('<HHI',105,0,65535))
            pkt=wifi(protected=True)
            header+=block(6,struct.pack('<IIIII',0,0,0,len(pkt),len(pkt))+pkt)
            source=Path(d)/'native-wifi.pcapng';source.write_bytes(header)
            self.assertEqual(prepare_capture(source),source)

if __name__=='__main__':unittest.main()
