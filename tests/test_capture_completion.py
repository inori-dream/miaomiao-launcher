"""Synthetic regressions for completed output reuse and recovery; no real capture."""
import json, tempfile, unittest
from pathlib import Path
from unittest.mock import Mock,patch
import official_capture as capture
from test_official_import import make_zip,main_data
import sys
# SIBLING_TESTS: this suite imports sibling test modules by name.
sys.path.insert(0, str(Path(__file__).resolve().parent))

class CaptureCompletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.runtime=self.root/'runtime';self.sessions=self.runtime/'capture-sessions'
        self.session=self.sessions/'capture-test';self.account=self.session/'account_20200101_120000_001'
        self.account.mkdir(parents=True)
        for name,value in {'RUNTIME':self.runtime,'STATE':self.runtime/'capture-state.json','SESSIONS':self.sessions,
                           'STOP_FLAG':self.runtime/'capture-stop.flag','LOG':self.runtime/'capture.log'}.items():
            p=patch.object(capture,name,value);p.start();self.addCleanup(p.stop)
    def output(self,folder=None):
        folder=folder or self.account/'export';folder.mkdir(parents=True,exist_ok=True)
        source=make_zip(folder/'account_Test_777.zip',main_data(777,label=888))
        (folder/'summary.json').write_text(json.dumps({'total_packets':1,'player_uids':[777],'account_zip':source.name}),encoding='utf-8')
        return source
    def state(self,active=True):
        capture.write_state({'active':active,'phase':'decoding','session':str(self.session),'pid':0})
    def test_worker_output_is_not_decoded_twice(self):
        source=self.output();self.state()
        with patch.object(capture.subprocess,'run') as run:
            result=capture.stop()
        run.assert_not_called();self.assertEqual(result['accountZip'],str(source))
        self.assertEqual(capture.read_state()['phase'],'complete')
    def test_inactive_failed_completion_is_recoverable(self):
        source=self.output();self.state(False)
        with patch.object(capture,'powershell') as shell,patch.object(capture.subprocess,'run') as run:
            result=capture.stop()
        shell.assert_not_called();run.assert_not_called()
        self.assertEqual(result['accountZip'],str(source));self.assertTrue(capture.read_state()['recovered'])
    def test_recovery_never_stops_an_active_capture(self):
        self.output();self.state(True)
        with patch.object(capture,'powershell') as shell:
            with self.assertRaises(ValueError):capture.recover()
        shell.assert_not_called();self.assertTrue(capture.read_state()['active'])
    def test_repeated_completion_preserves_original(self):
        source=self.output();old=source.read_bytes();self.state(False)
        first=capture.stop();second=capture.stop()
        self.assertEqual(first['accountZip'],second['accountZip']);self.assertEqual(source.read_bytes(),old)
    def test_internal_and_display_uid_difference_is_allowed(self):
        self.output();self.state(False)
        result=capture.stop();self.assertEqual(result['playerUids'],[777])
    def test_partial_export_uses_fresh_folder(self):
        old=self.account/'export';old.mkdir();marker=old/'partial.txt';marker.write_text('keep')
        (self.account/'capture.etl').write_bytes(b'fixture');(self.account/'capture.pcapng').write_bytes(b'fixture')
        self.state();used=[]
        def decode(args,**kwargs):
            dest=Path(args[args.index('--out')+1]);used.append(dest);self.output(dest)
            return Mock(returncode=0,stderr='',stdout='')
        with patch.object(capture.subprocess,'run',side_effect=decode),patch.object(capture,'python_exe',return_value=Path('python.exe')):
            result=capture.stop()
        self.assertNotEqual(used[0],old);self.assertEqual(marker.read_text(),'keep')
        self.assertTrue(Path(result['accountZip']).is_file())
    def test_capture_gap_is_reported_without_blocking_zip_delivery(self):
        source=self.output()
        summary=source.with_name('summary.json')
        metadata=json.loads(summary.read_text(encoding='utf-8'))
        metadata['incomplete_streams']=[{'stream_id':0,'direction':'s2c','tcp_gap':True,'trailing_bytes':0}]
        summary.write_text(json.dumps(metadata,ensure_ascii=False),encoding='utf-8')
        (summary.parent/'json').mkdir()
        self.state(False)
        result=capture.stop()
        self.assertEqual(result['accountZip'],str(source));self.assertEqual(result['incompleteStreams'],1)
        report=json.loads(summary.with_name('capture-checklist.json').read_text(encoding='utf-8'))
        self.assertIn('备份可能不完整',report['warning']);self.assertIn('仍可保存',report['warning'])
        self.assertEqual(capture.status()['incompleteStreams'],1)
    def test_missing_incomplete_stream_field_is_zero(self):
        self.output();self.state(False)
        result=capture.stop()
        self.assertEqual(result['incompleteStreams'],0)
        self.assertNotIn('备份可能不完整',capture.read_state().get('error',''))
    def test_unsafe_account_zip_path_is_rejected(self):
        export=self.account/'export';export.mkdir()
        (export/'summary.json').write_text(json.dumps({'total_packets':1,'player_uids':[777],'account_zip':'../outside.zip'}),encoding='utf-8')
        self.state(False)
        with self.assertRaises(ValueError):capture.stop()
    def test_no_account_summary_does_not_make_fake_zip(self):
        export=self.account/'export';export.mkdir()
        (export/'summary.json').write_text(json.dumps({'total_packets':0,'player_uids':[]}),encoding='utf-8')
        self.state(False);result=capture.stop()
        self.assertEqual(result['accountZip'],'');self.assertEqual(list(export.glob('*.zip')),[])

if __name__=='__main__':unittest.main()
