"""Controller integration regressions; native calls mocked, no real capture."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import official_capture as capture
import official_launcher
from test_official_import import make_zip,main_data
import sys
# SIBLING_TESTS: this suite imports sibling test modules by name.
sys.path.insert(0, str(Path(__file__).resolve().parent))


class CollectorIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.runtime=self.root/'runtime';self.runtime.mkdir()
        self.sessions=self.runtime/'capture-sessions';self.session=self.sessions/'capture-test'
        self.export=self.session/'account_test'/'export';self.export.mkdir(parents=True)
        for name,value in {'RUNTIME':self.runtime,'STATE':self.runtime/'capture-state.json','SESSIONS':self.sessions,
            'STOP_FLAG':self.runtime/'capture-stop.flag','LOG':self.runtime/'capture.log','PACKETMON':self.root/'PktMon.exe'}.items():
            p=patch.object(capture,name,value);p.start();self.addCleanup(p.stop)
        capture.PACKETMON.write_bytes(b'synthetic-not-executable')

    def output(self,extra=None):
        archive=make_zip(self.export/'account_Test_777.zip',main_data(777,label=888))
        summary={'total_packets':7,'player_uids':[777],'account_zip':archive.name}
        summary.update(extra or {})
        (self.export/'summary.json').write_text(json.dumps(summary),encoding='utf-8')
        return archive

    def inspection(self,elevated):
        rows=[{'id':key,'state':'ok','message':key,'messageEn':key} for key in ('collector_root','python','tshark','windows')]
        rows.extend([{'id':'administrator','state':'ok' if elevated else 'error','message':'admin','messageEn':'admin'},
            {'id':'pktmon','state':'ok' if elevated else 'error','message':'permission','messageEn':'permission'},
            {'id':'output_permissions','state':'unknown','message':'not written','messageEn':'not written'}])
        return {'ready':elevated,'checks':rows}

    def run_preflight(self,elevated):
        fake=SimpleNamespace(inspect_runtime=Mock(return_value=self.inspection(elevated)))
        spec=SimpleNamespace(loader=SimpleNamespace(exec_module=lambda module:None))
        with patch.object(capture,'python_exe',return_value=Path('python.exe')),patch.object(capture,'tshark_exe',return_value=Path('tshark.exe')),patch.object(capture,'is_admin',return_value=elevated),patch.object(capture.importlib.util,'spec_from_file_location',return_value=spec),patch.object(capture.importlib.util,'module_from_spec',return_value=fake):
            return capture.preflight()

    def test_preflight_defers_elevation_not_dependencies(self):
        result=self.run_preflight(False)
        self.assertFalse(result['ok'])
        self.assertEqual(result['needsElevation'],capture.os.name=='nt')
        self.assertTrue(result['checks'])
        self.assertFalse(list(self.runtime.glob('*.flag')))

    def test_preflight_elevated_ready_with_optional_unknown(self):
        result=self.run_preflight(True)
        self.assertTrue(result['ok']);self.assertFalse(result['needsElevation'])
        self.assertEqual(result['python'],'python.exe')

    def test_per_mode_private_world_blocks_preflight(self):
        (self.runtime/'world-state-steam.json').write_text('{"Active":true}')
        result=self.run_preflight(False)
        self.assertFalse(result['ok']);self.assertFalse(result['needsElevation'])
        self.assertIn('world-state-steam.json',capture.active_world_records())

    def test_status_does_not_probe_dependencies(self):
        with patch.object(capture,'preflight',side_effect=AssertionError('heavy preflight')),patch.object(capture,'python_exe',side_effect=AssertionError('runtime probe')),patch.object(capture,'is_admin',return_value=True):
            result=capture.status()
        self.assertFalse(result['active'])

    def test_exploration_ipc_is_compact(self):
        (self.export/'exploration.txt').write_text('synthetic report')
        self.output({'exploration_report':'exploration.txt','successful_decode_count':7,'exploration':{
            'schema_version':1,'report_available':True,'evidence_available':True,'database_imported':False,
            'summary':'observed','maps':{'1':{'ids':list(range(10000))}},'evidence':[{'seq':1}]*2000}})
        result=capture.result_from_export(self.export,self.session)
        self.assertNotIn('maps',result['exploration']);self.assertNotIn('evidence',result['exploration'])
        self.assertLess(len(json.dumps(result)),2048)
        self.assertEqual(result['explorationReport'],str(self.export/'exploration.txt'))

    def test_exploration_report_cannot_escape_export(self):
        self.output({'exploration_report':'../outside.txt'})
        with self.assertRaisesRegex(ValueError,'探索报告'):capture.result_from_export(self.export,self.session)

    def test_unknown_worker_status_preserves_stop_signal(self):
        capture.write_state({'active':True,'phase':'capturing','session':str(self.session),'pid':123})
        with patch.object(capture,'powershell',return_value=Mock(returncode=5,stdout='')) as shell,patch.object(capture,'completed_result') as finished:
            with self.assertRaises(ValueError):capture.stop()
        finished.assert_not_called();self.assertTrue(capture.STOP_FLAG.exists())
        self.assertTrue(capture.read_state()['active']);self.assertEqual(capture.read_state()['phase'],'stopping')
        self.assertNotIn('Stop-Process',shell.call_args[0][0])

    def test_wait_timeout_never_force_kills_or_declares_complete(self):
        capture.write_state({'active':True,'phase':'capturing','session':str(self.session),'pid':123})
        with patch.object(capture,'powershell',return_value=Mock(returncode=0,stdout='alive')) as shell,patch.object(capture.time,'sleep'),patch.object(capture,'completed_result') as finished:
            with self.assertRaises(ValueError):capture.stop()
        finished.assert_not_called();self.assertTrue(capture.STOP_FLAG.exists())
        self.assertTrue(capture.read_state()['active'])
        self.assertTrue(all('Stop-Process' not in call.args[0] for call in shell.call_args_list))

    def test_managed_start_respects_closed_launcher(self):
        (self.runtime/'capture-launcher-exit.flag').write_text('stop')
        with patch.object(capture.subprocess,'Popen') as spawn:
            with self.assertRaises(ValueError):capture.start(None,launcher_managed=True)
        spawn.assert_not_called()

    def test_close_during_preflight_does_not_spawn_worker(self):
        def preflight():
            (self.runtime/'capture-launcher-exit.flag').write_text('stop')
            return {'ok':True}
        with patch.object(official_launcher,'validate_launcher',return_value=Path('official.exe')),patch.object(capture,'preflight',side_effect=preflight),patch.object(capture.subprocess,'Popen') as spawn:
            with self.assertRaises(ValueError):capture.start(Path('official.exe'),launcher_managed=True)
        spawn.assert_not_called()

if __name__=='__main__':unittest.main()
