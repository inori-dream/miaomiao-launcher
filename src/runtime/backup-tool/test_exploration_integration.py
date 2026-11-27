"""Synthetic only: no tshark, real capture, personal DB or installation."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import parse_pcap as parser
from exploration import Exploration
p = parser.net_pb2


def frame(msg_id, body, flag=0):
    head = p.PacketHead(msg_id=msg_id, body_len=len(body), flag=flag).SerializeToString()
    return len(head).to_bytes(2, 'big') + head + body


class ExplorationTests(unittest.TestCase):
    def test_catalog_mismatch_and_absence(self):
        report = Exploration(p).summary([7])
        crystal = report['maps']['3']['crystals']
        self.assertEqual((crystal['total'], crystal['catalog_id_count']), (264, 269))
        self.assertTrue(crystal['mismatch'])
        self.assertIsNone(crystal['collected'])
        self.assertIsNone(crystal['percentage'])
        self.assertFalse(crystal['total_verified'])
        self.assertEqual(len(report['catalog']['local_catalog_sha256']), 64)

    def test_monotonic_and_unknown(self):
        e = Exploration(p)
        item = e.catalog['maps']['3']['collections']['8'][0]
        for ids in ([item, 99999999], []):
            e.add('GetCollectItemIdsRsp', p.GetCollectItemIdsRsp(status=1, item_ids=ids).SerializeToString(), {'seq': 1})
        for value in (10, 5):
            rsp = p.SceneProcessListRsp(status=1)
            rsp.scene_process_list.add(scene_id=3, process=value)
            e.add('SceneProcessListRsp', rsp.SerializeToString(), {'seq': value})
        report = e.summary([7])
        self.assertEqual(report['maps']['3']['crystals']['collected'], 1)
        self.assertEqual(report['maps']['3']['points']['collected'], 10)
        self.assertEqual(report['unknown_ids'], [99999999])
        self.assertIsNone(e.summary([7, 8])['maps']['3']['crystals']['collected'])

    def test_status_and_scene_partial(self):
        e = Exploration(p)
        e.add('GetCollectItemIdsRsp', p.GetCollectItemIdsRsp(status=0, item_ids=[999]).SerializeToString(), {})
        scene = p.SceneDataNotice(status=1)
        scene.data.scene_id = 3
        e.add('SceneDataNotice', scene.SerializeToString(), {})
        report = e.summary([7])
        self.assertEqual(report['unknown_ids'], [])
        self.assertEqual(report['maps']['3']['collection_coverage'], 'scene_snapshot_partial')
        self.assertFalse(report['maps']['3']['complete'])
        self.assertIsNone(report['maps']['3']['crystals']['collected'])

    def test_snappy_budget_and_tail(self):
        compressed = bytes(parser.cramjam.snappy.compress_raw(b'abc'))
        packets, tail = parser.parse_game_stream(frame(999, compressed, 1) + b'\x00')
        self.assertEqual(packets[0]['body'], b'abc')
        self.assertEqual(tail, 1)
        with patch.object(parser, 'MAX_DECOMPRESSED_BODY', 2):
            with patch.object(parser.cramjam.snappy, 'decompress_raw') as decompress:
                packets, _ = parser.parse_game_stream(frame(999, compressed, 1))
                decompress.assert_not_called()
                self.assertIn('budget', packets[0]['decompress_err'])
                self.assertEqual(packets[0]['body'], compressed)
        with patch.object(parser, 'MAX_STREAM_DECOMPRESSED', 4):
            packets, _ = parser.parse_game_stream(frame(999, compressed, 1) * 2)
            self.assertIsNone(packets[0]['decompress_err'])
            self.assertIn('budget', packets[1]['decompress_err'])

    def export(self, uids):
        mapping = {1006: 'PlayerMainDataRsp', 30001: 'GetCollectItemIdsRsp',
                   30002: 'SceneProcessListRsp'}
        payload = b''.join(frame(1006, p.PlayerMainDataRsp(player_id=uid, player_name='Synthetic').SerializeToString()) for uid in uids)
        item = Exploration(p).catalog['maps']['3']['collections']['8'][0]
        payload += frame(30001, p.GetCollectItemIdsRsp(status=1, item_ids=[item]).SerializeToString())
        payload += frame(30002, b'\xff') + frame(39999, b'raw') + b'\x00'
        login = p.PlayerLoginRsp()
        login.client_log_server_token = 'SECRET'
        mapping[1004] = 'PlayerLoginRsp'
        payload += b''  # Tail remains intentionally incomplete.
        # Token response goes before the tail, so it is also decoded/sanitized.
        payload = frame(1004, login.SerializeToString()) + payload
        c2s = frame(30001, p.GetCollectItemIdsRsp(status=1, item_ids=[99999998]).SerializeToString()) + frame(1001, b'SECRET')
        with tempfile.TemporaryDirectory(prefix='synthetic_', dir=Path(__file__).parent) as temp:
            out = Path(temp)
            with patch.object(parser, 'prepare_capture', side_effect=lambda x: x), patch.object(parser, 'load_cmd_map', return_value=mapping), patch.object(parser, 'list_tcp_streams', return_value=[0]), patch.object(parser, 'follow_tcp_stream', return_value=('client:4567', 'server:11003', c2s, payload, [])), contextlib.redirect_stdout(io.StringIO()):
                parser.analyze_pcap(Path('synthetic.pcap'), out)
            summary = json.loads((out / 'summary.json').read_text(encoding='utf-8'))
            self.assertEqual(summary['exploration_report'], 'exploration.txt')
            self.assertTrue((out / summary['exploration_report']).is_file())
            self.assertTrue(summary['exploration']['report_available'])
            self.assertTrue(summary['exploration']['evidence_available'])
            self.assertIn('未知不当0', summary['exploration']['summary'])
            self.assertEqual(summary['sanitized_login_responses'], 1)
            self.assertEqual(summary['omitted_login_requests'], 1)
            self.assertEqual(summary['trailing_bytes_count'], 1)
            self.assertEqual(summary['successful_decode_count'], len(uids) + 3)
            self.assertEqual(summary['decode_fallback_count'], 2)
            self.assertNotIn('SceneProcessListRsp', summary['successful_message_counts'])
            self.assertNotIn(99999998, summary['exploration']['unknown_ids'])
            self.assertTrue(any('_s2c_' in x.name for x in (out / 'raw').glob('*.bin')))
            self.assertFalse(any(b'SECRET' in x.read_bytes() for x in (out / 'raw').glob('*.bin')))
            if len(uids) == 1:
                with ZipFile(out / summary['account_zip']) as z:
                    self.assertIn('exploration.json', z.namelist())
                    self.assertIn('exploration.txt', z.namelist())
            else:
                self.assertNotIn('account_zip', summary)
                self.assertEqual(summary['player_highlights'], {})
                self.assertEqual(summary['exploration']['attribution'], 'unattributed_evidence')
                self.assertIsNone(summary['exploration']['maps']['3']['crystals']['collected'])

    def test_single_uid_zip_and_success_counts(self):
        self.export([7])

    def test_multi_uid_no_account_zip(self):
        self.export([7, 8])

    def test_truncated_header_and_body(self):
        valid = frame(1006, p.PlayerMainDataRsp(player_id=7).SerializeToString())
        for payload in (b'\x00', valid[:3], valid[:-1]):
            packets, tail = parser.parse_game_stream(payload)
            self.assertEqual(packets, [])
            self.assertEqual(tail, len(payload))

    def test_explicit_zero_only_for_observed_scene(self):
        e = Exploration(p)
        rsp = p.SceneProcessListRsp(status=1)
        rsp.scene_process_list.add(scene_id=3, process=0)
        e.add('SceneProcessListRsp', rsp.SerializeToString(), {})
        report = e.summary([7])
        self.assertEqual(report['maps']['3']['points']['collected'], 0)
        self.assertIsNone(report['maps']['2']['points']['collected'])

    def test_steam_direction_probe(self):
        payload = frame(1006, p.PlayerMainDataRsp(player_id=7).SerializeToString())
        self.assertEqual(parser.pick_directions(payload, b'other')[0][0], 's2c')


if __name__ == '__main__':
    unittest.main()
