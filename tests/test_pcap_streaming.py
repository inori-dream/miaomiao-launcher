"""Metadata, safety and memory regressions for mixed PktMon interfaces."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import struct
import sys
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent.parent / 'backup-tool'))
import pcap_compat as compat
from test_wifi_compat import ipv4_tcp, wifi
# SIBLING_TESTS: this suite imports sibling test modules by name.
sys.path.insert(0, str(Path(__file__).resolve().parent))


def block(kind,body,endian='<'):
    body += b'\0' * (-len(body) % 4)
    size=len(body)+12
    return struct.pack(endian+'II',kind,size)+body+struct.pack(endian+'I',size)


def shb(endian='<',length=-1):
    return block(0x0a0d0d0a,struct.pack(endian+'IHHq',0x1a2b3c4d,1,0,length),endian)


def idb(endian='<',options=b'',linktype=1):
    return block(1,struct.pack(endian+'HHI',linktype,0,65535)+options,endian)


def epb(packet,endian='<',iid=0,ts=1000000000):
    return block(6,struct.pack(endian+'IIIII',iid,ts>>32,ts&0xffffffff,len(packet),len(packet))+packet,endian)


def options(endian='<'):
    return (struct.pack(endian+'HHB3x',9,1,9)
            +struct.pack(endian+'HHq',14,8,60)
            +struct.pack(endian+'HH4s',2,4,b'test')
            +struct.pack(endian+'HH',0,0))


class StreamingCompatTests(unittest.TestCase):
    def convert(self,raw):
        path=Path(self.directory)/'capture.pcapng'
        path.write_bytes(raw)
        with redirect_stdout(io.StringIO()): result=compat.prepare_capture(path)
        self.assertEqual(path.read_bytes(),raw)
        return path,result,list(compat._blocks(result))

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.directory=self.tmp.name

    def tearDown(self): self.tmp.cleanup()

    def test_clone_preserves_all_options_in_both_endiannesses(self):
        for endian in ('<','>'):
            with self.subTest(endian=endian):
                original=idb(endian,options(endian))
                source,result,blocks=self.convert(shb(endian)+original+epb(wifi(),endian)+epb(ipv4_tcp(),endian))
                self.assertNotEqual(source,result)
                ids=[b for _,k,_,b in blocks if k==1]
                self.assertEqual(len(ids),2)
                for description in ids:
                    self.assertEqual(description[10:],original[10:])
                self.assertEqual([struct.unpack_from(endian+'H',b,8)[0] for b in ids],[105,101])
                packets=[b for _,k,_,b in blocks if k==6]
                self.assertEqual([struct.unpack_from(endian+'III',b,8) for b in packets],[(0,0,1000000000),(1,0,1000000000)])

    def test_raw_majority_clears_wifi_flag_on_secondary_interface(self):
        source,result,blocks=self.convert(shb()+idb()+epb(ipv4_tcp())*2+epb(wifi()))
        self.assertNotEqual(source,result)
        descriptions=[struct.unpack_from('<H',b,8)[0] for _,k,_,b in blocks if k==1]
        self.assertEqual(descriptions,[101,105])
        packets=[b for _,k,_,b in blocks if k==6]
        self.assertEqual(struct.unpack_from('<I',packets[-1],8)[0],1)
        self.assertEqual(packets[-1][29]&0x40,0)

    def test_finite_section_length_tracks_cloned_idb(self):
        for endian in ('<','>'):
            with self.subTest(endian=endian):
                body=idb(endian,options(endian))+epb(wifi(),endian)+epb(ipv4_tcp(),endian)
                _,result,blocks=self.convert(shb(endian,len(body))+body)
                output=result.read_bytes()
                header=blocks[0][3]
                self.assertEqual(struct.unpack_from(endian+'q',header,16)[0],len(output)-len(header))

    def test_invalid_iid_keeps_entire_file_unchanged(self):
        raw=shb()+idb()+epb(wifi())+epb(ipv4_tcp())+epb(b'unknown',iid=1)
        source,result,_=self.convert(raw)
        self.assertEqual(source,result)
        self.assertFalse(source.with_name('capture.link-fixed.pcapng').exists())

    def test_interface_must_be_defined_before_packet(self):
        raw=shb()+idb()+epb(wifi(),iid=1)+idb()
        source,result,_=self.convert(raw)
        self.assertEqual(source,result)

    def test_interleaved_idbs_keep_existing_interface_numbers(self):
        raw=shb()+idb()+epb(wifi())+idb(linktype=105)+epb(wifi(False),iid=1)+epb(ipv4_tcp())
        _,_,blocks=self.convert(raw)
        self.assertEqual([struct.unpack_from('<H',b,8)[0] for _,k,_,b in blocks if k==1],[105,105,101])
        self.assertEqual([struct.unpack_from('<I',b,8)[0] for _,k,_,b in blocks if k==6],[0,1,2])

    def test_multiple_sections_reset_interfaces_and_byte_order(self):
        first=shb()+idb()+epb(wifi())+epb(ipv4_tcp())
        second=shb('>')+idb('>')+epb(ipv4_tcp(),'>')*2+epb(wifi(),'>')
        _,_,blocks=self.convert(first+second)
        links=[struct.unpack_from(e+'H',b,8)[0] for _,k,e,b in blocks if k==1]
        self.assertEqual(links,[105,101,101,105])

    def test_memory_does_not_scale_with_packet_count(self):
        path=Path(self.directory)/'memory.pcapng'
        with path.open('wb') as out:
            out.write(shb()+idb())
            packet=epb(ipv4_tcp())
            for _ in range(60000): out.write(packet)
        tracemalloc.start()
        try:
            with redirect_stdout(io.StringIO()): compat.prepare_capture(path)
            _,peak=tracemalloc.get_traced_memory()
        finally: tracemalloc.stop()
        self.assertLess(peak,1024*1024,'Do not retain per-packet buffers/indexes')

    def test_idempotent_derived_path(self):
        _,result,_=self.convert(shb()+idb()+epb(wifi()))
        self.assertEqual(compat.prepare_capture(result),result)

    def test_source_change_during_write_does_not_publish_copy(self):
        path=Path(self.directory)/'capture.pcapng'
        path.write_bytes(shb()+idb()+epb(wifi()))
        original=compat._write
        def changed(source,target,sections):
            original(source,target,sections)
            with Path(source).open('ab') as out: out.write(b'changed')
        with patch.object(compat,'_write',changed):
            self.assertEqual(compat.prepare_capture(path),path)
        self.assertFalse(path.with_name('capture.link-fixed.pcapng').exists())
        self.assertFalse(list(Path(self.directory).glob('*.tmp')))

    def test_invalid_lengths_and_options_never_generate_copy(self):
        for raw in (shb( length=100)+idb(),shb()+idb(options=b'\x09\0\x08\0'),shb()+idb()+epb(ipv4_tcp())[:-1]):
            with self.subTest(raw=raw[:20]):
                path=Path(self.directory)/'bad.pcapng';path.write_bytes(raw)
                self.assertEqual(compat.prepare_capture(path),path)
                self.assertFalse(path.with_name('bad.link-fixed.pcapng').exists())

if __name__=='__main__': unittest.main()
