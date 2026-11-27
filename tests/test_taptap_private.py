"""Offline tests for private-client identity and adapter boundaries."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import client_identity as identity
import taptap_private_runner as runner


class PrivateBoundaries(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.source=self.root/'official';self.source.mkdir()
        (self.source/'launcher.exe').write_bytes(b'client-a')
        (self.source/'GameAssembly.dll').write_bytes(b'assembly-a')
    def test_fingerprint_tracks_source_changes(self):
        first=identity.fingerprint(self.source)
        (self.source/'GameAssembly.dll').write_bytes(b'assembly-b')
        self.assertNotEqual(first['identity'],identity.fingerprint(self.source)['identity'])
    def test_legacy_ready_marker_is_not_a_valid_cache(self):
        marker=self.root/'client-ready.json';marker.write_text(json.dumps({'source':str(self.source),'localUidSdk':True}))
        self.assertFalse(identity.cache_matches(self.source,marker))
    def test_source_and_shared_destination_are_rejected(self):
        with self.assertRaises(ValueError):identity.assert_private_destination(self.source,self.source/'launcher_Data'/'Config.txt')
    def test_sdk_presence_is_not_channel_proof(self):
        plugins=self.source/'launcher_Data/Plugins/x86_64';plugins.mkdir(parents=True)
        (plugins/'tapsdkcore.dll').write_bytes(b'tap')
        quick=self.source/'launcher_Data/Plugins/Win/PCSDK_release';quick.mkdir(parents=True)
        (quick/'PCSDKUI.exe').write_bytes(b'quick')
        self.assertEqual(identity.classify(self.source),'quicksdk-pc')
    def test_original_client_cannot_be_spawned_by_adapter(self):
        with self.assertRaises(ValueError):runner.validated_config(self.source,'127.0.0.1',18080,1000000)
    def test_public_target_and_unverified_version_are_rejected(self):
        client=self.root/'profiles/solo/client';client.mkdir(parents=True)
        with patch.object(identity,'classify',return_value='quicksdk-pc'):
            with self.assertRaises(ValueError):runner.validated_config(client,'8.8.8.8',18080,1000000)
            (client/'GameAssembly.dll').write_bytes(b'unknown')
            with self.assertRaisesRegex(ValueError,'Unverified'):runner.validated_config(client,'127.0.0.1',18080,1000000)
    def test_local_token_only_in_test_agent(self):
        source=runner.agent_source({'uid':1000000,'host':'127.0.0.1','port':18080})
        self.assertNotIn('__LOCAL_CONFIGURATION__',source)
        self.assertIn('local-session',source)
        self.assertIn('official-token-check-disabled',source)


class SteamOverlayPolicyTests(unittest.TestCase):
    def configuration(self, build):
        with tempfile.TemporaryDirectory() as directory:
            client=Path(directory)/'profiles/steam/client'
            client.mkdir(parents=True)
            (client/'GameAssembly.dll').write_bytes(b'synthetic-build')
            with patch.object(identity,'classify',return_value='quicksdk-pc'), patch.object(runner.hashlib,'sha256') as digest:
                digest.return_value.hexdigest.return_value=build
                return runner.validated_config(client,'127.0.0.1',20080,1000000)
    def test_pinned_steam_build_enables_only_overlay_policy(self):
        build=next(key for key,kind in runner.SUPPORTED_BUILDS.items() if kind=='steam')
        self.assertIs(self.configuration(build)['quietErrorOverlay'],True)
    def test_non_steam_adapter_keeps_previous_policy(self):
        self.assertIs(self.configuration(runner.SUPPORTED_GAME_ASSEMBLY)['quietErrorOverlay'],False)
    def test_agent_does_not_replace_game_logging(self):
        source=runner.agent_source({'quietErrorOverlay':True,'uid':1000000,'host':'127.0.0.1','port':20080})
        self.assertIn("cfg.quietErrorOverlay === true",source)
        self.assertIn("utf8('open_error_log')",source)
        self.assertIn('error-overlay-policy-installed',source)
        self.assertEqual(source.count('Interceptor.replace('),3)
        self.assertNotIn('logMessageReceived',source)

if __name__=='__main__':unittest.main()
