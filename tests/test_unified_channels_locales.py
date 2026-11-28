"""Offline regressions for unified 5-language UI and Bilibili/TapTap/Steam launch & capture."""
from build.project_layout import source_file
from pathlib import Path
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(source_file(ROOT, 'backup-tool')))
import client_identity as identity
import taptap_private_runner as runner
import official_launcher as launcher
import official_capture as capture
import capture_checklist as checklist
import normalize_capture
import parse_pcap as decoder


def _pcap_block(kind, body):
    body += b'\0' * ((-len(body)) % 4)
    size = len(body) + 12
    return struct.pack('<II', kind, size) + body + struct.pack('<I', size)


def _pcapng(packets):
    out = _pcap_block(0x0a0d0d0a, struct.pack('<IHHq', 0x1a2b3c4d, 1, 0, -1))
    out += _pcap_block(1, struct.pack('<HHI', 1, 0, 65535))
    for pkt in packets:
        out += _pcap_block(6, struct.pack('<IIIII', 0, 0, 0, len(pkt), len(pkt)) + pkt)
    return out


def _ipv4_tcp(src='198.18.0.1', dst='150.5.131.114', sport=51938, dport=11001, payload=b''):
    total = 20 + 20 + len(payload)
    ip = struct.pack('>BBHHHBBH4s4s', 0x45, 0, total, 0, 0x4000, 128, 6, 0,
                     bytes(map(int, src.split('.'))), bytes(map(int, dst.split('.'))))
    tcp = struct.pack('>HHIIBBHHH', sport, dport, 1, 0, 0x50, 0x18, 65535, 0, 0)
    return ip + tcp + payload


class UnifiedChannelDetectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _client(self, name, files):
        folder = self.root / name
        for rel, content in files.items():
            target = folder / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        return folder

    def test_steam_quicksdk_distinguished_from_generic_quicksdk(self):
        steam = self._client('steam', {
            'launcher.exe': b'MZ-steam',
            'GameAssembly.dll': b'ga',
            'launcher_Data/app.info': b'info',
            'launcher_Data/Plugins/Win/PCSDK_release/PCSDKUI.exe': b'quick',
            'launcher_Data/Plugins/x86_64/steam_api64.dll': b'steam',
            'launcher_Data/il2cpp_data/Metadata/global-metadata.dat': b'hdr\x00QuickSDK_Oversea_steam\x00tail',
        })
        self.assertEqual(identity.classify(steam), 'steam-quicksdk')
        info = launcher.detect_channel(steam / 'launcher.exe')
        self.assertEqual(info['channel'], 'steam')
        self.assertEqual(info['identity'], 'steam-quicksdk')

    def test_bilibili_and_taptap_channels_detected_uniformly(self):
        bili = self._client('bili', {
            'launcher.exe': b'MZ-bili',
            'launcher_Data/Plugins/x86_64/PCGameSDK.dll': b'bili',
        })
        tap = self._client('tap', {
            'launcher.exe': b'MZ-tap',
            'launcher_Data/Plugins/Win/PCSDK_release/PCSDKUI.exe': b'tap',
        })
        self.assertEqual(identity.classify(bili), 'pcgamesdk')
        self.assertEqual(launcher.detect_channel(bili / 'launcher.exe')['channel'], 'bilibili')
        self.assertEqual(identity.classify(tap), 'quicksdk-pc')
        self.assertEqual(launcher.detect_channel(tap / 'launcher.exe')['channel'], 'taptap')

    def test_steam_private_runner_accepts_steam_quicksdk_and_sets_appid(self):
        client = self._client('profiles/solo/client', {
            'launcher.exe': b'MZ-steam',
            'GameAssembly.dll': b'synthetic-steam-assembly',
            'launcher_Data/app.info': b'info',
            'launcher_Data/Plugins/Win/PCSDK_release/PCSDKUI.exe': b'quick',
            'launcher_Data/Plugins/x86_64/steam_api64.dll': b'steam',
            'launcher_Data/il2cpp_data/Metadata/global-metadata.dat': b'QuickSDK_Oversea_steam\x00',
        })
        steam_hash = next(k for k, v in runner.SUPPORTED_BUILDS.items() if v == 'steam')
        with patch.object(runner.hashlib, 'sha256') as digest:
            digest.return_value.hexdigest.return_value = steam_hash
            cfg = runner.validated_config(client, '127.0.0.1', 18080, 1000000)
        self.assertEqual(cfg['channel'], 'steam-quicksdk')
        self.assertIs(cfg['isSteam'], True)
        self.assertIs(cfg['quietErrorOverlay'], True)
        with runner._steam_environment(True):
            self.assertEqual(os.environ.get('SteamAppId'), '3879660')
            self.assertEqual(os.environ.get('SteamGameId'), '3879660')

    def test_steam_direct_exe_capture_uses_any_tcp_when_no_static_ips(self):
        base = self._client('app', {'backup-tool/placeholder.txt': b'x'})
        steam_exe = self._client('steam-game', {
            'launcher.exe': b'MZ-steam',
            'launcher_Data/Plugins/Win/PCSDK_release/PCSDKUI.exe': b'quick',
            'launcher_Data/Plugins/x86_64/steam_api64.dll': b'steam',
            'launcher_Data/il2cpp_data/Metadata/global-metadata.dat': b'QuickSDK_Oversea_steam\x00',
        }) / 'launcher.exe'
        runtime = base / 'runtime'
        spawned_commands = []

        def fake_popen(command, **kwargs):
            spawned_commands.append(command)
            ready = Path(command[command.index('-ReadyPath') + 1])
            ready.write_text(json.dumps({'ready': True, 'pid': 77, 'session': str(ready.parent / 's')}))
            proc = Mock(pid=77)
            proc.poll.return_value = None
            return proc

        patches = [
            patch.object(capture, name, val) for name, val in {
                'BASE': base, 'RUNTIME': runtime, 'STATE': runtime / 'capture-state.json',
                'SESSIONS': runtime / 'capture-sessions', 'LOG': runtime / 'capture.log',
                'STOP_FLAG': runtime / 'capture-stop.flag',
            }.items()
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        with patch.object(capture, 'preflight', return_value={'ok': True, 'problems': []}), \
             patch.object(capture, 'python_exe', return_value=Path('py.exe')), \
             patch.object(capture, 'tshark_exe', return_value=Path('tshark.exe')), \
             patch.object(capture.subprocess, 'Popen', side_effect=fake_popen), \
             patch.object(launcher, 'launch_official', return_value=launcher.Launched(88, 'steam', steam_exe.parent)):
            result = capture.start(steam_exe)
        self.assertEqual(result['channel'], 'steam')
        self.assertEqual(result['officialKind'], 'steam')
        self.assertIn('-AnyTcp', spawned_commands[0])


class Patch041AdoptedFeaturesTests(unittest.TestCase):
    def test_mixed_ethernet_and_bare_ip_normalized_for_decode(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / 'mixed.pcapng'
            eth_frame = b'\xff' * 6 + b'\x11' * 6 + b'\x08\x00' + _ipv4_tcp()
            raw = _pcapng([eth_frame, _ipv4_tcp()])
            source.write_bytes(raw)
            normalized, count = normalize_capture.normalize_pcapng(source)
            self.assertEqual(count, 1)
            self.assertNotEqual(normalized, source)
            self.assertTrue(normalized.is_file())

    def test_tshark_input_handles_long_windows_paths(self):
        with tempfile.TemporaryDirectory() as d:
            deep = Path(d) / ('a_' * 60) / ('b_' * 60)
            deep.mkdir(parents=True)
            long_path = Path('\\\\?\\' + str((deep / 'capture.pcapng').resolve())) if os.name == 'nt' else deep / 'capture.pcapng'
            long_path.write_bytes(b'pcapng-payload')
            with decoder.tshark_input(long_path) as usable:
                if os.name == 'nt':
                    self.assertFalse(usable.startswith('\\\\?\\'))
                    self.assertTrue(Path(usable).is_file())
                    self.assertLess(len(usable), 240)
            if os.name == 'nt':
                self.assertFalse(Path(usable).exists())

    def test_fishing_and_gathering_life_evidence_observed(self):
        from collections import Counter
        counts, empty = Counter(), Counter()
        checklist.observe('FishingResultNotice', {'status': 1}, counts, empty)
        checklist.observe('GetLifeInfoRsp', {'status': 1, 'life_type': 6, 'life_base_info': {'level': 3}}, counts, empty)
        rep = {item['id']: item for item in checklist.report(counts, empty, readable=True)['items']}
        self.assertEqual(rep['fishing']['state'], 'collected')
        self.assertEqual(rep['gathering']['state'], 'collected')


class StringsParityTests(unittest.TestCase):
    def test_every_used_loc_key_is_translated_in_all_five_locales(self):
        src = ROOT / 'src/launcher'
        strings_text = (src / 'Strings.cs').read_text(encoding='utf-8-sig')
        defined = set(re.findall(r'Add\(d,\s*"((?:\\.|[^"\\])*)"', strings_text))
        used = set()
        for name in ('Launcher.cs', 'SeasideUI.cs', 'Friendly.cs', 'RoomManagerForm.cs'):
            used |= set(re.findall(r'Loc\.S\("((?:\\.|[^"\\])*)"\)', (src / name).read_text(encoding='utf-8-sig')))
        self.assertEqual(sorted(k for k in used if k not in defined), [])


class LiveRegressionFixesTests(unittest.TestCase):
    def test_dist_repo_dev_tools_fallback_finds_python_and_tshark(self):
        with tempfile.TemporaryDirectory() as d:
            fake_repo = Path(d) / 'repo'
            (fake_repo / 'src/runtime').mkdir(parents=True)
            (fake_repo / 'build').mkdir(parents=True)
            (fake_repo / 'build/project_layout.py').write_text('# stub\n', encoding='utf-8')
            tools = fake_repo / 'src/runtime/backup-tool/tools'
            py = tools / 'python/python.exe'
            tshark = tools / 'tshark/tshark.exe'
            py.parent.mkdir(parents=True)
            tshark.parent.mkdir(parents=True)
            py.write_bytes(b'MZ')
            tshark.write_bytes(b'MZ')
            dist_launcher = fake_repo / 'dist/launcher'
            dist_launcher.mkdir(parents=True)
            with patch.object(capture, 'BASE', dist_launcher), \
                 patch.object(capture, 'can_decode', side_effect=lambda p: p == py), \
                 patch.object(capture.shutil, 'which', return_value=None):
                self.assertEqual(capture.python_exe(), py)
                self.assertEqual(capture.tshark_exe(), tshark)

    def test_capture_and_official_import_preserve_database_and_backup_paths(self):
        import sqlite3
        import zipfile
        import capture_import
        import official_import
        from contextlib import closing
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'Users' / 'tester'
            profile = root / 'profiles/solo'
            (profile / 'db').mkdir(parents=True)
            database = profile / 'db/lolo.db'
            with closing(sqlite3.connect(database)) as conn, conn:
                conn.executescript(
                    'CREATE TABLE of_user(user_id INTEGER PRIMARY KEY, sdk_uid INTEGER UNIQUE, ban INTEGER);'
                    'CREATE TABLE of_game(user_id INTEGER PRIMARY KEY, created_at TEXT, updated_at TEXT, bin_data BLOB);'
                    'CREATE TABLE of_game_basic(user_id INTEGER PRIMARY KEY, nick_name TEXT, level INTEGER);'
                )
            capture_zip = root / 'account_12345.zip'
            main_rsp = {'status': 'StatusCode_OK', 'player_id': 12345, 'player_label': 12345,
                        'player_name': 'Tester', 'level': 10,
                        'characters': [{'character_id': 101001, 'level': 1}], 'team': {'char1': 101001}}
            with zipfile.ZipFile(capture_zip, 'w') as zf:
                zf.writestr('export/json/00001_s0_s2c_1006_PlayerMainDataRsp.json', json.dumps(main_rsp))
            app_dir = source_file(ROOT, 'capture_import.py').parent
            for script, argv in (
                ('capture_import.py', ['import', '--file', str(capture_zip), '--profile', str(profile), '--uid', '12345', '--allow-overwrite']),
                ('official_import.py', ['import', '--file', str(capture_zip), '--database', str(database), '--uid', '12345', '--allow-overwrite']),
            ):
                proc = subprocess.run(
                    [sys.executable, '-I', '-B', '-X', 'utf8', str(app_dir / script), *argv],
                    capture_output=True, text=True, encoding='utf-8', check=True,
                )
                payload = json.loads(proc.stdout)
                self.assertEqual(Path(payload['database']).resolve(), database.resolve())
                self.assertTrue(Path(payload['database']).is_file())
                self.assertTrue(Path(payload['backup']).is_file())

    def test_ip_geo_country_mapping_on_compiled_launchers(self):
        if os.name != 'nt':
            self.skipTest('Compiled C# contract requires Windows')
        harness = r'''
using System;
using System.Reflection;
class GeoCheck {
    static void Need(bool cond, string msg) { if (!cond) throw new Exception(msg); }
    static void Main(string[] args) {
        var asm = Assembly.LoadFrom(args[0]);
        var loc = asm.GetType("OverFieldLocal.Loc", true);
        var parse = loc.GetMethod("ParseGeoCountryCode", BindingFlags.Public | BindingFlags.Static);
        var map = loc.GetMethod("LocaleFromCountryCode", BindingFlags.Public | BindingFlags.Static);
        string initialLocale = (string)loc.GetField("InitialLocale").GetValue(null);
        Need((string)parse.Invoke(null, new object[]{" CN \n"}) == "CN", "plain text");
        Need((string)parse.Invoke(null, new object[]{"{\"ip\":\"1.2.3.4\",\"country\":\"jp\"}"}) == "JP", "json");
        Need((string)parse.Invoke(null, new object[]{"<html>gateway</html>"}) == "", "html");
        Need((string)map.Invoke(null, new object[]{"CN"}) == "zh-CN", "CN");
        Need((string)map.Invoke(null, new object[]{"TW"}) == "zh-TW", "TW");
        Need((string)map.Invoke(null, new object[]{"HK"}) == "zh-TW", "HK");
        Need((string)map.Invoke(null, new object[]{"MO"}) == "zh-TW", "MO");
        Need((string)map.Invoke(null, new object[]{"JP"}) == "ja", "JP");
        Need((string)map.Invoke(null, new object[]{"KR"}) == "ko", "KR");
        Need((string)map.Invoke(null, new object[]{"US"}) == "en", "US");
        Need((string)map.Invoke(null, new object[]{"XX"}) == initialLocale, "fallback");
        var supported = (string[])loc.GetField("SupportedLocales").GetValue(null);
        Need(supported.Length == 5, "all 5 locales present");
        Console.WriteLine("PASS");
    }
}
'''
        csc = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        with tempfile.TemporaryDirectory() as d:
            cs = Path(d) / 'GeoCheck.cs'
            exe = Path(d) / 'GeoCheck.exe'
            cs.write_text(harness, encoding='utf-8-sig')
            subprocess.run([str(csc), '/nologo', '/target:exe', '/out:' + str(exe), str(cs)],
                           capture_output=True, text=True, check=True)
            out = subprocess.run([str(exe), str(source_file(ROOT, 'OverFieldLauncher.exe'))],
                                 capture_output=True, text=True, check=True)
            self.assertIn('PASS', out.stdout)

    def test_direct_capture_progress_reader_on_compiled_launchers(self):
        if os.name != 'nt':
            self.skipTest('Compiled C# contract requires Windows')
        harness = r'''
using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
class ProgressCheck {
    static void Need(bool cond, string msg) { if (!cond) throw new Exception(msg); }
    static object CallTryRead(MethodInfo method, object backend, string prev,
                              out Dictionary<string, object> data, out string key,
                              out bool waiting, out bool unchanged) {
        object[] callArgs = new object[] { prev, null, null, false, false };
        bool ok = (bool)method.Invoke(backend, callArgs);
        data = callArgs[1] as Dictionary<string, object>;
        key = (string)callArgs[2];
        waiting = (bool)callArgs[3];
        unchanged = (bool)callArgs[4];
        return ok;
    }
    static void Main(string[] args) {
        var asm = Assembly.LoadFrom(args[0]);
        var backendType = asm.GetType("OverFieldLocal.Backend", true);
        var backend = Activator.CreateInstance(backendType, true);
        var baseDirField = backendType.GetField("BaseDir", BindingFlags.Public | BindingFlags.Instance);
        string tempRoot = args[1];
        baseDirField.SetValue(backend, tempRoot);
        var method = backendType.GetMethod("TryReadCaptureProgressDirect", BindingFlags.Public | BindingFlags.Instance);
        Dictionary<string, object> data; string key; bool waiting; bool unchanged;

        // 1. Missing capture-state.json -> unhandled
        Need(!(bool)CallTryRead(method, backend, "", out data, out key, out waiting, out unchanged), "missing state");

        // 2. Active capture before progress.json exists -> handled, waiting
        string runtime = Path.Combine(tempRoot, "runtime");
        string sessionCap = Path.Combine(runtime, "capture-sessions", "cap_1", "account_1");
        Directory.CreateDirectory(sessionCap);
        File.WriteAllText(Path.Combine(runtime, "capture-state.json"),
            "{\"active\":true,\"phase\":\"capturing\",\"capturePath\":\"" + sessionCap.Replace("\\", "\\\\") + "\"}");
        Need((bool)CallTryRead(method, backend, "", out data, out key, out waiting, out unchanged), "capturing wait ok");
        Need(waiting && data == null, "capturing wait flags");

        // 3. progress.json created -> parsed directly, then second read reports unchanged
        string liveDir = Path.Combine(sessionCap, "live-progress");
        Directory.CreateDirectory(liveDir);
        File.WriteAllText(Path.Combine(liveDir, "progress.json"),
            "{\"phase\":\"capturing\",\"readable\":true,\"items\":[{\"id\":\"login\",\"state\":\"collected\"}]}");
        Need((bool)CallTryRead(method, backend, "", out data, out key, out waiting, out unchanged), "live ok");
        Need(!waiting && !unchanged && data != null && !String.IsNullOrEmpty(key), "live data");
        Need((bool)CallTryRead(method, backend, key, out data, out key, out waiting, out unchanged), "live cached ok");
        Need(unchanged && data == null, "live unchanged");

        // 4. Complete session reads export/capture-checklist.json directly
        string exportDir = Path.Combine(sessionCap, "export");
        Directory.CreateDirectory(exportDir);
        File.WriteAllText(Path.Combine(exportDir, "capture-checklist.json"),
            "{\"phase\":\"complete\",\"readable\":true,\"items\":[{\"id\":\"login\",\"state\":\"collected\"}]}");
        File.WriteAllText(Path.Combine(runtime, "capture-state.json"),
            "{\"active\":false,\"phase\":\"complete\",\"export\":\"" + exportDir.Replace("\\", "\\\\") + "\"}");
        Need((bool)CallTryRead(method, backend, key, out data, out key, out waiting, out unchanged), "complete ok");
        Need(!waiting && !unchanged && data != null && Convert.ToString(data["phase"]) == "complete", "complete data");

        // 5. Path outside runtime/capture-sessions is rejected
        string outside = Path.Combine(tempRoot, "outside");
        Directory.CreateDirectory(Path.Combine(outside, "live-progress"));
        File.WriteAllText(Path.Combine(outside, "live-progress", "progress.json"), "{\"items\":[]}");
        File.WriteAllText(Path.Combine(runtime, "capture-state.json"),
            "{\"active\":true,\"phase\":\"capturing\",\"capturePath\":\"" + outside.Replace("\\", "\\\\") + "\"}");
        Need(!(bool)CallTryRead(method, backend, "", out data, out key, out waiting, out unchanged), "reject outside");

        Console.WriteLine("PASS");
    }
}
'''
        csc = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        with tempfile.TemporaryDirectory() as d:
            cs = Path(d) / 'ProgressCheck.cs'
            exe = Path(d) / 'ProgressCheck.exe'
            cs.write_text(harness, encoding='utf-8-sig')
            subprocess.run([str(csc), '/nologo', '/target:exe', '/out:' + str(exe), str(cs)],
                           capture_output=True, text=True, check=True)
            work = Path(d) / 'work'
            work.mkdir()
            out = subprocess.run([str(exe), str(source_file(ROOT, 'OverFieldLauncher.exe')), str(work)],
                                 capture_output=True, text=True, check=True)
            self.assertIn('PASS', out.stdout)

    def test_environment_compatibility_helpers(self):
        import capture_import
        import official_capture
        import preflight

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            fake_tshark = root / 'tshark.exe'
            fake_tshark.write_bytes(b'MZ')
            with patch.dict(os.environ, {'OVERFIELD_TSHARK': str(fake_tshark)}):
                self.assertEqual(official_capture.tshark_exe(), fake_tshark)

            ps7_mod = r'C:\Program Files\PowerShell\7\Modules;C:\Windows\System32\WindowsPowerShell\v1.0\Modules'
            with patch.dict(os.environ, {'PSModulePath': ps7_mod}):
                sanitized_env = official_capture._sanitized_powershell_env()
                cleaned = next((v for k, v in sanitized_env.items() if k.lower() == 'psmodulepath'), '')
                self.assertNotIn('PowerShell\\7', cleaned)
                self.assertIn('WindowsPowerShell\\v1.0\\Modules', cleaned)

            src = root / 'tmp.db'
            dst = root / 'dest.db'
            src.write_bytes(b'sqlite-bytes')
            with patch.object(capture_import.os, 'link', side_effect=OSError(1, 'exFAT unsupported')):
                capture_import._publish_temp_file(src, dst)
            self.assertEqual(dst.read_bytes(), b'sqlite-bytes')
            self.assertFalse(src.exists())

            client = root / 'steam_client'
            for rel, content in {
                'launcher.exe': b'MZ', 'GameAssembly.dll': b'ga', 'launcher_Data/app.info': b'info',
                'launcher_Data/Plugins/Win/PCSDK_release/PCSDKUI.exe': b'q',
                'launcher_Data/Plugins/x86_64/steam_api64.dll': b's',
                'launcher_Data/il2cpp_data/Metadata/global-metadata.dat': b'QuickSDK_Oversea_steam',
            }.items():
                target = client / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            with patch.object(preflight.importlib.util, 'find_spec', side_effect=ModuleNotFoundError('frida')):
                report = preflight.check(client, mode='solo')
            self.assertFalse(any('Unsupported or unverified game channel' in m for m in report['missing']))


if __name__ == '__main__':
    unittest.main()
