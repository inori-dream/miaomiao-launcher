"""Offline tests: official path isolation, visible launch and capture-before-launch order."""
import json, os, subprocess, tempfile, unittest
from pathlib import Path
from unittest.mock import Mock, patch
import official_launcher as launcher
import official_capture as capture


class OfficialLauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.base=self.root/'app';self.base.mkdir()
        self.exe=self.root/'official-game'/'launcher.exe';self.exe.parent.mkdir();self.exe.write_bytes(b'MZ-synthetic')
    def test_empty_and_relative_paths_are_rejected(self):
        for value in (None,'','launcher.exe'):
            with self.assertRaises(ValueError):launcher.validate_launcher(value,self.base)
    def test_private_profiles_are_rejected(self):
        exe=self.base/'profiles/solo/client/launcher.exe';exe.parent.mkdir(parents=True);exe.write_bytes(b'MZ')
        with self.assertRaises(ValueError):launcher.validate_launcher(exe,self.base)
    def test_private_marker_outside_current_base_is_rejected(self):
        (self.exe.parent/'client-ready.json').write_text('{}')
        with self.assertRaises(ValueError):launcher.validate_launcher(self.exe,self.base)
    def test_local_sdk_replacement_is_rejected(self):
        sdk=self.exe.parent/'launcher_Data/Plugins/x86_64/PCGameSDK.dll';sdk.parent.mkdir(parents=True)
        sdk.write_bytes(b'local-sdk');(self.base/'LocalUidSDK.dll').write_bytes(b'local-sdk')
        with self.assertRaises(ValueError):launcher.validate_launcher(self.exe,self.base)
    def test_private_launcher_name_is_rejected(self):
        exe=self.exe.parent/'OverFieldLauncher.exe';exe.write_bytes(b'MZ')
        with self.assertRaises(ValueError):launcher.validate_launcher(exe,self.base)
    def test_public_path_is_accepted(self):
        self.assertEqual(launcher.validate_launcher(self.exe,self.base),self.exe.resolve())
    def test_visible_launch_has_no_private_args_or_environment(self):
        with patch.dict(os.environ,{'LOLO_SDK_UID':'123','lolo_dispatch_host':'private','PATH':'normal'}), patch.object(launcher.subprocess,'Popen') as spawn:
            launched=launcher.launch_official(self.exe,self.base)
        args,kwargs=spawn.call_args
        self.assertEqual(args[0],[str(self.exe.resolve())]);self.assertEqual(kwargs['creationflags'],0)
        self.assertEqual(kwargs['cwd'],str(self.exe.parent))
        self.assertFalse(any(k.upper().startswith('LOLO_') for k in kwargs['env']))
        self.assertEqual(kwargs['env']['PATH'],'normal')
        self.assertEqual(launched.kind,'exe')


