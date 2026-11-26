"""Regression tests for isolated runtime preparation and failure cleanup."""
import json
import sqlite3
import sys
import tempfile
import types
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

import capture_import
import client_identity
import profile_manager
import service_runner


class SchemaCopyTests(unittest.TestCase):
    def test_schema_copy_preserves_objects_without_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.db"
            target = root / "nested/target.db"
            with closing(sqlite3.connect(source)) as connection, connection:
                connection.executescript("""
                    PRAGMA user_version=7;
                    CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT);
                    CREATE TABLE audit(message TEXT);
                    CREATE TRIGGER items_ai AFTER INSERT ON items BEGIN
                        INSERT INTO audit(message) VALUES('inserted');
                    END;
                    CREATE VIEW item_names AS SELECT name FROM items;
                    INSERT INTO items(name) VALUES('private-row');
                """)
            capture_import.schema_database(source, target)
            with closing(sqlite3.connect(target)) as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 7)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM items").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='view'").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0], 1)
                connection.execute("INSERT INTO items(name) VALUES('local')")
                self.assertEqual(connection.execute("SELECT message FROM audit").fetchone()[0], "inserted")

    def test_failed_schema_copy_does_not_publish_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.db"
            target = root / "target.db"
            with closing(sqlite3.connect(source)) as connection:
                connection.execute("CREATE TABLE item(id INTEGER)")
            real_connect = sqlite3.connect

            def fail_temporary(database, *args, **kwargs):
                if str(database).endswith(".tmp"):
                    raise sqlite3.OperationalError("synthetic disk failure")
                return real_connect(database, *args, **kwargs)

            with patch.object(capture_import.sqlite3, "connect", side_effect=fail_temporary):
                with self.assertRaisesRegex(sqlite3.OperationalError, "synthetic"):
                    capture_import.schema_database(source, target)
            self.assertFalse(target.exists())
            self.assertEqual(list(root.glob(".*.tmp")), [])


class ClientCacheTests(unittest.TestCase):
    def make_client(self, root):
        client = root / "official"
        plugins = client / "launcher_Data/Plugins/Win/PCSDK_release"
        plugins.mkdir(parents=True)
        (client / "launcher.exe").write_bytes(b"launcher")
        (client / "GameAssembly.dll").write_bytes(b"assembly")
        (client / "launcher_Data/app.info").write_text("Inutan\n开放空间\n", encoding="utf-8")
        streaming = client / "launcher_Data/StreamingAssets"
        streaming.mkdir(parents=True)
        (streaming / "Config.txt").write_text("config", encoding="utf-8")
        (plugins / "PCSDKUI.exe").write_bytes(b"quick")
        return client

    def test_cache_rejects_missing_or_modified_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.make_client(root)
            target = root / "profiles/solo/client"
            target.mkdir(parents=True)
            identity = client_identity.fingerprint(source)
            marker = target / "client-ready.json"
            marker.write_text(json.dumps({
                "schema": client_identity.MARKER_SCHEMA,
                "source": str(source), "fingerprint": identity["identity"], "files": identity["files"],
                "copies": identity["files"], "links": {}, "adapter": client_identity.classify(source),
            }), encoding="utf-8")
            self.assertFalse(client_identity.cache_matches(source, marker))
            for relative in identity["files"]:
                source_path = source / relative
                target_path = target / relative
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_bytes(source_path.read_bytes())
            self.assertTrue(client_identity.cache_matches(source, marker))
            (target / "GameAssembly.dll").write_bytes(b"tampered")
            self.assertFalse(client_identity.cache_matches(source, marker))

    def test_failed_rebuild_keeps_previous_client_and_removes_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.make_client(root)
            (source / "launcher_Data/app.info").unlink()
            base = root / "profiles"
            target = base / "solo/client"
            target.mkdir(parents=True)
            (target / "sentinel").write_text("keep", encoding="utf-8")
            with patch.object(profile_manager, "locate", return_value=(source, None)), \
                 patch.object(profile_manager.sys, "version_info", (3, 12, 0)):
                with self.assertRaises(OSError):
                    profile_manager.prepare_client(source, base, "solo")
            self.assertEqual((target / "sentinel").read_text(encoding="utf-8"), "keep")
            self.assertEqual(list((base / "solo").glob(".client-staging-*")), [])


