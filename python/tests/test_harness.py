"""Public Python behavior against a tiny executable backend, without game files."""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from oniharness import CommandError, Harness, HarnessError, TestFailure

BACKEND = """
import json, os, pathlib, subprocess, sys, time
mode, action, *args = sys.argv[1:]
if mode == "hang":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    pathlib.Path(action).write_text(str(child.pid))
    time.sleep(60)
if mode == "setup":
    print("No matching tests found", file=sys.stderr)
    sys.exit(2)
if action == "doctor":
    print(json.dumps({"adapter": "fixture", "gamePath": args[args.index("--game-path") + 1] if "--game-path" in args else os.environ.get("ONIHARNESS_GAME_PATH")}))
elif action == "contract":
    print(json.dumps({"evidence": "metadata-only", "found": mode != "missing"}))
    sys.exit(1 if mode == "missing" else 0)
elif action == "test":
    output = pathlib.Path(args[args.index("--report") + 1])
    tests = [] if mode == "empty" else [{"name": "sample", "status": mode,
        "evidence": "native-sim", "fingerprint": "abc", "error": None if mode == "passed" else "retained failure"}]
    if "--assembly" not in args:
        scenario = json.loads(pathlib.Path(args[0]).read_text())
        for test in tests:
            test["name"] = scenario["name"]
    output.write_text(json.dumps({"schemaVersion": 1, "tests": tests}))
    print("worker output")
    sys.exit(0 if mode in ("passed", "empty") else 1)
"""


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="oniharness test spaces ")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.backend = self.directory / "backend.py"
        self.backend.write_text(BACKEND, encoding="utf-8")

    def harness(self, mode="passed", **options):
        return Harness([sys.executable, self.backend, mode], **options)

    def test_scenario_with_unicode_and_spaces_returns_original_evidence(self):
        output = self.directory / "results/my report.json"
        report = self.harness().run_scenario(
            {"name": "氧气测试 $(never-execute)"}, repeat=3, report_path=output
        )
        self.assertTrue(report.passed)
        self.assertEqual(report.tests[0]["name"], "氧气测试 $(never-execute)")
        self.assertEqual(report.tests[0]["fingerprint"], "abc")
        self.assertEqual(report.tests[0]["evidence"], "native-sim")
        self.assertEqual(json.loads(output.read_text(encoding="utf-8")), report.data)
        self.assertEqual(report.stdout.strip(), "worker output")

    def test_all_worker_failure_statuses_remain_failures_and_report_survives(self):
        for status in ("failed", "unsupported", "timeout", "crashed"):
            with self.subTest(status=status):
                output = self.directory / f"{status}.json"
                with self.assertRaises(TestFailure) as caught:
                    self.harness(status).run_scenario(
                        {"name": status}, report_path=output
                    )
                self.assertFalse(caught.exception.report.passed)
                self.assertEqual(caught.exception.report.tests[0]["status"], status)
                self.assertTrue(output.exists())
                report = self.harness(status).run_scenario(
                    {"name": status}, check=False
                )
                self.assertFalse(report.passed)

    def test_empty_report_cannot_pass_even_with_zero_exit(self):
        with self.assertRaises(TestFailure):
            self.harness("empty").test_assembly("any.dll")

    def test_setup_failure_retains_diagnostics_and_previous_report(self):
        output = self.directory / "old.json"
        output.write_text("previous result")
        with self.assertRaises(CommandError) as caught:
            self.harness("setup").test_assembly("any.dll", report_path=output)
        self.assertEqual(caught.exception.returncode, 2)
        self.assertIn("No matching tests", caught.exception.stderr)
        self.assertEqual(output.read_text(), "previous result")

    def test_doctor_passes_game_path_as_one_argument(self):
        game = self.directory / "game path"
        self.assertEqual(
            self.harness(game_path=game).doctor()["gamePath"], str(game.resolve())
        )

    def test_missing_metadata_method_returns_false(self):
        value = self.harness("missing").contract(
            "any.dll", type_name="Sim", method="Missing"
        )
        self.assertFalse(value["found"])
        self.assertEqual(value["evidence"], "metadata-only")

    def test_invalid_limits_and_shell_string_are_rejected(self):
        with self.assertRaises(TypeError):
            Harness("echo unsafe")
        for value in (0, -1, float("inf"), float("nan")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.harness(process_timeout=value)
        for options in (
            {"repeat": 0},
            {"repeat": 101},
            {"repeat": True},
            {"timeout": 601},
            {"timeout": 0.5},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.harness().test_assembly("any.dll", **options)

    def test_missing_backend_has_actionable_error(self):
        with self.assertRaisesRegex(HarnessError, "Executable not found"):
            Harness([str(self.directory / "does-not-exist")]).doctor()
        with self.assertRaisesRegex(HarnessError, "Not an OniHarness checkout"):
            Harness.from_checkout(self.directory)

    @unittest.skipUnless(os.name == "posix", "POSIX process group assertion")
    def test_parent_timeout_stops_descendant_worker(self):
        marker = self.directory / "worker.pid"
        harness = Harness(
            [sys.executable, self.backend, "hang", str(marker)], process_timeout=1
        )
        with self.assertRaisesRegex(HarnessError, "process tree terminated"):
            harness.doctor()
        pid = int(marker.read_text())
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                # A reparented zombie is terminated, waiting only for host init to reap it.
                stat = Path(f"/proc/{pid}/stat")
                if stat.is_file() and stat.read_text().split(") ", 1)[1].startswith(
                    "Z"
                ):
                    return
                os.kill(pid, 0)
            except (ProcessLookupError, FileNotFoundError):
                return
            time.sleep(0.02)
        self.fail("descendant worker survived timeout")


if __name__ == "__main__":
    unittest.main()
