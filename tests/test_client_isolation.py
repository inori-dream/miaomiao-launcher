"""Real-filesystem regression tests for private-client isolation and cache integrity."""
from build.project_layout import source_file
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import client_identity as identity
import preflight
import profile_manager as manager
import repair_version
import taptap_private_runner as runner


class ClientIsolationTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'official'
        self.base = self.root / 'profiles'
        self.target = self.base / 'solo/client'
        self.make_source()

    def make_source(self):
        files = {
            'launcher.exe': b'launcher-original',
            'GameAssembly.dll': b'assembly-original',
            'UnityPlayer.dll': b'unity-original',
            'launcher_Data/app.info': b'Inutan\nOverField\n',
            'launcher_Data/Boot.config': b'boot-config',
            'launcher_Data/Plugins/x86_64/ordinary.dll': b'ordinary-original',
            'launcher_Data/Plugins/Win/PCSDK_release/PCSDKUI.exe': b'quick-sdk',
            'launcher_Data/StreamingAssets/Config.txt': b'config-original',
            'launcher_Data/StreamingAssets/yoo/DefaultPackage/PackageManifest_DefaultPackage.version':
                b'2026-10-21-14-05-50',
            'launcher_Data/yoo/DefaultPackage/ManifestFiles/PackageManifest_DefaultPackage.version':
                b'2026-11-07-22-39-35',
        }
        for relative, content in files.items():
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return files

    def prepare(self):
        with patch.object(manager.sys, 'version_info', (3, 12, 0)):
            return manager.prepare_client(self.source, self.base, 'solo')

    def marker(self):
        return self.target / 'client-ready.json'

    def snapshot(self, root):
        return {str(path.relative_to(root)): path.read_bytes()
                for path in root.rglob('*') if path.is_file() and not identity.is_reparse_point(path)}

    def assert_no_staging(self):
        self.assertEqual(list((self.base / 'solo').glob('.client-staging-*')), [])
        self.assertFalse((self.base / 'solo/client-swap.json').exists())

    def junction(self, destination, source):
        if os.name != 'nt':
            self.skipTest('Windows Junction tests require Windows.')
        result = subprocess.run([
            'powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
            str(source_file(REPO, 'make_junction.ps1')), '-Destination', str(destination), '-Source', str(source),
        ], capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode or not destination.is_dir() or destination.resolve() != source.resolve():
            self.skipTest('Junction creation unavailable: ' + (result.stderr.strip() or result.stdout.strip()))

    def require_junctions(self):
        probe = self.root / 'junction-probe'
        self.junction(probe, self.source)
        probe.rmdir()

class ClientIsolationTests(ClientIsolationTestCase):
    def test_source_change_rebuilds_and_never_mutates_original(self):
        before = self.snapshot(self.source)
        self.prepare()
        self.assertEqual(self.snapshot(self.source), before)
        old_marker = self.marker().read_bytes()
        (self.source / 'launcher_Data/Plugins/x86_64/ordinary.dll').write_bytes(b'ordinary-updated')
        self.assertFalse(identity.cache_matches(self.source, self.marker()))
        self.prepare()
        self.assertEqual((self.target / 'launcher_Data/Plugins/x86_64/ordinary.dll').read_bytes(),
                         b'ordinary-updated')
        self.assertNotEqual(self.marker().read_bytes(), old_marker)
        self.assertTrue(identity.cache_matches(self.source, self.marker()))
        self.assertEqual((self.source / 'launcher_Data/Plugins/x86_64/ordinary.dll').read_bytes(),
                         b'ordinary-updated')
        self.assertEqual((self.source / 'launcher_Data/Plugins/Win/PCSDK_release/PCSDKUI.exe').read_bytes(),
                         b'quick-sdk')
        self.assertTrue(list((self.base / 'solo/backups').iterdir()))

    def test_missing_copy_file_invalidates_cache_and_is_restored(self):
        self.prepare()
        (self.target / 'UnityPlayer.dll').unlink()
        self.assertFalse(identity.cache_matches(self.source, self.marker()))
        self.prepare()
        self.assertEqual((self.target / 'UnityPlayer.dll').read_bytes(), b'unity-original')
        self.assertTrue(identity.cache_matches(self.source, self.marker()))

    def test_hash_tampering_invalidates_cache_and_is_repaired(self):
        self.prepare()
        (self.target / 'GameAssembly.dll').write_bytes(b'poisoned-assembly')
        self.assertFalse(identity.cache_matches(self.source, self.marker()))
        self.prepare()
        self.assertEqual((self.target / 'GameAssembly.dll').read_bytes(), b'assembly-original')

    def test_old_ready_marker_is_rejected(self):
        self.prepare()
        self.marker().write_text(json.dumps({
            'source': str(self.source), 'fingerprint': identity.fingerprint(self.source)['identity'],
            'files': identity.fingerprint(self.source)['files'], 'localUidSdk': False,
        }), encoding='utf-8')
        self.assertFalse(identity.cache_matches(self.source, self.marker()))
        self.prepare()
        self.assertEqual(json.loads(self.marker().read_text(encoding='utf-8'))['schema'],
                         identity.MARKER_SCHEMA)

    def test_marker_cannot_reuse_another_installation(self):
        self.prepare()
        other = self.root / 'other-install'
        shutil.copytree(self.source, other)
        recorded = json.loads(self.marker().read_text(encoding='utf-8'))
        recorded['source'] = str(other.resolve())
        recorded['fingerprint'] = identity.fingerprint(other)['identity']
        self.marker().write_text(json.dumps(recorded), encoding='utf-8')
        self.assertFalse(identity.cache_matches(self.source, self.marker()))
        self.prepare()
        self.assertEqual(json.loads(self.marker().read_text(encoding='utf-8'))['source'],
                         str(self.source.resolve()))

    def test_extra_immutable_copy_file_is_cache_poisoning(self):
        self.prepare()
        poison = self.target / 'launcher_Data/Plugins/x86_64/injected.dll'
        poison.write_bytes(b'poison')
        self.assertFalse(identity.cache_matches(self.source, self.marker()))
        self.prepare()
        self.assertFalse(poison.exists())

    def test_known_runtime_cache_is_purged_without_touching_source(self):
        cache = self.source / 'launcher_Data/Cache'
        cache.mkdir()
        (cache / 'source-session').write_bytes(b'private-source-cache')
        self.prepare()
        copied_cache = self.target / 'launcher_Data/Cache'
        self.assertTrue(copied_cache.is_dir())
        self.assertEqual(list(copied_cache.iterdir()), [])
        (copied_cache / 'poison.dll').write_bytes(b'poison')
        self.assertTrue(identity.cache_matches(self.source, self.marker()))
        self.prepare()
        self.assertFalse((copied_cache / 'poison.dll').exists())
        self.assertEqual((cache / 'source-session').read_bytes(), b'private-source-cache')

    def test_unknown_root_runtime_directory_is_private_and_not_imported(self):
        runtime = self.source / 'UnknownWritable'
        runtime.mkdir()
        (runtime / 'save.dat').write_bytes(b'private')
        self.prepare()
        private_runtime = self.target / 'UnknownWritable'
        self.assertTrue(private_runtime.is_dir())
        self.assertFalse(identity.is_junction(private_runtime))
        self.assertEqual(list(private_runtime.iterdir()), [])
        (private_runtime / 'injected.dll').write_bytes(b'poison')
        self.assertFalse(identity.cache_matches(self.source, self.marker()))
        self.prepare()
        self.assertEqual(list(private_runtime.iterdir()), [])
        self.assertEqual((runtime / 'save.dat').read_bytes(), b'private')

    def test_staging_failure_preserves_previous_client(self):
        self.prepare()
        sentinel = self.target / 'sentinel'
        sentinel.write_text('keep', encoding='utf-8')
        (self.source / 'UnityPlayer.dll').write_bytes(b'updated-unity')

        def fail_copy(source, destination):
            if Path(source).name == 'UnityPlayer.dll':
                raise OSError('synthetic staging failure')
            return real_copy(source, destination)

        real_copy = manager._copy_file
        with patch.object(manager, '_copy_file', side_effect=fail_copy):
            with self.assertRaisesRegex(OSError, 'staging failure'):
                self.prepare()
        self.assertEqual(sentinel.read_text(encoding='utf-8'), 'keep')
        self.assertEqual((self.target / 'UnityPlayer.dll').read_bytes(), b'unity-original')
        self.assert_no_staging()

    def test_publication_failure_restores_previous_client(self):
        self.prepare()
        sentinel = self.target / 'sentinel'
        sentinel.write_text('keep', encoding='utf-8')
        (self.source / 'UnityPlayer.dll').write_bytes(b'updated-unity')
        real_rename = manager._rename_directory

        def fail_publish(source, destination):
            if Path(source).name.startswith('.client-staging-') and Path(destination).name == 'client':
                raise PermissionError(32, 'sharing violation')
            return real_rename(source, destination)

        with patch.object(manager, '_rename_directory', side_effect=fail_publish), \
             patch.object(manager.time, 'sleep'):
            with self.assertRaises(PermissionError):
                self.prepare()
        self.assertEqual(sentinel.read_text(encoding='utf-8'), 'keep')
        self.assertEqual((self.target / 'UnityPlayer.dll').read_bytes(), b'unity-original')
        self.assert_no_staging()

    def test_interrupted_publication_restores_previous_over_present_target(self):
        profile = self.base / 'solo'
        previous = profile / 'backups/previous'
        previous.mkdir(parents=True)
        (previous / 'launcher.exe').write_bytes(b'previous')
        self.target.mkdir()
        (self.target / 'launcher.exe').write_bytes(b'unconfirmed')
        marker = profile / 'client-swap.json'
        marker.write_text(json.dumps({
            'target': str(self.target), 'previous': str(previous),
            'staging': str(profile / '.client-staging-interrupted'),
        }), encoding='utf-8')
        manager._recover_client_swap(profile)
        self.assertEqual((self.target / 'launcher.exe').read_bytes(), b'previous')
        self.assertFalse((profile / 'client-swap.json').exists())
        self.assertEqual(list(profile.glob('.client-recovery-*')), [])

    def test_preexisting_staging_path_is_never_deleted(self):
        profile = self.base / 'solo'
        staging = profile / '.client-staging-deadbeef'
        staging.mkdir(parents=True)
        sentinel = staging / 'sentinel'
        sentinel.write_text('keep', encoding='utf-8')
        with patch.object(manager.uuid, 'uuid4', return_value=type('Identifier', (), {'hex': 'deadbeef'})()):
            with self.assertRaises(FileExistsError):
                self.prepare()
        self.assertEqual(sentinel.read_text(encoding='utf-8'), 'keep')
        self.assertFalse(self.target.exists())

    def test_transient_winerror_is_retried_but_nontransient_is_not(self):
        first, second = self.root / 'first', self.root / 'second'
        first.mkdir()
        real_rename = Path.rename
        attempts = []

        def windows_error(code, message):
            error = PermissionError(message)
            error.winerror = code
            return error

        def transient(path, destination):
            attempts.append(path)
            if len(attempts) == 1:
                raise windows_error(32, 'sharing violation')
            return real_rename(path, destination)

        with patch.object(Path, 'rename', transient), patch.object(manager.time, 'sleep') as sleep:
            manager._rename_directory(first, second)
        self.assertEqual(len(attempts), 2)
        sleep.assert_called_once_with(0.1)
        third, fourth = self.root / 'third', self.root / 'fourth'
        third.mkdir()
        with patch.object(Path, 'rename', side_effect=windows_error(145, 'not empty')) as rename:
            with self.assertRaises(PermissionError):
                manager._rename_directory(third, fourth)
        self.assertEqual(rename.call_count, 1)

    def test_unsafe_swap_marker_cannot_move_external_data(self):
        profile = self.base / 'solo'
        profile.mkdir(parents=True)
        external = self.root / 'external-backup'
        external.mkdir()
        (external / 'sentinel').write_text('keep', encoding='utf-8')
        marker = profile / 'client-swap.json'
        marker.write_text(json.dumps({
            'target': str(self.target), 'previous': str(external),
            'staging': str(profile / '.client-staging-deadbeef'),
        }), encoding='utf-8')
        with self.assertRaises(ValueError):
            manager._recover_client_swap(profile)
        self.assertTrue(marker.exists())
        self.assertFalse(self.target.exists())
        self.assertEqual((external / 'sentinel').read_text(encoding='utf-8'), 'keep')

    def test_pcgame_sdk_replacement_is_hashed_and_original_is_unchanged(self):
        original_sdk = self.source / 'launcher_Data/Plugins/x86_64/PCGameSDK.dll'
        original_sdk.parent.mkdir(parents=True, exist_ok=True)
        original_sdk.write_bytes(b'official-sdk')
        self.prepare()
        installed = self.target / 'launcher_Data/Plugins/x86_64/PCGameSDK.dll'
        self.assertEqual(installed.read_bytes(), (source_file(REPO, 'LocalUidSDK.dll')).read_bytes())
        self.assertEqual(original_sdk.read_bytes(), b'official-sdk')
        self.assertTrue(identity.cache_matches(self.source, self.marker(), {
            'launcher_Data/Plugins/x86_64/PCGameSDK.dll': identity.file_digest(source_file(REPO, 'LocalUidSDK.dll')),
        }))
        installed.write_bytes(b'old-or-poisoned-sdk')
        self.assertFalse(identity.cache_matches(self.source, self.marker(), {
            'launcher_Data/Plugins/x86_64/PCGameSDK.dll': identity.file_digest(source_file(REPO, 'LocalUidSDK.dll')),
        }))
        self.prepare()
        self.assertEqual(installed.read_bytes(), (source_file(REPO, 'LocalUidSDK.dll')).read_bytes())
        self.assertEqual(original_sdk.read_bytes(), b'official-sdk')

    def test_quicksdk_runtime_cache_gate_rejects_tampering(self):
        self.prepare()
        self.assertEqual(runner._validated_cache(self.target), self.source.resolve())
        (self.target / 'launcher.exe').write_bytes(b'poisoned-launcher')
        with self.assertRaisesRegex(ValueError, 'cache'):
            runner._validated_cache(self.target)

    def test_invalid_version_timestamp_is_rejected(self):
        path = self.source / 'launcher_Data/yoo/DefaultPackage/ManifestFiles/PackageManifest_DefaultPackage.version'
        path.write_text('2026-99-99-99-99-99', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'timestamp'):
            repair_version.read_version(path)

    def test_linked_version_manifest_is_rejected(self):
        if os.name != 'nt':
            self.skipTest('Windows symlink test requires Windows.')
        external = self.root / 'external.version'
        external.write_text('2026-11-07-22-39-35', encoding='utf-8')
        path = self.source / 'launcher_Data/yoo/DefaultPackage/ManifestFiles/PackageManifest_DefaultPackage.version'
        path.unlink()
        try:
            path.symlink_to(external)
        except OSError as error:
            self.skipTest('Symlink creation unavailable: ' + str(error))
        with self.assertRaisesRegex(ValueError, 'non-linked'):
            repair_version.read_version(path)
        self.assertEqual(external.read_text(encoding='utf-8'), '2026-11-07-22-39-35')

    def test_missing_resource_manifest_does_not_preserve_stale_mapping(self):
        server = self.root / 'LoloServer'
        (server / 'data').mkdir(parents=True)
        (server / 'Resource').mkdir()
        (server / 'db').mkdir()
        (server / 'Lolo.exe').write_bytes(b'synthetic-server')
        (server / 'config.json').write_text(json.dumps({
            'HttpNet': {}, 'GateWay': {}, 'LogServer': {}, 'DB': {},
        }), encoding='utf-8')
        mapping = server / 'data/ClientVersion.json'
        before = b'{"VersionMap":{"2026-10-21-14-05-50":"stale"}}'
        mapping.write_bytes(before)
        with closing(sqlite3.connect(server / 'db/lolo.db')) as connection, connection:
            connection.executescript('CREATE TABLE of_user(user_id INTEGER); CREATE TABLE of_game(user_id INTEGER); '
                                     'CREATE TABLE of_game_basic(user_id INTEGER);')
        resource = self.source / 'launcher_Data/yoo/DefaultPackage/ManifestFiles/PackageManifest_DefaultPackage.version'
        resource.unlink()
        source_before = self.snapshot(self.source)
        with self.assertRaises(FileNotFoundError):
            manager.prepare(self.source, self.base, 'solo', '127.0.0.1')
        self.assertEqual(mapping.read_bytes(), before)
        self.assertEqual(self.snapshot(self.source), source_before)

    def test_preflight_reports_linked_client_without_crashing(self):
        if os.name != 'nt':
            self.skipTest('Windows Junction test requires Windows.')
        linked = self.root / 'linked-client'
        self.junction(linked, self.source)
        result = preflight.check(linked, 'solo')
        self.assertFalse(result['ready'])
        self.assertTrue(any('symlink or Junction' in item for item in result['missing']))


class JunctionIsolationTests(ClientIsolationTestCase):
    # Keep the real Junction cases in a separate class so non-Windows/restricted
    # hosts skip only those tests, not the ordinary temporary-directory coverage.
    def test_whitelist_links_assets_but_copies_mutable_manifests(self):
        self.require_junctions()
        resources = self.source / 'launcher_Data/Resources'
        resources.mkdir()
        (resources / 'shader.bin').write_bytes(b'shader')
        assets = self.source / 'launcher_Data/StreamingAssets/aa'
        assets.mkdir()
        (assets / 'asset.bin').write_bytes(b'asset')
        before = self.snapshot(self.source)
        self.prepare()
        for relative in ('launcher_Data/Resources', 'launcher_Data/StreamingAssets/aa'):
            self.assertTrue(identity.is_junction(self.target / relative))
            self.assertEqual((self.target / relative).resolve(), (self.source / relative).resolve())
        manifest = self.target / 'launcher_Data/yoo/DefaultPackage/ManifestFiles/PackageManifest_DefaultPackage.version'
        self.assertFalse(identity.is_reparse_point(manifest))
        manifest.write_text('2026-12-01-00-00-00', encoding='utf-8')
        source_manifest = self.source / 'launcher_Data/yoo/DefaultPackage/ManifestFiles/PackageManifest_DefaultPackage.version'
        self.assertEqual(source_manifest.read_text(encoding='utf-8'), '2026-11-07-22-39-35')
        self.assertEqual(self.snapshot(self.source), before)
        self.assertFalse(identity.cache_matches(self.source, self.marker()))

    def test_retargeted_junction_invalidates_cache_without_writing_external(self):
        self.require_junctions()
        resources = self.source / 'launcher_Data/Resources'
        resources.mkdir()
        (resources / 'shader.bin').write_bytes(b'shader')
        self.prepare()
        linked = self.target / 'launcher_Data/Resources'
        linked.rmdir()
        external = self.root / 'external-assets'
        external.mkdir()
        (external / 'sentinel').write_text('keep', encoding='utf-8')
        self.junction(linked, external)
        self.assertFalse(identity.cache_matches(self.source, self.marker()))
        self.prepare()
        self.assertEqual(linked.resolve(), resources.resolve())
        self.assertEqual((external / 'sentinel').read_text(encoding='utf-8'), 'keep')

    def test_target_root_junction_to_original_is_rejected(self):
        self.target.parent.mkdir(parents=True)
        self.junction(self.target, self.source)
        before = self.snapshot(self.source)
        with self.assertRaisesRegex(ValueError, 'linked private-client destination'):
            self.prepare()
        self.assertEqual(self.snapshot(self.source), before)

    def test_source_reparse_point_outside_whitelist_is_rejected(self):
        self.require_junctions()
        external = self.root / 'external-plugin'
        external.mkdir()
        (external / 'sentinel').write_text('keep', encoding='utf-8')
        self.junction(self.source / 'launcher_Data/Plugins/External', external)
        with self.assertRaisesRegex(ValueError, 'symlink or Junction'):
            self.prepare()
        self.assertEqual((external / 'sentinel').read_text(encoding='utf-8'), 'keep')
        self.assertFalse(self.target.exists())

    def test_linked_ready_marker_is_rejected_and_rebuilt(self):
        if os.name != 'nt':
            self.skipTest('Windows symlink test requires Windows.')
        self.prepare()
        external = self.root / 'external-ready.json'
        external.write_text('{"schema":2}', encoding='utf-8')
        self.marker().unlink()
        try:
            self.marker().symlink_to(external)
        except OSError as error:
            self.skipTest('Symlink creation unavailable: ' + str(error))
        self.assertFalse(identity.cache_matches(self.source, self.marker()))
        self.prepare()
        self.assertFalse(identity.is_reparse_point(self.marker()))
        self.assertEqual(external.read_text(encoding='utf-8'), '{"schema":2}')

    def test_linked_swap_marker_and_staging_are_rejected(self):
        self.require_junctions()
        profile = self.base / 'solo'
        profile.mkdir(parents=True)
        external = self.root / 'external-marker'
        external.mkdir()
        marker = profile / 'client-swap.json'
        self.junction(marker, external)
        with self.assertRaisesRegex(ValueError, 'linked client swap marker'):
            manager._recover_client_swap(profile)
        self.assertTrue(identity.is_junction(marker))
        marker.rmdir()
        external_staging = self.root / 'external-staging'
        external_staging.mkdir()
        (external_staging / 'sentinel').write_text('keep', encoding='utf-8')
        staging = profile / '.client-staging-deadbeef'
        self.junction(staging, external_staging)
        marker.write_text(json.dumps({
            'target': str(self.target), 'staging': str(staging),
        }), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'staging path is linked'):
            manager._recover_client_swap(profile)
        self.assertTrue(marker.is_file())
        self.assertEqual((external_staging / 'sentinel').read_text(encoding='utf-8'), 'keep')

    def test_target_root_junction_to_another_client_is_rejected(self):
        self.require_junctions()
        self.prepare()
        external = self.root / 'external-client'
        self.target.rename(external)
        self.junction(self.target, external)
        before = self.snapshot(external)
        with self.assertRaisesRegex(ValueError, 'linked private-client destination'):
            self.prepare()
        self.assertEqual(self.snapshot(external), before)
        with patch.object(runner, 'hashlib') as digest:
            digest.sha256.return_value.hexdigest.return_value = runner.SUPPORTED_GAME_ASSEMBLY
            with self.assertRaisesRegex(ValueError, 'linked private-client root'):
                runner.validated_config(self.target, '127.0.0.1', 18080, 1000000)

    def test_tracked_file_junction_cannot_redirect_hash_reads(self):
        self.require_junctions()
        self.prepare()
        assembly = self.target / 'GameAssembly.dll'
        assembly.unlink()
        external = self.root / 'external-assembly'
        external.mkdir()
        (external / 'GameAssembly.dll').write_bytes(b'poison')
        self.junction(assembly, external)
        self.assertFalse(identity.cache_matches(self.source, self.marker()))
        self.prepare()
        self.assertFalse(identity.is_reparse_point(assembly))
        self.assertEqual(assembly.read_bytes(), b'assembly-original')
        self.assertEqual((external / 'GameAssembly.dll').read_bytes(), b'poison')

    def test_linked_asset_source_change_invalidates_cache(self):
        self.require_junctions()
        resources = self.source / 'launcher_Data/Resources'
        resources.mkdir()
        shader = resources / 'shader.bin'
        shader.write_bytes(b'old-shader')
        self.prepare()
        shader.write_bytes(b'new-shader')
        self.assertFalse(identity.cache_matches(self.source, self.marker()))
        self.prepare()
        self.assertEqual((self.target / 'launcher_Data/Resources/shader.bin').read_bytes(), b'new-shader')
        self.assertTrue(identity.cache_matches(self.source, self.marker()))

    def test_yoo_cache_is_linked_while_manifests_copy_and_large_asset_edits_invalidate(self):
        self.require_junctions()
        stream_yoo = self.source / 'launcher_Data/StreamingAssets/yoo/DefaultPackage'
        stream_bundle = stream_yoo / 'large_asset.bundle'
        stream_bundle.write_bytes(b'A' * 8192)
        cache_dir = self.source / 'launcher_Data/yoo/DefaultPackage/CacheFiles/ab'
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_bundle = cache_dir / '__data'
        cache_bundle.write_bytes(b'B' * 8192)
        self.prepare()
        for linked_rel in ('launcher_Data/StreamingAssets/yoo', 'launcher_Data/yoo/DefaultPackage/CacheFiles'):
            linked = self.target / linked_rel
            self.assertTrue(identity.is_junction(linked), linked_rel)
            self.assertEqual(linked.resolve(), (self.source / linked_rel).resolve())
        manifest = self.target / 'launcher_Data/yoo/DefaultPackage/ManifestFiles/PackageManifest_DefaultPackage.version'
        self.assertFalse(identity.is_reparse_point(manifest))
        self.assertTrue(identity.cache_matches(self.source, self.marker()))
        cache_bundle.write_bytes(b'C' * 9000)
        self.assertFalse(identity.cache_matches(self.source, self.marker()))
        self.prepare()
        self.assertTrue(identity.cache_matches(self.source, self.marker()))

    def test_large_client_ready_marker_over_one_megabyte_is_supported(self):
        self.prepare()
        recorded = identity.read_ready_marker(self.marker())
        recorded['files']['launcher_Data/StreamingAssets/yoo/pad'] = '0' * 64
        # Pad with extra entries exceeding the legacy 1 MiB limit while staying under 16 MiB.
        for idx in range(13000):
            recorded['files'][f'launcher_Data/StreamingAssets/yoo/bundle_{idx:05d}.bin'] = f'{idx:064x}'
        raw = json.dumps(recorded).encode('utf-8')
        self.assertGreater(len(raw), 1024 * 1024)
        self.marker().write_bytes(raw)
        loaded = identity.read_ready_marker(self.marker())
        self.assertEqual(len(loaded['files']), len(recorded['files']))
        self.marker().write_bytes(b' ' * (identity.MAX_READY_MARKER_BYTES + 1))
        with self.assertRaisesRegex(ValueError, 'Invalid client-ready marker'):
            identity.read_ready_marker(self.marker())

    def test_json_write_refuses_linked_parent(self):
        self.require_junctions()
        external = self.root / 'external-output'
        external.mkdir()
        linked = self.root / 'linked-output'
        self.junction(linked, external)
        with self.assertRaisesRegex(ValueError, 'symlink or Junction'):
            manager.write_json(linked / 'client-swap.json', {'unsafe': True})
        self.assertEqual(list(external.iterdir()), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
