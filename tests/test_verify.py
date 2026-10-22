"""Verification environment checks; no game files or .NET SDK required."""
import contextlib
import io
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import verify


class VerifyTests(unittest.TestCase):
    def invoke(self, *args):
        with patch.object(sys, "argv", ["verify.py", *args]):
            verify.main()

    def test_missing_dependency_fails_before_build_or_install(self):
        for args in ((), ("--game-path", "/unused/game")):
            with self.subTest(args=args), patch.dict(sys.modules, {"toon_format": None}), \
                    patch("verify.subprocess.run") as child:
                with self.assertRaisesRegex(SystemExit, "requirements-runtime.txt") as error:
                    self.invoke(*args)
                self.assertIn(sys.executable, str(error.exception))
                child.assert_not_called()

    def test_help_does_not_require_runtime_dependencies(self):
        with patch.dict(sys.modules, {"toon_format": None}), \
                patch("verify.subprocess.run") as child, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                self.invoke("--help")
            self.assertEqual(error.exception.code, 0)
            child.assert_not_called()

    def test_python_steps_keep_the_selected_interpreter_and_runtime_checks(self):
        python = "/venv with spaces/python"
        for args in ((), ("--game-path", "/unused/game")):
            with self.subTest(args=args), \
                    patch.dict(sys.modules, {"toon_format": types.ModuleType("toon_format")}), \
                    patch.object(sys, "executable", python), patch("verify.run") as run, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.invoke(*args)
            commands = [call.args for call in run.call_args_list]
            python_commands = [command for command in commands if command[0] not in ("dotnet", "git")]
            self.assertTrue(python_commands)
            self.assertTrue(all(command[0] == python for command in python_commands))
            self.assertIn((python, "-m", "unittest", "discover", "-s", "tests", "-p", "test_runtime.py"), commands)
            self.assertTrue(any(command[1] == "scripts/verify_distribution.py" for command in python_commands))
            self.assertEqual(any(command[1] == "scripts/physics_playground.py" for command in python_commands), bool(args))


if __name__ == "__main__":
    unittest.main()
