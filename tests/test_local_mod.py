import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import test_mod


class ModEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dll = self.root / "Example.dll"
        self.dll.write_bytes(b"test DLL")

    def test_headless_fails_before_build_or_runtime(self):
        with patch.object(sys, "argv", ["test_mod", "--mod", str(self.dll), "--headless"]), patch.object(test_mod, "build_mod_if_needed") as build, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as failure:
                test_mod.main()
        self.assertEqual(failure.exception.code, 2)
        build.assert_not_called()
        self.assertFalse(test_mod.deploy_and_test_headless(self.dll, self.root)["runtimeVerified"])

    def test_managed_tests_require_relevant_assembly_before_build(self):
        with patch.object(sys, "argv", ["test_mod", "--mod", str(self.dll)]), patch.object(test_mod, "build_mod_if_needed") as build, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                test_mod.main()
        build.assert_not_called()

    def test_managed_skip_build_and_filter_are_forwarded(self):
        args = ["test_mod", "--mod", str(self.dll), "--assembly", str(self.root / "Tests.dll"), "--filter", "Liquid", "--skip-build"]
        with patch.object(sys, "argv", args), patch.object(test_mod, "build_mod_if_needed") as build, patch.object(test_mod, "run_managed_mod_test", return_value={"status": "passed"}) as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(test_mod.main(), 0)
        build.assert_not_called()
        self.assertEqual(run.call_args.args[-1], "Liquid")

    def test_resolves_project_output_without_picking_a_dependency(self):
        self.dll.unlink()
        (self.root / "Example.csproj").write_text('<Project><PropertyGroup><AssemblyName>ActualMod</AssemblyName></PropertyGroup></Project>')
        directory = self.root / "bin/Release/net48"
        directory.mkdir(parents=True)
        (directory / "Dependency.dll").write_bytes(b"dependency")
        actual = directory / "ActualMod.dll"
        actual.write_bytes(b"mod")
        self.assertEqual(test_mod.find_mod_dll(self.root), actual)

    def test_native_entry_writes_failure_report_and_never_builds_managed_mod(self):
        report = self.root / "report.json"
        args = ["test_mod", "--native-project", str(self.root), "--filter", "Liquid", "--report", str(report)]
        with patch.object(sys, "argv", args), patch.object(test_mod, "build_mod_if_needed") as build, patch.object(test_mod, "run_native", return_value={"status": "failed", "error": "no match"}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(test_mod.main(), 1)
        self.assertTrue(report.exists())
        build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
