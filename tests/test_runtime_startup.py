"""Fixture-only startup diagnostics; no real processes, Steam or game launch."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from runtime import wait_ready
from runtime_startup import diagnose_startup


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux /proc diagnostics")
class StartupDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.proc = self.root / "proc"
        self.proc.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        self.control = self.root / "this-launch"
        self.control.mkdir()
        self.private = self.root / "private-config"

    def process(self, pid, argv=None, environment=None, executable="OxygenNotIncluded"):
        process = self.proc / str(pid)
        process.mkdir()
        (process / "exe").symlink_to("/opt/game/" + executable)
        argv = argv if argv is not None else [
            executable, "--oniharness-control", str(self.control)]
        (process / "cmdline").write_bytes(b"\0".join(os.fsencode(v) for v in argv) + b"\0")
        environment = environment if environment is not None else {"HOME": str(self.home)}
        (process / "environ").write_bytes(b"\0".join(
            os.fsencode(key + "=" + value) for key, value in environment.items()) + b"\0")
        return process

    def diagnose(self):
        return diagnose_startup(self.control, proc_root=self.proc,
                                default_home=self.home, platform="linux")

    def install(self, config):
        folder = config / "unity3d/Klei/Oxygen Not Included/mods/Local/OniHarness.Runtime"
        folder.mkdir(parents=True)
        (folder / "OniHarness.Runtime.dll").write_bytes(b"fixture")
        (folder / "mod.yaml").write_text("staticID: LIghtJUNction.OniHarness.Runtime\n")
        return folder

    def test_private_config_missing_runtime_is_distinguished_from_installed_host_config(self):
        self.install(self.home / ".config")
        self.process(101, environment={"HOME": str(self.home),
                     "XDG_CONFIG_HOME": str(self.private), "API_TOKEN": "SECRET_MUST_NOT_APPEAR"})
        before = sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*"))
        report = self.diagnose()
        row = report["matchingProcesses"][0]
        self.assertEqual(row["pid"], 101)
        self.assertEqual(row["effectiveConfigHome"], str(self.private))
        self.assertTrue(row["differsFromDefaultHostConfig"])
        self.assertFalse(row["runtimeDllPresent"])
        self.assertIn("/mods/Local/OniHarness.Runtime/", row["runtimeDll"])
        self.assertIn("After normal game exit", row["next"])
        self.assertEqual(set(row["environment"]), {"HOME", "XDG_CONFIG_HOME"})
        self.assertNotIn("SECRET_MUST_NOT_APPEAR", json.dumps(report))
        self.assertNotIn("API_TOKEN", json.dumps(report))
        self.assertEqual(before, sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*")))

    def test_only_exact_unambiguous_control_and_game_executable_are_inspected(self):
        self.process(101)
        self.process(102, argv=["OxygenNotIncluded", "--oniharness-control", str(self.control) + "-other"])
        self.process(103, executable="steam")
        self.process(104, argv=["OxygenNotIncluded", "--oniharness-control", str(self.control),
                               "--oniharness-control", "/different"])
        original = Path.read_bytes

        def checked_read(path):
            if path.name == "environ" and path.parent.name != "101":
                raise AssertionError("Read environment for an unrelated process")
            return original(path)

        with patch.object(Path, "read_bytes", checked_read):
            report = self.diagnose()
        self.assertEqual([row["pid"] for row in report["matchingProcesses"]], [101])

    def test_equals_form_and_home_default_report_existing_runtime(self):
        folder = self.install(self.home / ".config")
        self.process(101, argv=["OxygenNotIncluded.x86_64",
                               "--oniharness-control=" + str(self.control)],
                     executable="OxygenNotIncluded.x86_64")
        row = self.diagnose()["matchingProcesses"][0]
        self.assertEqual(row["configSource"], "HOME/.config")
        self.assertFalse(row["differsFromDefaultHostConfig"])
        self.assertEqual(row["runtimeDirectory"], str(folder))
        self.assertTrue(row["runtimeDllPresent"])
        self.assertTrue(row["modYamlPresent"])
        self.assertIn("does not prove", row["next"])

    def test_absent_allowed_environment_is_unknown_not_missing_runtime(self):
        self.process(101, environment={"API_TOKEN": "ignored"})
        row = self.diagnose()["matchingProcesses"][0]
        self.assertEqual(row["environment"], {})
        self.assertIn("effective config is unknown", row["reason"])
        self.assertNotIn("runtimeDllPresent", row)

    def test_relative_config_does_not_guess_an_effective_path(self):
        self.process(101, environment={"HOME": str(self.home), "XDG_CONFIG_HOME": "relative"})
        row = self.diagnose()["matchingProcesses"][0]
        self.assertIn("not absolute", row["reason"])
        self.assertNotIn("runtimeDllPresent", row)

    def test_unreadable_environment_is_reported_without_fallback_guess(self):
        process = self.process(101)
        original = Path.read_bytes

        def checked_read(path):
            if path == process / "environ":
                raise PermissionError("PRIVATE_EXCEPTION_DETAILS")
            return original(path)

        with patch.object(Path, "read_bytes", checked_read):
            report = self.diagnose()
        row = report["matchingProcesses"][0]
        self.assertIn("PermissionError", row["reason"])
        self.assertNotIn("runtimeDllPresent", row)
        self.assertNotIn("PRIVATE_EXCEPTION_DETAILS", json.dumps(report))

    def test_other_platform_and_missing_proc_are_explicit(self):
        report = diagnose_startup(self.control, proc_root=self.root / "absent", platform="darwin")
        self.assertEqual(report["matchingProcesses"], [])
        self.assertIn("Linux only", report["reason"])
        report = diagnose_startup(self.control, proc_root=self.root / "absent",
                                  default_home=self.home, platform="linux")
        self.assertIn("Process listing unavailable", report["reason"])

    def test_missing_status_timeout_attaches_actionable_diagnostic_without_launching(self):
        self.process(101, environment={"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.private)})
        with patch("runtime.time.monotonic", side_effect=[0, 1]), \
                patch("runtime.diagnose_startup", side_effect=lambda _: self.diagnose()), \
                patch("runtime.subprocess.Popen") as launch:
            with self.assertRaises(RuntimeError) as caught:
                wait_ready(self.control, 0)
            launch.assert_not_called()
        message = str(caught.exception)
        self.assertIn("Startup did not verify: no companion telemetry", message)
        self.assertIn("No fallback or restart was performed", message)
        self.assertIn(str(self.private), message)
        self.assertIn('"runtimeDllPresent": false', message)

    def test_existing_status_failure_keeps_original_diagnostic_path(self):
        (self.control / "status.json").write_text("{}")
        with patch("runtime.time.monotonic", side_effect=[0, 1]), \
                patch("runtime.diagnose_startup") as diagnostic:
            with self.assertRaisesRegex(RuntimeError, "Startup did not verify"):
                wait_ready(self.control, 0)
            diagnostic.assert_not_called()

    def test_diagnostic_failure_does_not_replace_startup_failure_or_leak_details(self):
        with patch("runtime.time.monotonic", side_effect=[0, 1]), \
                patch("runtime.diagnose_startup", side_effect=OSError("PRIVATE_EXCEPTION_DETAILS")):
            with self.assertRaises(RuntimeError) as caught:
                wait_ready(self.control, 0)
        message = str(caught.exception)
        self.assertIn("Startup did not verify", message)
        self.assertIn("diagnostics unavailable: OSError", message)
        self.assertNotIn("PRIVATE_EXCEPTION_DETAILS", message)


if __name__ == "__main__":
    unittest.main()
