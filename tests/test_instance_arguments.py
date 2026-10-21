import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from instance_arguments import resolve_game_arguments, validate_game_arguments


class ExternalControlArgumentsTests(unittest.TestCase):
    def test_each_launch_has_a_fresh_empty_external_control_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            arguments = ["--test-probe={controlDir}/runs/{runNonce}/probe", "--readonly"]
            first = resolve_game_arguments(arguments, directory, "a" * 32)
            old = pathlib.Path(first[0].partition("=")[2])
            (old / "command.json").write_text('{"action":"quit"}')
            second = resolve_game_arguments(arguments, directory, "b" * 32)
            new = pathlib.Path(second[0].partition("=")[2])
            self.assertNotEqual(old, new)
            self.assertFalse((new / "command.json").exists())
            self.assertTrue((old / "command.json").exists())
            self.assertEqual(second[1], "--readonly")

    def test_unknown_templates_traversal_and_reserved_flags_are_rejected(self):
        for value in ["--probe={other}", "--probe={controlDir}/shared", "--probe={controlDir}/runs/{runNonce}/../probe",
                      "--probe={controlDir}/runs/{runNonce}/{other}", "--oniharness-control=/tmp/foreign", "-nographics"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_game_arguments([value])

    def test_namespace_reuse_and_symlink_escape_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as foreign:
            control = pathlib.Path(directory)
            (control / "runs").symlink_to(foreign, target_is_directory=True)
            with self.assertRaises(ValueError):
                resolve_game_arguments(["--probe={controlDir}/runs/{runNonce}/probe"], control, "a" * 32)
        with tempfile.TemporaryDirectory() as directory:
            args = ["--probe={controlDir}/runs/{runNonce}/probe"]
            resolve_game_arguments(args, directory, "a" * 32)
            with self.assertRaises(FileExistsError):
                resolve_game_arguments(args, directory, "a" * 32)


if __name__ == "__main__":
    unittest.main()
