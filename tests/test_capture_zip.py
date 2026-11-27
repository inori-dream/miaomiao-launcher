"""Synthetic account-ZIP delivery tests: no actual user captures or databases."""
import hashlib, tempfile, unittest, warnings, zipfile
from pathlib import Path
import capture_zip, capture_import
from test_official_import import make_zip, main_data
import sys
# SIBLING_TESTS: this suite imports sibling test modules by name.
sys.path.insert(0, str(Path(__file__).resolve().parent))

class CaptureZipTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.source=make_zip(self.root/'account_Test_777.zip',main_data(777))
        self.dest=self.root/'delivered.zip'
    def test_export_delivers_same_zip_and_preserves_original(self):
        original=self.source.read_bytes()
        result=capture_zip.export_account_zip(self.source,self.dest)
        self.assertEqual(self.dest.read_bytes(),original);self.assertEqual(self.source.read_bytes(),original)
        self.assertEqual(result['uid'],777)
        self.assertEqual(result['sha256'],hashlib.sha256(original).hexdigest())
        self.assertEqual(list(self.root.glob('*.db')),[])
    def test_same_destination_preserves_source(self):
        old=self.source.read_bytes()
        capture_zip.export_account_zip(self.source,self.source)
        self.assertEqual(self.source.read_bytes(),old)
    def test_non_zip_destination_rejected(self):
        with self.assertRaises(ValueError):capture_zip.export_account_zip(self.source,self.root/'not-zip.txt')
    def test_raw_network_capture_not_delivered(self):
        with zipfile.ZipFile(self.source,'a') as z:z.writestr('capture.pcapng',b'network')
        with self.assertRaises(ValueError):capture_zip.export_account_zip(self.source,self.dest)
        self.assertFalse(self.dest.exists())
    def test_unsafe_member_does_not_overwrite_destination(self):
        self.dest.write_bytes(b'keep')
        with zipfile.ZipFile(self.source,'a') as z:z.writestr('../outside.json','{}')
        with self.assertRaises(ValueError):capture_zip.export_account_zip(self.source,self.dest)
        self.assertEqual(self.dest.read_bytes(),b'keep')
    def test_missing_uid_is_not_a_fake_backup(self):
        empty=make_zip(self.root/'empty.zip',main_data(0))
        with self.assertRaises(ValueError):capture_zip.export_account_zip(empty,self.dest)
    def test_duplicate_members_rejected(self):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            with zipfile.ZipFile(self.source,'a') as z:z.writestr('json/00001_s0_s2c_1006_PlayerMainDataRsp.json','{}')
        with self.assertRaises(ValueError):capture_zip.export_account_zip(self.source,self.dest)
    def test_delivered_flat_zip_is_readable_by_manual_import(self):
        capture_zip.export_account_zip(self.source,self.dest)
        main,entries=capture_import.read_capture(self.dest)
        self.assertEqual(main['player_id'],777)
        self.assertEqual(len(entries['PlayerMainDataRsp']),1)
    def test_zlib_error_in_member_read_raises_value_error(self):
        import zlib
        from unittest.mock import patch
        with zipfile.ZipFile(self.source) as archive:
            info = archive.infolist()[0]
            with patch.object(archive, 'open', side_effect=zlib.error('corrupt deflate')):
                with self.assertRaisesRegex(ValueError, 'integrity validation failed'):
                    capture_zip.read_member(archive, info)

if __name__=='__main__':unittest.main()