class ServiceRunnerFailureTests(unittest.TestCase):
    def test_failed_quicksdk_result_terminates_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stdout = root / "client-stdout.log"
            result = root / "quicksdk-start-deadbeef.json"
            result.write_text('{"ready": false}', encoding="utf-8")

            class Worker:
                def __init__(self):
                    self.terminated = False
                    self.pid = 4242

                def poll(self):
                    return None

                def terminate(self):
                    self.terminated = True

                def wait(self, timeout=None):
                    return 0

            worker = Worker()
            fake_frida = types.ModuleType("frida")
            with patch.dict(sys.modules, {"frida": fake_frida}), \
                 patch("taptap_private_runner.validated_config", return_value={}), \
                 patch("uuid.uuid4", return_value=types.SimpleNamespace(hex="deadbeef")), \
                 patch.object(service_runner.subprocess, "Popen", return_value=worker):
                with self.assertRaises(RuntimeError):
                    service_runner.launch_quicksdk(
                        root / "profiles/solo/client/launcher.exe", root,
                        stdout, root / "client-stderr.log", {},
                    )
            self.assertTrue(worker.terminated)
            self.assertFalse(result.exists())

    def test_quicksdk_unverified_pid_file_is_cleaned_without_pid_kill(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stdout = root / "client-stdout.log"
            result = root / "quicksdk-start-deadbeef.json"
            pid_file = root / "quicksdk-pid-deadbeef.json"
            result.write_text('{"ready": false}', encoding="utf-8")
            pid_file.write_text('{"pid": 4242}', encoding="utf-8")

            class Worker:
                pid = 4242

                def poll(self):
                    return None

                def terminate(self):
                    pass

                def wait(self, timeout=None):
                    return 0

            with patch.dict(sys.modules, {"frida": types.ModuleType("frida")}), \
                 patch("taptap_private_runner.validated_config", return_value={}), \
                 patch("uuid.uuid4", return_value=types.SimpleNamespace(hex="deadbeef")), \
                 patch.object(service_runner.subprocess, "Popen", return_value=Worker()), \
                 patch.object(service_runner.subprocess, "run") as run:
                with self.assertRaises(RuntimeError):
                    service_runner.launch_quicksdk(
                        root / "profiles/solo/client/launcher.exe", root,
                        stdout, root / "client-stderr.log", {},
                    )
            run.assert_not_called()
            evidence = json.loads((root / "quicksdk-failure-deadbeef.json").read_text(encoding="utf-8"))
            self.assertEqual(evidence["unverifiedPid"], 4242)
            self.assertIsNone(evidence["process"])
            self.assertFalse(pid_file.exists())


class ClientSwapTests(unittest.TestCase):
    def test_interrupted_swap_restores_previous_client(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "solo"
            previous = profile / "backups/previous"
            target = profile / "client"
            previous.mkdir(parents=True)
            (previous / "launcher.exe").write_bytes(b"previous")
            (profile / "client-swap.json").write_text(json.dumps({
                "target": str(target), "previous": str(previous),
                "staging": str(profile / ".client-staging-interrupted"),
            }), encoding="utf-8")
            profile_manager._recover_client_swap(profile)
            self.assertEqual((target / "launcher.exe").read_bytes(), b"previous")
            self.assertFalse((profile / "client-swap.json").exists())

    def test_old_python_is_rejected_before_client_access(self):
        with patch.object(profile_manager.sys, "version_info", (3, 11, 0)):
            with self.assertRaisesRegex(RuntimeError, "Python 3.12"):
                profile_manager.prepare_client(Path("unused"), Path("unused"), "solo")

    def test_sdk_cache_compares_file_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.dll"
            second = root / "second.dll"
            first.write_bytes(b"new-sdk")
            second.write_bytes(b"old-sdk")
            self.assertFalse(profile_manager._same_file_hash(first, second))
            second.write_bytes(b"new-sdk")
            self.assertTrue(profile_manager._same_file_hash(first, second))

    def test_pinned_resource_version_supports_single_manifest_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            client = Path(directory)
            self.assertEqual(
                profile_manager.client_resource_version(client, "2026-10-21-14-05-50"),
                "2026-10-21-14-05-50",
            )
            self.assertIsNone(profile_manager.client_resource_version(client, "unknown"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
