import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from test_official_import import main_data, make_zip
from verify_backup import verify_backup


class BackupIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_valid_capture_has_identity_hash_and_quality(self):
        source = make_zip(self.root / 'account.zip', main_data(777, name='Synthetic'))
        report = verify_backup(source)
        self.assertTrue(report['ok'])
        self.assertEqual(report['uid'], 777)
        self.assertEqual(report['name'], 'Synthetic')
        self.assertEqual(len(report['sha256']), 64)
        self.assertIsInstance(report['complete'], bool)
        self.assertIn('incomplete', report['quality'])

    def test_summary_gap_marks_backup_incomplete(self):
        source = self.root / 'account.zip'
        with zipfile.ZipFile(source, 'w') as archive:
            archive.writestr('json/00001_s0_s2c_1006_PlayerMainDataRsp.json',
                             json.dumps(main_data(777)))
            archive.writestr('summary.json', json.dumps({
                'player_uids': [777], 'total_packets': 1,
                'incomplete_stream_count': 1,
                'incomplete_streams': [{'stream_id': 0, 'direction': 's2c',
                                        'tcp_gap': True, 'trailing_bytes': 0}],
            }))
        report = verify_backup(source)
        self.assertFalse(report['complete'])
        self.assertGreaterEqual(report['quality']['incompleteStreams'], 1)

    def test_unsafe_zip_member_is_rejected(self):
        source = self.root / 'unsafe.zip'
        with zipfile.ZipFile(source, 'w') as archive:
            archive.writestr('../escape.json', '{}')
        with self.assertRaises(ValueError):
            verify_backup(source)


if __name__ == '__main__':
    unittest.main()
