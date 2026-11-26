"""Synthetic backup tests: no live capture, account login or network access."""
from pathlib import Path
import contextlib
import io
import json
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'backup-tool'))
import parse_pcap as parser
import extract_gardens as gardens
import net_pb2
import cramjam


def frame(msg_id, body, compressed=False):
    payload = bytes(cramjam.snappy.compress_raw(body)) if compressed else body
    head = net_pb2.PacketHead(msg_id=msg_id, body_len=len(payload), flag=int(compressed))
    header = head.SerializeToString()
    return struct.pack('>H', len(header)) + header + payload


class BackupTests(unittest.TestCase):
    def test_frames_snappy_and_truncation(self):
        data = frame(1006, b'example', True) + frame(1016, b'tail')
        packets, remaining = parser.parse_game_stream(data)
        self.assertEqual([p['body'] for p in packets], [b'example', b'tail'])
        self.assertEqual(remaining, 0)
        packets, remaining = parser.parse_game_stream(data[:-1])
        self.assertEqual(len(packets), 1)
        self.assertGreater(remaining, 0)

    def test_login_export_removes_known_secret_and_requests(self):
        login = net_pb2.PlayerLoginRsp(client_log_server_token='synthetic-secret')
        payload = frame(1001, b'synthetic-request') + frame(1004, login.SerializeToString())
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'export'
            with patch.object(parser, 'prepare_capture', side_effect=lambda path: path), patch.object(parser, 'list_tcp_streams', return_value=[0]), patch.object(parser, 'follow_tcp_stream', return_value=('127.0.0.1:40000', '127.0.0.1:11003', b'', payload, [])), contextlib.redirect_stdout(io.StringIO()):
                parser.analyze_pcap(Path('synthetic.pcapng'), out)
            summary = json.loads((out / 'summary.json').read_text(encoding='utf-8'))
            self.assertEqual(summary['omitted_login_requests'], 1)
            self.assertEqual(summary['sanitized_login_responses'], 1)
            self.assertEqual(summary['incomplete_streams'], [])
            raw = list((out / 'raw').glob('*.bin'))
            self.assertEqual(len(raw), 1)
            self.assertFalse(net_pb2.PlayerLoginRsp.FromString(raw[0].read_bytes()).client_log_server_token)
            self.assertNotIn('synthetic-secret', ''.join(p.read_text(encoding='utf-8') for p in (out / 'json').glob('*.json')))

    def test_tshark_bidirectional_reassembly_output(self):
        result = type('Result', (), {'returncode': 0, 'stderr': '', 'stdout': 'Node 0: 127.0.0.1:40000\nNode 1: 127.0.0.1:11003\n6162\n\t6364\n====\n'})()
        with patch.object(parser.subprocess, 'run', return_value=result):
            node0, node1, forward, backward = parser.extract_tcp_stream_directions(Path('synthetic.pcapng'), 0)
        self.assertEqual((forward, backward), (b'ab', b'cd'))

    def test_tshark_gap_markers_are_directional_and_stop_reassembly(self):
        reverse = frame(1007, b'')
        after_gap = frame(1008, b'')
        markers = [
            '[1 byte missing in capture file]',
            '10: [21 bytes missing in capture file]',
            b'[21 bytes missing in capture file]'.hex(),
            (b'[21 bytes missing in capture file]\x00').hex(),
        ]
        for marker in markers:
            with self.subTest(marker=marker):
                output = ('Node 0: 192.0.2.1:40000\nNode 1: 192.0.2.2:11003\n'
                          f'6162\n{marker}\n{after_gap.hex()}\n\t{reverse.hex()}\n====\n')
                result = subprocess.CompletedProcess([], 0, stdout=output, stderr='')
                gaps = set()
                with patch.object(parser.subprocess, 'run', return_value=result):
                    _, _, forward, backward = parser.extract_tcp_stream_directions(
                        Path('synthetic.pcapng'), 0, gap_directions=gaps)
                self.assertEqual((forward, backward), (b'ab', reverse))
                self.assertEqual(gaps, {0})

    def test_tshark_gap_does_not_complete_a_partial_game_frame(self):
        first = frame(1008, b'')
        interrupted = frame(1402, b'123456789ABCDE')[:-10]
        after_gap = frame(1352, b'')
        reverse = frame(1007, b'')
        marker = b'[5 bytes missing in capture file]\x00'
        output = ('Node 0: 192.0.2.1:40000\nNode 1: 192.0.2.2:11003\n'
                  f'{first.hex()}\n{interrupted.hex()}\n{marker.hex()}\n'
                  f'{after_gap.hex()}\n\t{reverse.hex()}\n====\n')
        result = subprocess.CompletedProcess([], 0, stdout=output, stderr='')
        with patch.object(parser.subprocess, 'run', return_value=result):
            _, _, forward, backward = parser.extract_tcp_stream_directions(Path('synthetic.pcapng'), 0)
        self.assertEqual(forward, first + interrupted)
        self.assertEqual(backward, reverse)
        packets, trailing = parser.parse_game_stream(forward)
        self.assertEqual([packet['msg_id'] for packet in packets], [1008])
        self.assertEqual(trailing, len(interrupted))

    def test_export_reports_bidirectional_gaps_without_http_or_short_stream_false_positives(self):
        player = net_pb2.PlayerMainDataRsp(player_id=1000000, player_name='测试玩家')
        player_frame = frame(1006, player.SerializeToString())
        fake_player_frame = frame(1006, net_pb2.PlayerMainDataRsp(player_id=2000000).SerializeToString())
        client_frame = frame(1001, b'')
        interrupted = frame(1402, b'abc')[:-1]
        marker_hex = b'[21 bytes missing in capture file]\x00'.hex()
        # Node 0 is the client. Both directions have gaps and complete-looking
        # frames after them; neither direction may contribute those later bytes.
        game_output = ('Node 0: 192.0.2.1:40000\nNode 1: 192.0.2.2:11003\n'
                       f'{client_frame.hex()}\n{interrupted.hex()}\n{marker_hex}\n{client_frame.hex()}\n'
                       f'\t{player_frame.hex()}\n\t{marker_hex}\n\t{fake_player_frame.hex()}\n====\n')
        http_output = ('Node 0: 192.0.2.1:40001\nNode 1: 192.0.2.2:80\n'
                       f'485454502f312e310d0a\n{marker_hex}\n====\n')
        short_output = 'Node 0: 192.0.2.1:40002\nNode 1: 192.0.2.2:443\n\t00\n====\n'
        results = [subprocess.CompletedProcess([], 0, stdout=output, stderr='')
                   for output in (game_output, http_output, short_output)]
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'export'
            stdout = io.StringIO()
            with (patch.object(parser, 'prepare_capture', side_effect=lambda path: path),
                  patch.object(parser, 'load_cmd_map', return_value={1006: 'PlayerMainDataRsp', 1001: 'LoginReq'}),
                  patch.object(parser, 'list_tcp_streams', return_value=[0, 1, 2]),
                  patch.object(parser.subprocess, 'run', side_effect=results),
                  contextlib.redirect_stdout(stdout)):
                parser.analyze_pcap(Path('synthetic.pcapng'), out)
            summary_path = out / 'summary.json'
            summary = json.loads(summary_path.read_text(encoding='utf-8'))
            self.assertEqual(summary['message_counts'], {'PlayerMainDataRsp': 1, 'LoginReq': 1})
            self.assertEqual(summary['player_uids'], [1000000])
            self.assertEqual(summary['omitted_login_requests'], 1)
            self.assertEqual(summary['decode_fallback_count'], 0)
            self.assertEqual(summary['incomplete_streams'], [
                {'stream_id': 0, 'direction': 'c2s', 'tcp_gap': True, 'trailing_bytes': len(interrupted)},
                {'stream_id': 0, 'direction': 's2c', 'tcp_gap': True, 'trailing_bytes': 0},
            ])
            self.assertEqual(summary['incomplete_stream_count'], 2)
            self.assertEqual(summary['trailing_bytes_count'], len(interrupted))
            self.assertEqual(len(summary['trailing_streams']), 1)
            self.assertIn('备份可能不完整', stdout.getvalue())
            self.assertIn('测试玩家', summary_path.read_text(encoding='utf-8'))
            self.assertEqual(len(list((out / 'raw').glob('*.bin'))), 1)
            with ZipFile(out / summary['account_zip']) as archive:
                self.assertEqual(json.loads(archive.read('summary.json').decode('utf-8')), summary)

    def test_export_maps_gap_when_game_port_is_node_zero(self):
        player = net_pb2.PlayerMainDataRsp(player_id=1000000, player_name='node-zero')
        player_frame = frame(1006, player.SerializeToString())
        fake_player_frame = frame(1006, net_pb2.PlayerMainDataRsp(player_id=2000000).SerializeToString())
        marker_hex = b'[21 bytes missing in capture file]\x00'.hex()
        game_output = ('Node 0: 192.0.2.2:11003\nNode 1: 192.0.2.1:40000\n'
                       f'{player_frame.hex()}\n{marker_hex}\n{fake_player_frame.hex()}\n====\n')
        result = subprocess.CompletedProcess([], 0, stdout=game_output, stderr='')
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'export'
            with (patch.object(parser, 'prepare_capture', side_effect=lambda path: path),
                  patch.object(parser, 'load_cmd_map', return_value={1006: 'PlayerMainDataRsp'}),
                  patch.object(parser, 'list_tcp_streams', return_value=[0]),
                  patch.object(parser.subprocess, 'run', return_value=result),
                  contextlib.redirect_stdout(io.StringIO())):
                parser.analyze_pcap(Path('synthetic.pcapng'), out)
            summary = json.loads((out / 'summary.json').read_text(encoding='utf-8'))
            self.assertEqual(summary['player_uids'], [1000000])
            self.assertEqual(summary['incomplete_streams'], [
                {'stream_id': 0, 'direction': 's2c', 'tcp_gap': True, 'trailing_bytes': 0},
            ])

    def test_garden_only_exports_expected_scene(self):
        notice = net_pb2.SceneDataNotice()
        notice.data.scene_id = 9999
        notice.data.channel_id = 1000000
        notice.data.scene_garden_data.garden_furniture_info_map[1].SetInParent()
        payload = frame(1016, notice.SerializeToString())
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            with patch.object(gardens, 'prepare_capture', side_effect=lambda path: path), patch.object(gardens, 'list_tcp_streams', return_value=[0]), patch.object(gardens, 'extract_tcp_stream_directions', return_value=('', '', b'', payload)), contextlib.redirect_stdout(io.StringIO()):
                gardens.extract_gardens(Path('synthetic.pcapng'), out)
            index = json.loads((out / 'gardens_index.json').read_text(encoding='utf-8'))
            self.assertEqual(len(index), 1)
            self.assertEqual(index[0]['owner_uid'], 1000000)
            self.assertEqual(index[0]['furniture_count'], 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
