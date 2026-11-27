"""Synthetic backup tests: no live capture, account login or network access."""
from pathlib import Path
import contextlib
import io
import json
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parent.parent
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
            summary = json.loads((out / 'summary.json').read_text())
            self.assertEqual(summary['omitted_login_requests'], 1)
            self.assertEqual(summary['sanitized_login_responses'], 1)
            raw = list((out / 'raw').glob('*.bin'))
            self.assertEqual(len(raw), 1)
            self.assertFalse(net_pb2.PlayerLoginRsp.FromString(raw[0].read_bytes()).client_log_server_token)
            self.assertNotIn('synthetic-secret', ''.join(p.read_text() for p in (out / 'json').glob('*.json')))

    def test_tshark_bidirectional_reassembly_output(self):
        result = type('Result', (), {'returncode': 0, 'stderr': '', 'stdout': 'Node 0: 127.0.0.1:40000\nNode 1: 127.0.0.1:11003\n6162\n\t6364\n====\n'})()
        with patch.object(parser.subprocess, 'run', return_value=result):
            node0, node1, forward, backward = parser.extract_tcp_stream_directions(Path('synthetic.pcapng'), 0)
        self.assertEqual((forward, backward), (b'ab', b'cd'))

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
            index = json.loads((out / 'gardens_index.json').read_text())
            self.assertEqual(len(index), 1)
            self.assertEqual(index[0]['owner_uid'], 1000000)
            self.assertEqual(index[0]['furniture_count'], 1)


class GardenOwnerProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.codes = {name: code for code, name in parser.load_cmd_map().items()}

    def profile(self, owner=222, name='Offline Owner', status=1, label=8222):
        response = net_pb2.OtherPlayerInfoRsp(status=status)
        response.other_info.player_id = owner
        response.other_info.player_label = label
        response.other_info.nick_name = name
        return frame(self.codes['OtherPlayerInfoRsp'], response.SerializeToString())

    def scene(self, owner=222, visitor=111, name='Visitor'):
        response = net_pb2.SceneDataNotice(status=1)
        response.data.scene_id = 9999
        response.data.channel_id = owner
        response.data.players.add(player_id=visitor, player_name=name)
        response.data.scene_garden_data.garden_furniture_info_map[1].furniture_id = 101
        return response

    def export(self, payload, mode='account', streams=None):
        output = self.root / (mode + '-' + str(len(list(self.root.iterdir()))))
        if streams is None:
            streams = {0: payload}
        if mode == 'account':
            streams = dict(streams)
            first = next(iter(streams))
            main = net_pb2.PlayerMainDataRsp(status=1, player_id=111, player_label=8111, player_name='Recorder')
            streams[first] = frame(self.codes['PlayerMainDataRsp'], main.SerializeToString()) + streams[first]
        with contextlib.redirect_stdout(io.StringIO()):
            if mode == 'account':
                def follow(path, sid):
                    return '192.0.2.1:45678', '198.51.100.1:11003', b'', streams[sid], []
                with patch.object(parser, 'prepare_capture', side_effect=lambda path: path), patch.object(parser, 'list_tcp_streams', return_value=list(streams)), patch.object(parser, 'follow_tcp_stream', side_effect=follow):
                    parser.analyze_pcap(Path('synthetic.pcapng'), output)
                directory = output / 'gardens'
            else:
                with patch.object(gardens, 'prepare_capture', side_effect=lambda path: path), patch.object(gardens, 'list_tcp_streams', return_value=list(streams)), patch.object(gardens, 'extract_tcp_stream_directions', side_effect=lambda path, sid: ('192.0.2.1:45678', '198.51.100.1:11003', b'', streams[sid])):
                    gardens.extract_gardens(Path('synthetic.pcapng'), output)
                directory = output
        rows = json.loads((directory / 'gardens_index.json').read_text(encoding='utf-8'))
        return output, directory, rows

    def test_offline_owner_profiles_work_before_and_after_the_scene_in_both_entrypoints(self):
        scene = self.scene()
        notice = frame(1016, scene.SerializeToString())
        for mode in ('account', 'gardens'):
            for payload in (self.profile() + notice, notice + self.profile()):
                with self.subTest(mode=mode, profile_first=payload.startswith(self.profile())):
                    output, directory, rows = self.export(payload, mode)
                    self.assertEqual([(row['owner_uid'], row['owner_name']) for row in rows], [(222, 'Offline Owner')])
                    self.assertEqual(rows[0]['furniture_count'], 1)
                    self.assertEqual((directory / rows[0]['bin_file']).read_bytes(), scene.data.scene_garden_data.SerializeToString())
                    with ZipFile(directory / rows[0]['zip_file']) as archive:
                        self.assertIsNone(archive.testzip())
                        self.assertFalse(any('OtherPlayerInfoRsp' in name for name in archive.namelist()))
                    if mode == 'account':
                        raw = list((directory / '222/raw').glob('*SceneDataNotice.bin'))
                        self.assertEqual(len(raw), 1)
                        self.assertEqual(raw[0].read_bytes(), scene.SerializeToString())
                        summary = json.loads((output / 'summary.json').read_text(encoding='utf-8'))
                        self.assertEqual(summary['player_uids'], [111])
                        with ZipFile(output / summary['account_zip']) as account:
                            archived = json.loads(account.read('gardens/gardens_index.json'))
                            self.assertEqual(archived[0]['owner_name'], 'Offline Owner')

    def test_names_bind_to_internal_id_across_streams_and_sanitized_name_collisions(self):
        streams = {
            0: self.profile(222, 'Same Name', label=333),
            1: frame(1016, self.scene(333).SerializeToString()),
            2: frame(1016, self.scene(222).SerializeToString()),
            3: self.profile(333, 'Same/Name', label=222),
        }
        for mode in ('account', 'gardens'):
            with self.subTest(mode=mode):
                _, directory, rows = self.export(b'', mode, streams)
                self.assertEqual([(row['owner_uid'], row['owner_name']) for row in rows], [(222, 'Same Name'), (333, 'Same/Name')])
                self.assertEqual(len({row['zip_file'] for row in rows}), 2)
                self.assertTrue(all((directory / row['zip_file']).is_file() for row in rows))

    def test_failed_blank_and_unrelated_profiles_cannot_replace_the_scene_name(self):
        notice = frame(1016, self.scene(visitor=222, name='Scene Owner').SerializeToString())
        bad = self.profile(name='Failed', status=2) + self.profile(name='   ') + self.profile(owner=333, name='Different Owner', label=222)
        for mode in ('account', 'gardens'):
            with self.subTest(mode=mode):
                _, _, rows = self.export(notice + bad, mode)
                self.assertEqual(rows[0]['owner_name'], 'Scene Owner')

    def test_last_valid_profile_wins_and_malformed_profiles_do_not_abort_export(self):
        notice = frame(1016, self.scene().SerializeToString())
        malformed = frame(self.codes['OtherPlayerInfoRsp'], b'\xff')
        payload = self.profile(name='Old Name') + notice + self.profile(name='Current Name') + malformed + self.profile(status=0, name='Unknown Status')
        for mode in ('account', 'gardens'):
            with self.subTest(mode=mode):
                _, _, rows = self.export(payload, mode)
                self.assertEqual(rows[0]['owner_name'], 'Current Name')

    def test_missing_profiles_keep_unknown_owner_instead_of_the_visitor_name(self):
        payload = frame(1016, self.scene().SerializeToString()) + self.profile(owner=333, label=222)
        for mode in ('account', 'gardens'):
            with self.subTest(mode=mode):
                _, _, rows = self.export(payload, mode)
                self.assertEqual(rows[0]['owner_name'], '未知玩家')

    def test_owner_profile_resolution_does_not_rename_the_captured_account(self):
        payload = frame(1016, self.scene().SerializeToString()) + self.profile() + self.profile(owner=111, name='Profile Alias', label=8111)
        output, _, rows = self.export(payload)
        self.assertEqual(rows[0]['owner_name'], 'Offline Owner')
        summary = json.loads((output / 'summary.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['account_zip'], 'account_Recorder_111.zip')
        self.assertEqual(summary['player_highlights']['player_name'], 'Recorder')

    def test_profile_validation_rejects_invalid_fields_without_guessing_from_labels(self):
        names = {}
        invalid = [None, [], {}, {'status': True, 'other_info': {'player_id': 222, 'nick_name': 'Boolean status'}},
                   {'status': 1, 'other_info': []},
                   *[{'status': 1, 'other_info': {'player_id': owner, 'nick_name': 'Invalid'}} for owner in (0, -1, True, '222', 1 << 32)],
                   *[{'status': 1, 'other_info': {'player_id': 222, 'nick_name': name}} for name in (None, [], 123, ' ', 'x' * 257)]]
        for body in invalid:
            parser.collect_player_profile('OtherPlayerInfoRsp', body, names)
        self.assertEqual(names, {})
        valid = {'status': 'StatusCode_OK', 'other_info': {'player_id': 333, 'player_label': 222, 'nick_name': 'Actual Owner'}}
        parser.collect_player_profile('OtherPlayerInfoRsp', valid, names)
        parser.collect_player_profile('OtherPlayerInfoReq', valid, names)
        self.assertEqual(names, {333: 'Actual Owner'})


if __name__ == '__main__':
    unittest.main(verbosity=2)
