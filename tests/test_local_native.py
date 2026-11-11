import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import native_tests as runner


class NativeRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = self.root / "Cargo.toml"
        self.manifest.write_text('[package]\nname="fixture"\nversion="0.1.0"\n')
        self.binary = self.root / "test-binary"
        self.binary.write_bytes(b"native test executable")
        self.commands = []

    def invoke(self, command, cwd, timeout):
        self.commands.append(command)
        output = "test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out\n"
        if command[0] == "cargo":
            output = json.dumps({"reason": "compiler-artifact", "manifest_path": str(self.manifest),
                                 "executable": str(self.binary), "profile": {"test": True}, "fresh": True})
        elif "--list" in command:
            output = "fluid::falls: test\ngas::diffuses: test\nfluid::gpu: test\n"
        return dict(returncode=0, stdout=output, stderr="", seconds=0.01, timedOut=False)

    def run_selected(self, **kwargs):
        with patch.object(runner, "invoke", side_effect=self.invoke):
            return runner.run_native(self.root, **kwargs)

    def test_filter_builds_once_and_executes_only_exact_selected_names(self):
        result = self.run_selected(filters=["fluid::falls"])
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["buildFresh"])
        self.assertFalse(result["runtimeVerified"])
        self.assertFalse(result["gameLaunched"])
        self.assertEqual(len(self.commands), 3)
        self.assertEqual(self.commands[-1][3:], ["fluid::falls"])
        self.assertNotIn("--release", self.commands[0])
        self.assertIn("--no-run", self.commands[0])

    def test_no_selection_fails_before_any_build(self):
        result = self.run_selected()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.commands, [])

    def test_empty_match_cannot_pass(self):
        result = self.run_selected(filters=["missing"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(self.commands), 2)

    def test_unknown_suite_fails_before_build(self):
        self.assertEqual(self.run_selected(suites=["missing"])["status"], "failed")
        self.assertEqual(self.commands, [])

    def test_changed_files_select_mapped_suites(self):
        (self.root / "oniharness.json").write_text(json.dumps({"suites": {
            "liquid": {"filters": ["fluid::falls"], "paths": ["src/fluid*.rs"]}}}))
        result = self.run_selected(changed_files=["src/fluid.rs"])
        self.assertEqual(result["suites"], ["liquid"])
        self.assertEqual(result["status"], "passed")

    def test_unmapped_change_fails_before_build(self):
        self.assertEqual(self.run_selected(changed_files=["src/unknown.rs"])["status"], "failed")
        self.assertEqual(self.commands, [])

    def test_suite_skip_does_not_suppress_an_explicitly_selected_gpu_test(self):
        (self.root / "oniharness.json").write_text(json.dumps({"suites": {
            "cpu": {"filters": ["fluid::"], "skip": ["gpu"]},
            "gpu": {"filters": ["fluid::gpu"]}}}))
        result = self.run_selected(suites=["cpu", "gpu"])
        self.assertEqual(result["selectedTests"], ["fluid::falls", "fluid::gpu"])
        # The fake executor reports only one run, so incomplete execution fails.
        self.assertEqual(result["status"], "failed")

    def test_partial_unmatched_filter_cannot_silently_reduce_coverage(self):
        result = self.run_selected(filters=["fluid::falls", "typo"])
        self.assertEqual(result["status"], "failed")
        self.assertIn("Unmatched", result["error"])
        self.assertEqual(len(self.commands), 2)

    def test_cargo_diagnostics_include_source_error(self):
        output = json.dumps({"reason": "compiler-message", "message": {
            "level": "error", "rendered": "src/fluid.rs:17: unknown field"}})
        with patch.object(runner, "invoke", return_value=dict(returncode=101, stdout=output,
                stderr="could not compile", seconds=0.01, timedOut=False)):
            result = runner.run_native(self.root, filters=["fluid"])
        self.assertIn("src/fluid.rs:17", result["error"])

    def test_release_is_explicit(self):
        self.run_selected(filters=["fluid::falls"], profile="release")
        self.assertIn("--release", self.commands[0])

    def test_build_failure_stops_discovery(self):
        with patch.object(runner, "invoke", return_value=dict(returncode=101, stdout="", stderr="compile error", seconds=0.01, timedOut=False)) as call:
            result = runner.run_native(self.root, filters=["fluid"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(call.call_count, 1)

    def test_timeout_and_zero_executed_tests_fail(self):
        original = self.invoke
        for output, timeout in [("test result: ok. 0 passed; 0 failed; 1 ignored", False), ("", True)]:
            def invocation(command, cwd, limit):
                result = original(command, cwd, limit)
                if "--exact" in command:
                    result.update(stdout=output, timedOut=timeout)
                return result
            with patch.object(runner, "invoke", side_effect=invocation):
                self.assertEqual(runner.run_native(self.root, filters=["fluid::falls"])["status"], "failed")

    def test_actual_process_timeout_returns_failure(self):
        result = runner.invoke([sys.executable, "-c", "import time; time.sleep(10)"], self.root, 0.05)
        self.assertTrue(result["timedOut"])
        self.assertNotEqual(result["returncode"], 0)

    def test_malformed_suite_is_rejected_before_build(self):
        (self.root / "oniharness.json").write_text('{"suites":{"broken":{"filters":"fluid"}}}')
        self.assertEqual(self.run_selected(suites=["broken"])["status"], "failed")
        self.assertEqual(self.commands, [])


if __name__ == "__main__":
    unittest.main()