class SteamShortcutTests(unittest.TestCase):
    """A Steam .url shortcut is an accepted official channel, launched by protocol."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.base=self.root/'app';self.base.mkdir()
        self.shortcut=self.root/'OverField.url'
        self.shortcut.write_text('[InternetShortcut]\nURL=steam://rungameid/3879660\nIconIndex=0\n',encoding='utf-8')
        self.install=self.root/'steamapps/common/OverField';self.install.mkdir(parents=True)
        (self.install/'launcher.exe').write_bytes(b'MZ-steam')
    def test_steam_shortcut_is_accepted(self):
        with patch.object(launcher,'steam_install_dir',return_value=self.install):
            self.assertEqual(launcher.validate_launcher(self.shortcut,self.base),self.shortcut.resolve())
    def test_app_id_is_read_from_shortcut(self):
        self.assertEqual(launcher.steam_app_id(self.shortcut),'3879660')
    def test_non_steam_shortcut_is_rejected(self):
        other=self.root/'Other.url';other.write_text('[InternetShortcut]\nURL=https://example.com\n',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'Steam'):
            launcher.steam_app_id(other)
    def test_shortcut_without_url_is_rejected(self):
        other=self.root/'Empty.url';other.write_text('[InternetShortcut]\n',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'URL'):
            launcher.steam_app_id(other)
    def test_unknown_suffix_is_rejected(self):
        other=self.root/'notes.txt';other.write_text('hello',encoding='utf-8')
        with self.assertRaises(ValueError):
            launcher.validate_launcher(other,self.base)
    def test_steam_shortcut_inside_private_profiles_is_rejected(self):
        private=self.base/'profiles/solo/OverField.url';private.parent.mkdir(parents=True)
        private.write_text('[InternetShortcut]\nURL=steam://rungameid/3879660\n',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'profiles'):
            launcher.validate_launcher(private,self.base)
    def test_steam_install_with_private_marker_is_rejected(self):
        (self.install/'client-ready.json').write_text('{}')
        with patch.object(launcher,'steam_install_dir',return_value=self.install):
            with self.assertRaisesRegex(ValueError,'私服客户端副本'):
                launcher.validate_launcher(self.shortcut,self.base)
    def test_launch_uses_protocol_handler_and_returns_pid(self):
        with patch.object(launcher,'steam_install_dir',return_value=self.install), \
             patch.object(launcher.os,'startfile') as startfile, \
             patch.object(launcher,'_await_steam_game',return_value=4242) as await_game:
            launched=launcher.launch_official(self.shortcut,self.base)
        startfile.assert_called_once_with('steam://rungameid/3879660')
        await_game.assert_called_once_with(self.install)
        self.assertEqual((launched.pid,launched.kind),(4242,'steam'))
    def test_steam_launch_never_spawns_a_private_process(self):
        with patch.object(launcher,'steam_install_dir',return_value=self.install), \
             patch.object(launcher.os,'startfile'), \
             patch.object(launcher,'_await_steam_game',return_value=0), \
             patch.dict(os.environ,{'LOLO_SDK_UID':'9'}), \
             patch.object(launcher.subprocess,'Popen') as spawn:
            launcher.launch_official(self.shortcut,self.base)
        spawn.assert_not_called()
    def test_install_dir_is_none_when_manifest_missing(self):
        with patch.object(launcher,'_steam_roots',return_value=[self.root]):
            self.assertIsNone(launcher.steam_install_dir('999999'))
    def test_install_dir_is_read_from_manifest(self):
        apps=self.root/'steamapps';apps.mkdir(exist_ok=True)
        (apps/'appmanifest_3879660.acf').write_text('"AppState"\n{\n\t"installdir"\t\t"OverField"\n}\n',encoding='utf-8')
        with patch.object(launcher,'_steam_roots',return_value=[self.root]):
            self.assertEqual(launcher.steam_install_dir('3879660'),self.install)
    def write_config(self,text):
        config=self.install/'launcher_Data/StreamingAssets/Config.txt';config.parent.mkdir(parents=True,exist_ok=True)
        config.write_text(text,encoding='utf-8')
    def test_server_address_is_read_for_runtime_port_channels(self):
        self.write_config('[服务器配置]\nUseCustomConfig = FALSE\nLocalServerIP = 192.168.0.123\nRemoteServerIP = 139.196.33.142\n')
        self.assertEqual(launcher.server_addresses(self.install),['139.196.33.142'])
    def test_private_addresses_are_not_used_as_capture_targets(self):
        self.write_config('LocalServerIP = 192.168.0.123\nRemoteServerIP = 127.0.0.1\n')
        self.assertEqual(launcher.server_addresses(self.install),[])
    def test_missing_config_yields_no_addresses(self):
        self.assertEqual(launcher.server_addresses(self.install),[])
    def test_missing_install_yields_no_addresses(self):
        self.assertEqual(launcher.server_addresses(None),[])


class CaptureStartOrderTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);self.base=root/'app';self.base.mkdir();(self.base/'backup-tool').mkdir()
        self.exe=root/'original/launcher.exe';self.exe.parent.mkdir();self.exe.write_bytes(b'MZ-synthetic')
        self.runtime=self.base/'runtime';self.events=[]
        for name,value in {'BASE':self.base,'RUNTIME':self.runtime,'STATE':self.runtime/'capture-state.json',
                           'SESSIONS':self.runtime/'capture-sessions','LOG':self.runtime/'capture.log',
                           'STOP_FLAG':self.runtime/'capture-stop.flag'}.items():
            p=patch.object(capture,name,value);p.start();self.addCleanup(p.stop)
        self.proc=Mock(pid=42);self.proc.poll.return_value=None
        def finish(timeout):self.proc.poll.return_value=0
        self.proc.wait.side_effect=finish
    def spawn_capture(self,command,**kwargs):
        self.events.append('capture')
        ready=Path(command[command.index('-ReadyPath')+1])
        ready.write_text(json.dumps({'ready':True,'pid':42,'session':str(ready.parent/'account')}))
        self.assertEqual(kwargs['stdin'],subprocess.DEVNULL)
        self.assertFalse(any(k.upper().startswith('LOLO_') for k in kwargs['env']))
        return self.proc
    def spawn_public(self,value,base):
        self.assertTrue(capture.read_state()['active']);self.events.append('official')
        return launcher.Launched(99,'exe',None)
    def contexts(self):
        return (patch.object(capture,'preflight',return_value={'ok':True,'problems':[]}),
                patch.object(capture,'python_exe',return_value=Path('decode-python.exe')),
                patch.object(capture,'tshark_exe',return_value=Path('tshark.exe')))
    def test_capture_ready_precedes_public_launch(self):
        a,b,c=self.contexts()
        with a,b,c,patch.object(capture.subprocess,'Popen',side_effect=self.spawn_capture),patch.object(launcher,'launch_official',side_effect=self.spawn_public):
            result=capture.start(self.exe)
        self.assertEqual(self.events,['capture','official']);self.assertEqual(result['officialPid'],99)
        self.assertEqual(capture.read_state()['officialLauncherPath'],str(self.exe.resolve()))
    def test_capture_failure_never_launches_game(self):
        a,b,c=self.contexts();self.proc.poll.return_value=3
        with a,b,c,patch.object(capture.subprocess,'Popen',return_value=self.proc),patch.object(launcher,'launch_official') as game:
            with self.assertRaises(ValueError):capture.start(self.exe)
        game.assert_not_called();self.assertFalse(capture.read_state()['active'])
    def test_public_spawn_failure_requests_capture_cleanup(self):
        a,b,c=self.contexts()
        with a,b,c,patch.object(capture.subprocess,'Popen',side_effect=self.spawn_capture),patch.object(launcher,'launch_official',side_effect=OSError('synthetic launch failure')):
            with self.assertRaises(OSError):capture.start(self.exe)
        self.assertTrue(capture.STOP_FLAG.is_file());self.assertFalse(capture.read_state()['active'])
        self.proc.kill.assert_not_called()
    def test_missing_official_path_does_not_fall_back_or_capture(self):
        with patch.object(capture.subprocess,'Popen') as spawn:
            with self.assertRaises(ValueError):capture.start(None)
        spawn.assert_not_called()
    def test_active_private_world_blocks_capture(self):
        self.runtime.mkdir();(self.runtime/'world-state.json').write_text(json.dumps({'Active':True}))
        with patch.object(capture.subprocess,'Popen') as spawn:
            with self.assertRaises(ValueError):capture.start(self.exe)
        spawn.assert_not_called()
    def test_wait_timeout_is_explicit(self):
        with self.assertRaises(ValueError):capture.wait_capture_ready(self.proc,self.runtime/'no-ready.json',timeout=0)
    def test_failed_worker_reports_readable_first_error(self):
        self.runtime.mkdir()
        (self.runtime/'capture-worker.stderr.log').write_text('拒绝访问。需要管理员权限。\nsynthetic stack',encoding='utf-8')
        self.proc.poll.return_value=5
        with self.assertRaisesRegex(ValueError,'拒绝访问'):
            capture.wait_capture_ready(self.proc,self.runtime/'capture-ready.json')


if __name__=='__main__':unittest.main()
