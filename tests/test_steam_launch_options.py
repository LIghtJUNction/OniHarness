"""Offline configuration fixtures only. Never stop Steam or edit real userdata."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import steam_launch_options as options


def configuration(value='"LaunchOptions"\t"OLD-VALUE"', newline="\n"):
    text = '''// unrelated comments and whitespace remain byte-identical
"UserLocalConfigStore"
{
 "AccountSecret" "DO-NOT-PRINT-ME"
 "Software" { "Valve" { "Steam" { "apps" {
  "123" { "LaunchOptions" "OTHER-GAME" }
  "457140"
  {
   "name" "Oxygen Not Included"
   ENTRY
   "NativeLock" "single-instance=" // preserve this too
  }
 } } } }
 "controller_config" { "457140" { "LaunchOptions" "CONTROLLER-UNCHANGED" } }
}
'''
    return text.replace("ENTRY", value).replace("\n", newline).encode()


class OfflineSteamOptionsTests(unittest.TestCase):
    def test_only_app_launch_token_changes_and_exact_restore(self):
        original = configuration()
        installed = 'python3 "/path with spaces/wrapper.py" %command%'
        changed, journal = options.edit_bytes(original, installed)
        expected = original.replace(b'"OLD-VALUE"', options.quote(installed).encode())
        self.assertEqual(changed, expected)
        self.assertIn(b'"CONTROLLER-UNCHANGED"', changed)
        self.assertIn(b'"OTHER-GAME"', changed)
        self.assertEqual(options.restore_bytes(changed, journal), original)

    def test_missing_option_inserts_without_rewriting_and_roundtrips(self):
        original = configuration("", "\r\n")
        changed, journal = options.edit_bytes(original, "WRAPPER %command%")
        self.assertFalse(journal["wasPresent"])
        self.assertIn(b"\r\n", changed)
        self.assertEqual(options.restore_bytes(changed, journal), original)

    def test_restore_preserves_subsequent_unrelated_updates(self):
        original = configuration()
        installed, journal = options.edit_bytes(original, "WRAPPER %command%")
        later = installed.replace(b'"NativeLock" "single-instance="', b'"NativeLock" "single-instance="\n "new-stat" "changed-by-Steam"')
        restored = options.restore_bytes(later, journal)
        self.assertEqual(restored, original.replace(b'"NativeLock" "single-instance="', b'"NativeLock" "single-instance="\n "new-stat" "changed-by-Steam"'))
        with self.assertRaisesRegex(RuntimeError, "changed since"):
            options.restore_bytes(installed.replace(b"WRAPPER %command%", b"USER-CUSTOM-OPTION"), journal)

    def test_duplicate_and_malformed_targets_fail_closed(self):
        cases = [configuration('"LaunchOptions" "A" "launchoptions" "B"'),
                 configuration().replace(b'"apps" {', b'"apps" "wrong-type" "else" {'),
                 configuration()[:-2], configuration().replace(b'"457140"\n  {', b'"457140"\n  { "LaunchOptions" { "invalid" "type" }')]
        for value in cases:
            with self.subTest(value=value[-30:]):
                with self.assertRaises(ValueError):
                    options.edit_bytes(value, "WRAPPER")

    def test_atomic_edit_refuses_live_steam_and_preserves_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "localconfig.vdf"
            original = configuration()
            path.write_bytes(original)
            path.chmod(0o640)
            selected, before, details = options.read_config(path)
            changed, _ = options.edit_bytes(before, "WRAPPER")
            with patch.object(options, "running_clients", return_value=[{"pid": 1, "executable": "steam"}]):
                with self.assertRaisesRegex(RuntimeError, "fully exited"):
                    options.atomic_replace(selected, before, changed, details)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(Path(directory).iterdir()), [path])
            with patch.object(options, "running_clients", return_value=[]):
                options.atomic_replace(selected, before, changed, details)
            self.assertEqual(path.read_bytes(), changed)
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)

    def test_hash_precondition_and_small_private_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "localconfig.vdf"
            journal = Path(directory) / "restore.json"
            original = configuration()
            path.write_bytes(original)
            args = ["helper", "--config", str(path), "apply", "--value", "WRAPPER", "--journal", str(journal),
                    "--expected-sha256", "0" * 64]
            with patch.object(options, "running_clients", return_value=[]), patch.object(sys, "argv", args):
                with self.assertRaisesRegex(RuntimeError, "SHA256"):
                    options.main()
            self.assertFalse(journal.exists())
            self.assertEqual(path.read_bytes(), original)
            args[-1] = options.sha256(original)
            output = io.StringIO()
            with patch.object(options, "running_clients", return_value=[]), patch.object(sys, "argv", args), contextlib.redirect_stdout(output):
                options.main()
            self.assertEqual(journal.stat().st_mode & 0o777, 0o600)
            self.assertLess(journal.stat().st_size, 4096)
            self.assertNotIn("DO-NOT-PRINT-ME", output.getvalue())
            self.assertNotIn("DO-NOT-PRINT-ME", journal.read_text())
            self.assertTrue(json.loads(output.getvalue())["readbackVerified"])
            edited = path.read_bytes()
            restore = ["helper", "--config", str(path), "restore", "--journal", str(journal), "--expected-sha256", options.sha256(edited)]
            with patch.object(options, "running_clients", return_value=[]), patch.object(sys, "argv", restore), contextlib.redirect_stdout(io.StringIO()):
                options.main()
            self.assertEqual(path.read_bytes(), original)

    def test_concurrent_config_change_and_parent_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "localconfig.vdf"
            original = configuration()
            path.write_bytes(original)
            selected, before, details = options.read_config(path)
            changed, _ = options.edit_bytes(before, "WRAPPER")
            path.write_bytes(original + b"// later unrelated work\n")
            with patch.object(options, "running_clients", return_value=[]):
                with self.assertRaisesRegex(RuntimeError, "changed during"):
                    options.atomic_replace(selected, before, changed, details)
            link = Path(directory) / "link/localconfig.vdf"
            link.parent.mkdir()
            link.symlink_to(path)
            with self.assertRaises(ValueError):
                options.read_config(link)

    def test_plan_is_read_only_and_does_not_emit_private_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "localconfig.vdf"
            original = configuration()
            path.write_bytes(original)
            output = io.StringIO()
            args = ["helper", "--config", str(path), "plan", "--value", "WRAPPER"]
            with patch.object(options, "running_clients", return_value=[{"pid": 1}]), patch.object(sys, "argv", args), contextlib.redirect_stdout(output):
                options.main()
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse(json.loads(output.getvalue())["steamFullyExited"])
            self.assertNotIn("DO-NOT-PRINT-ME", output.getvalue())
            self.assertNotIn("OLD-VALUE", output.getvalue())


if __name__ == "__main__":
    unittest.main()
