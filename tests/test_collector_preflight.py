"""Read-only preflight contract tests; native calls mocked, no real capture."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("collector_preflight", ROOT / "backup-tool/collector_preflight.py")
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


class PreflightTests(unittest.TestCase):
    def test_missing_dependencies_are_not_ready_and_bilingual(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(preflight, "probe", return_value=("ok", "idle")) as probe:
                result = preflight.inspect_runtime(directory, Path(directory)/"no-python", Path(directory)/"no-tshark")
            # On Windows only PktMon status may be queried; never start/stop/remove.
            for call in probe.call_args_list:
                self.assertEqual(call.args[0][-1], "status")
            self.assertFalse(result["ready"])
            self.assertEqual({item["state"] for item in result["checks"]} & {"error"}, {"error"})
            for item in result["checks"]:
                self.assertEqual(set(item), {"id", "state", "message", "messageEn"})
                self.assertTrue(item["messageEn"])

    def test_unknown_dependency_never_ready(self):
        with patch.object(Path, "is_file", return_value=True), patch.object(Path, "is_dir", return_value=True), patch.object(preflight, "probe", return_value=("unknown", "timeout")):
            result = preflight.inspect_runtime(ROOT / "backup-tool", "python.exe", "tshark.exe", timeout=0.1)
        self.assertFalse(result["ready"])
        self.assertEqual(next(item for item in result["checks"] if item["id"] == "python")["state"], "unknown")

    def test_probe_timeout_is_unknown_and_bounded(self):
        with patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("test", 0.1)) as run:
            state, detail = preflight.probe(["dummy.exe"], timeout=0.1)
        self.assertEqual(state, "unknown")
        self.assertIn("timed out", detail)
        self.assertEqual(run.call_args.kwargs["timeout"], 0.1)
        self.assertEqual(run.call_args.kwargs["creationflags"],
                         subprocess.CREATE_NO_WINDOW if preflight.os.name == "nt" else 0)

    def test_failed_probe_preserves_diagnostic(self):
        result = subprocess.CompletedProcess(["dummy"], 42, "output", "diagnostic")
        with patch.object(subprocess, "run", return_value=result):
            state, detail = preflight.probe(["dummy"])
        self.assertEqual(state, "error")
        self.assertIn("42", detail)
        self.assertIn("diagnostic", detail)

    def test_unavailable_probe_is_unknown_not_zero(self):
        with patch.object(subprocess, "run", side_effect=OSError("access denied")):
            state, detail = preflight.probe(["dummy"])
        self.assertEqual(state, "unknown")
        self.assertIn("access denied", detail)

    def test_timeout_bounds(self):
        for timeout in (0, -1, 31):
            with self.assertRaises(ValueError):
                preflight.inspect_runtime(ROOT, "python", "tshark", timeout)


if __name__ == "__main__":
    unittest.main()
