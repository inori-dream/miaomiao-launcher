"""Pure-memory gateway regression tests: no DB, sockets, or game process."""
from build.project_layout import source_file
import asyncio
import contextlib
import io
import json
from pathlib import Path
import struct
import unittest
from unittest.mock import AsyncMock, patch

# exec avoids importing dependencies of Manager.__init__ or writing pycache.
ROOT = Path(__file__).resolve().parent.parent
namespace = {'__name__': 'gateway_test_module', '__file__': str(source_file(ROOT, 'room_gateway.py'))}
exec(compile((source_file(ROOT, 'room_gateway.py')).read_text(encoding='utf-8'), str(source_file(ROOT, 'room_gateway.py')), 'exec'), namespace)
packet, integer, blob = (namespace[name] for name in ('packet', 'integer', 'blob'))


def raw_packet(message, body, flag=0):
    head = integer(2, message) + integer(3, flag) + integer(4, len(body)) + integer(5, 1) + integer(6, 7)
    return struct.pack('>H', len(head)) + head + body


class Writer:
    def __init__(self, error=None):
        self.data = bytearray()
        self.closed = False
        self.drained = False
        self.error = error
    def write(self, data): self.data.extend(data)
    async def drain(self):
        if self.error: raise self.error
        self.drained = True
    def close(self): self.closed = True
    def get_extra_info(self, name): return ('synthetic', 0)


class ForwardingTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, response=b'', first=None, extra=b'', client_eof=False, writer_error=None, decode_error=None, other_sessions=None, initial_room=None, respond_after_request=False):
        first = packet(1001, blob(2, b'1')) if first is None else first
        client_reader = asyncio.StreamReader(); client_reader.feed_data(first + extra)
        if client_eof: client_reader.feed_eof()
        upstream = asyncio.StreamReader()
        if not respond_after_request: upstream.feed_data(response)
        # Keep server alive in client-policy cases so the client task determines exit.
        if not extra: upstream.feed_eof()
        client_writer, server_writer = Writer(writer_error), Writer()
        mgr = namespace['Manager'].__new__(namespace['Manager'])
        mgr.sessions = dict(other_sessions or {}); mgr.counter = 0
        mgr.rooms = [{'id': 1, 'closed': False, 'capacity': 20, 'scene': 0},
                     {'id': 2, 'closed': True, 'capacity': 20, 'scene': 0},
                     {'id': 3, 'closed': False, 'capacity': 1, 'scene': 0}]
        mgr.account = lambda value: (1, 'synthetic-secret-name')
        mgr.move = AsyncMock(); mgr.save = lambda: None
        original_write = server_writer.write
        def server_write(data):
            original_write(data)
            if data == first and initial_room is not None: mgr.sessions[1]['room'] = initial_room
            if respond_after_request and data == extra:
                upstream.feed_data(response); upstream.feed_eof()
        server_writer.write = server_write
        open_mock = AsyncMock(return_value=(upstream, server_writer))
        output = io.StringIO()
        actual_decode = namespace['decode_observation']
        def decode(head, body):
            # Every optional decoder invocation MUST happen after drain.
            self.assertTrue(client_writer.drained)
            if decode_error: raise decode_error
            return actual_decode(head, body)
        with patch('asyncio.open_connection', open_mock), patch.dict(namespace, decode_observation=decode), contextlib.redirect_stdout(output):
            await asyncio.wait_for(mgr.connect(client_reader, client_writer, 0), 2)
        logs = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertTrue(client_writer.closed)
        self.assertNotIn('synthetic-secret-name', output.getvalue())
        self.assertNotIn('secret-token', output.getvalue())
        for value in logs:
            self.assertNotIn('uid', value); self.assertNotIn('body', value)
            self.assertEqual(len(value['conn']), 12)
        return client_writer, server_writer, mgr, logs, open_mock

    async def test_valid_login_exact_relay_and_handshake(self):
        response = packet(1004, integer(1, 1) + blob(11, b'existing'))
        client, server, mgr, logs, _ = await self.exercise(response)
        self.assertEqual(bytes(client.data), response)
        self.assertTrue(any(v['event'] == 'frame-forwarded' and v['msgid'] == 1004 for v in logs))
        self.assertFalse(any(v['event'] == 'observe-error' for v in logs))

    async def test_successful_empty_name_backfill_integration(self):
        for flag in (0, 1):
            body = integer(1, 1)
            response = raw_packet(1004, namespace['snappy_literal'](body) if flag else body, flag)
            client, _, _, logs, _ = await self.exercise(response)
            size = struct.unpack('>H', client.data[:2])[0]
            meta = namespace['fields'](client.data[2:2 + size])
            decoded = namespace['decode_body'](meta, bytes(client.data[2 + size:]))
            self.assertEqual(namespace['fields'](decoded)[11], [b'synthetic-secret-name'])
            forwarded = next(v for v in logs if v['event'] == 'frame-forwarded' and v['msgid'] == 1004)
            self.assertIs(forwarded['name_backfilled'], True)

    async def test_unknown_group_still_forwarded(self):
        response = packet(1004, b'\x08\x01\xa3\x06\xa4\x06')
        client, _, mgr, logs, _ = await self.exercise(response)
        self.assertEqual(bytes(client.data), response)
        self.assertTrue(any(v['event'] == 'observe-error' and v['reason'] == 'ValueError' for v in logs))
        mgr.move.assert_not_awaited()

    async def test_fields_failure_and_snappy_failure_do_not_drop_or_move(self):
        for response in (packet(1006, b'\x00'), raw_packet(1006, b'\x04\x00', 1), packet(1016, blob(2, b'\x00'))):
            with self.subTest(response=response):
                client, _, mgr, logs, _ = await self.exercise(response)
                self.assertEqual(bytes(client.data), response)
                self.assertTrue(any(v['event'] == 'observe-error' for v in logs))
                mgr.move.assert_not_awaited()

    async def test_injected_observer_exception_is_not_raw_relay_failure(self):
        response = packet(1006, integer(1, 1))
        client, _, mgr, logs, _ = await self.exercise(response, decode_error=RuntimeError('secret-token'))
        self.assertEqual(bytes(client.data), response)
        self.assertTrue(any(v['event'] == 'observe-error' and v['reason'] == 'RuntimeError' for v in logs))
        mgr.move.assert_not_awaited()

    async def test_empty_main_data_is_unknown_not_ready(self):
        client, _, mgr, logs, _ = await self.exercise(packet(1006, b''))
        self.assertEqual(next(v for v in logs if v['event'] == 'main-data')['status'], None)
        mgr.move.assert_not_awaited()

    async def test_valid_main_data_can_move_after_forwarding(self):
        response = packet(1006, integer(1, 1) + integer(17, 3))
        client, _, mgr, logs, _ = await self.exercise(response)
        self.assertEqual(bytes(client.data), response)
        mgr.move.assert_awaited_once()

    async def test_non_observed_compressed_message_does_not_decode(self):
        response = raw_packet(9999, b'\x04\x00', 1)
        client, _, mgr, logs, _ = await self.exercise(response, decode_error=AssertionError('must not decode'))
        self.assertEqual(bytes(client.data), response)
        self.assertFalse(any(v['event'] in ('observe-error', 'frame-read', 'frame-forwarded') and v['msgid'] == 9999 for v in logs))

    async def test_first_request_authentication_fail_closed(self):
        for first in (packet(1003, b''), packet(1001, b'\x00'), raw_packet(1001, b'\x04\x00', 1), packet(1001, blob(2, b'0'))):
            with self.subTest(first=first):
                _, server, _, logs, opened = await self.exercise(first=first)
                self.assertEqual(bytes(server.data), b''); opened.assert_not_awaited()
                self.assertTrue(any(v['event'] == 'connection-error' for v in logs))

    async def test_room_policy_fail_closed(self):
        for request, sessions in ((packet(1201, integer(2, 2)), {}),
                                  (packet(1201, integer(2, 3)), {99: {'room': 3}}),
                                  (packet(1201, b'\x00'), {}),
                                  (raw_packet(1201, b'\x04\x00', 1), {})):
            with self.subTest(request=request):
                _, server, _, logs, _ = await self.exercise(extra=request, other_sessions=sessions)
                self.assertEqual(bytes(server.data), packet(1001, blob(2, b'1')))
                self.assertTrue(any(v['event'] == 'connection-error' and v['direction'] == 'client' and v['phase'] == 'authorize' for v in logs))

    async def test_unknown_channel_relays_without_premature_registration(self):
        for rid in (0, 999, 0xffffffff):
            with self.subTest(rid=rid):
                request = packet(1201, integer(2, rid))
                _, server, mgr, logs, _ = await self.exercise(extra=request, client_eof=True)
                self.assertEqual(bytes(server.data), packet(1001, blob(2, b'1')) + request)
                self.assertFalse(any(v['event'] == 'connection-error' for v in logs))
                self.assertEqual([r['id'] for r in mgr.rooms], [1, 2, 3])

    async def test_unknown_channel_registers_only_after_server_response(self):
        request = packet(1201, integer(2, 999))
        for scene in (None, 7):
            with self.subTest(scene=scene):
                response = packet(1202, integer(1, 1) + (integer(2, scene) if scene is not None else b'') + integer(3, 999))
                client, server, mgr, logs, _ = await self.exercise(response=response, extra=request, respond_after_request=True)
                self.assertEqual(bytes(server.data), packet(1001, blob(2, b'1')) + request)
                self.assertEqual(bytes(client.data), response)
                self.assertFalse(any(v['event'] == 'connection-error' for v in logs))
                registered = next(room for room in mgr.rooms if room['id'] == 999)
                self.assertEqual(registered['scene'], scene if scene is not None else 0)
                self.assertEqual(registered['capacity'], 50)
                self.assertFalse(registered['closed'])

    async def test_current_full_channel_reentry_is_allowed(self):
        request = packet(1201, integer(2, 3))
        _, server, _, logs, _ = await self.exercise(extra=request, client_eof=True, initial_room=3)
        self.assertEqual(bytes(server.data), packet(1001, blob(2, b'1')) + request)
        self.assertFalse(any(v['event'] == 'connection-error' for v in logs))

    async def test_closed_current_channel_is_still_rejected(self):
        request = packet(1201, integer(2, 2))
        _, server, _, logs, _ = await self.exercise(extra=request, initial_room=2)
        self.assertEqual(bytes(server.data), packet(1001, blob(2, b'1')))
        self.assertTrue(any(v['event'] == 'connection-error' and v['phase'] == 'authorize' for v in logs))

    async def test_channel_label_wrong_wire_or_uint32_overflow_is_rejected(self):
        bodies = [blob(2, b'wrong-wire'), integer(2, 0x100000000), integer(2, 0xffffffffffffffff),
                  integer(2, 999) + b'\xa3\x06\xa4\x06',
                  integer(2, 0x100000000) + integer(2, 999),
                  blob(2, b'wrong-wire') + integer(2, 999)]
        for body in bodies:
            with self.subTest(body=body):
                _, server, _, logs, _ = await self.exercise(extra=packet(1201, body))
                self.assertEqual(bytes(server.data), packet(1001, blob(2, b'1')))
                self.assertTrue(any(v['event'] == 'connection-error' and v['phase'] == 'authorize' and v['reason'] == 'ValueError' for v in logs))

    async def test_header_and_body_bounds_remain_rejected(self):
        invalid = [b'\x00\x00', struct.pack('>H', 4097), raw_packet(1004, b'')]
        # Explicitly malformed scalar and oversized body_len.
        head = integer(2, 1004) + blob(4, b'bad')
        invalid[2] = struct.pack('>H', len(head)) + head
        head = integer(2, 1004) + integer(4, 4 * 1024 * 1024 + 1)
        invalid.append(struct.pack('>H', len(head)) + head)
        for response in invalid:
            with self.subTest(response=response):
                client, _, _, logs, _ = await self.exercise(response)
                self.assertEqual(bytes(client.data), b'')
                self.assertTrue(any(v['event'] == 'connection-error' and v['direction'] == 'server' for v in logs))

    async def test_eof_and_done_exception_have_direction_and_phase(self):
        _, _, _, logs, _ = await self.exercise(b'\x00')
        eof = next(v for v in logs if v['event'] == 'connection-eof')
        self.assertEqual((eof['direction'], eof['phase'], eof['partial_len']), ('server', 'prefix', 1))
        _, _, _, logs, _ = await self.exercise(packet(1004, integer(1, 1)), writer_error=RuntimeError('secret-token'))
        error = next(v for v in logs if v['event'] == 'connection-error')
        self.assertEqual((error['direction'], error['phase'], error['reason']), ('server', 'drain', 'RuntimeError'))
        self.assertEqual(error['forwarded'], 0)

    async def test_frame_compatibility_still_decodes_fail_closed(self):
        reader = asyncio.StreamReader(); reader.feed_data(raw_packet(1004, b'\x02\x04\x08\x01', 1))
        _, _, body = await namespace['frame'](reader)
        self.assertEqual(body, b'\x08\x01')
        reader = asyncio.StreamReader(); reader.feed_data(raw_packet(1004, b'\x04\x00', 1))
        with self.assertRaises(ValueError): await namespace['frame'](reader)


class LoginNameBackfillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Read the actual checked-in generated descriptor, bypass its version guard.
        import ast
        from google.protobuf import descriptor_pool, message_factory
        tree = ast.parse((source_file(ROOT, 'backup-tool/proto_gen/net_pb2.py')).read_text(encoding='utf-8'))
        serialized = next(ast.literal_eval(node.value.args[0]) for node in tree.body
                          if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'DESCRIPTOR' for target in node.targets))
        pool = descriptor_pool.DescriptorPool(); pool.AddSerializedFile(serialized)
        cls.Rsp = message_factory.GetMessageClass(pool.FindMessageTypeByName('PlayerLoginRsp'))
        cls.Head = message_factory.GetMessageClass(pool.FindMessageTypeByName('PacketHead'))

    def rewrite(self, response, name='local-display'):
        size = struct.unpack('>H', response[:2])[0]
        header = response[2:2 + size]; meta = namespace['fields'](header)
        result, body, changed = namespace['backfill_login_name'](response, meta, response[2 + size:], name)
        new_size = struct.unpack('>H', result[:2])[0]
        new_header = result[2:2 + new_size]
        message = self.Rsp.FromString(namespace['decode_body'](meta, body))
        return result, body, changed, self.Head.FromString(new_header), message, new_header

    def test_missing_and_repeated_empty_names_both_compressions(self):
        for flag in (0, 1):
            for names in (b'', blob(11, b''), blob(11, b'') + blob(11, b'')):
                with self.subTest(flag=flag, names=names):
                    body = integer(1, 1) + names + blob(3, b'region')
                    encoded = namespace['snappy_literal'](body) if flag else body
                    raw = raw_packet(1004, encoded, flag)
                    result, encoded_out, changed, head, rsp, _ = self.rewrite(raw, '本地显示')
                    self.assertTrue(changed); self.assertNotEqual(result, raw)
                    self.assertEqual(rsp.status, 1); self.assertEqual(rsp.player_name, '本地显示')
                    self.assertEqual(rsp.region_name, 'region')
                    self.assertEqual(head.flag, flag); self.assertEqual(head.body_len, len(encoded_out))
                    self.assertEqual((head.seq_id, head.total_pack_count), (7, 1))
                    decoded = namespace['decode_body']({3: [flag]}, encoded_out)
                    self.assertEqual(len(namespace['fields'](decoded)[11]), 1)

    def test_nonempty_failed_or_uncertain_frames_stay_exact(self):
        bodies = [integer(1, 1) + blob(11, b'existing'), integer(1, 0), b'',
                  integer(1, 1) + blob(11, b'existing') + blob(11, b''),
                  integer(1, 1) + b'\xa3\x06\xa4\x06',
                  integer(1, 1) + integer(1, 0), integer(1, 1) + integer(11, 0)]
        for flag in (0, 1):
            for body in bodies:
                encoded = namespace['snappy_literal'](body) if flag else body
                response = raw_packet(1004, encoded, flag)
                size = struct.unpack('>H', response[:2])[0]
                result, _, changed = namespace['backfill_login_name'](response, namespace['fields'](response[2:2 + size]), encoded, 'local')
                self.assertFalse(changed); self.assertEqual(result, response)
        raw = raw_packet(1004, b'\x04\x00', 1)
        size = struct.unpack('>H', raw[:2])[0]
        result, _, changed = namespace['backfill_login_name'](raw, namespace['fields'](raw[2:2 + size]), raw[2 + size:], 'local')
        self.assertFalse(changed); self.assertEqual(result, raw)

    def test_unknown_header_bytes_and_all_routing_fields_preserved(self):
        for flag in (0, 1):
            body = integer(1, 1); encoded = namespace['snappy_literal'](body) if flag else body
            unknown = blob(90, b'opaque') + integer(91, 123)
            # Duplicate body_len entries and noncanonical tag encoding stay surgical.
            head = integer(1, 42) + integer(2, 1004) + integer(3, flag) + integer(4, len(encoded)) + unknown + integer(5, 1) + integer(6, 77) + integer(4, len(encoded))
            raw = struct.pack('>H', len(head)) + head + encoded
            result, out, changed, parsed_head, rsp, new_header = self.rewrite(raw)
            self.assertTrue(changed); self.assertIn(unknown, new_header)
            self.assertEqual((parsed_head.packet_id, parsed_head.seq_id, parsed_head.total_pack_count, parsed_head.flag), (42, 77, 1, flag))
            self.assertEqual(namespace['fields'](new_header)[4], [len(out), len(out)])
            old_preserved = b''.join(head[start:end] for n, _, start, _, end, _ in namespace['protobuf_spans'](head) if n != 4)
            new_preserved = b''.join(new_header[start:end] for n, _, start, _, end, _ in namespace['protobuf_spans'](new_header) if n != 4)
            self.assertEqual(new_preserved, old_preserved)

    def test_truncated_snappy_tail_is_uncertain_and_unchanged(self):
        encoded = b'\x02\x04\x08\x01\x00'
        raw = raw_packet(1004, encoded, 1)
        size = struct.unpack('>H', raw[:2])[0]
        result, _, changed = namespace['backfill_login_name'](raw, namespace['fields'](raw[2:2 + size]), encoded, 'local')
        self.assertFalse(changed); self.assertEqual(result, raw)
        with self.assertRaises(ValueError): namespace['snappy'](encoded)

    def test_literal_encoder_roundtrips_boundary_lengths(self):
        for size in (0, 1, 60, 61, 256, 257, 65536, 65537):
            value = b'x' * size
            self.assertEqual(namespace['snappy'](namespace['snappy_literal'](value)), value)


if __name__ == '__main__': unittest.main()
