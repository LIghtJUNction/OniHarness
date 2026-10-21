"""Synthetic ELF files only. Never executes the fixture or launches a game."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from instance_installation import MARKER, prepare_installation_view


class InstallationViewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.game = self.root / "game"
        self.game.mkdir()
        self.launcher = self.game / "OxygenNotIncluded"
        self.launcher.write_bytes(b"\x7fELF" + b"synthetic, never executable" * 8)
        self.launcher.chmod(0o755)
        self.data = self.game / "OxygenNotIncluded_Data"
        self.data.mkdir()
        self.boot = self.data / "boot.config"
        self.boot.write_text("single-instance=\ngfx-enable-gfx-jobs=1\n")
        (self.game / "UnityPlayer.so").write_bytes(b"synthetic shared data")
        (self.game / "unity.lock").write_bytes(b"original lock")
        (self.game / "player.log").write_text("private log")
        (self.game / "ONI_MP_Log.txt").write_text("private mod log")
        (self.game / "cache").mkdir()
        self.view = self.root / "instance" / "installation"

    def test_independent_real_launcher_and_shared_assets_preserve_source(self):
        before = {p: p.read_bytes() for p in (self.launcher, self.boot, self.game / "unity.lock")}
        result = prepare_installation_view(self.game, self.view)
        launcher = self.view / self.launcher.name
        self.assertFalse(launcher.is_symlink())
        self.assertEqual(launcher.read_bytes(), self.launcher.read_bytes())
        self.assertNotEqual(launcher.stat().st_ino, self.launcher.stat().st_ino)
        self.assertNotEqual(launcher.resolve(), self.launcher.resolve())
        self.assertEqual(launcher.stat().st_mode & 0o777, 0o755)
        self.assertEqual((self.view / self.data.name).resolve(), self.data)
        self.assertTrue((self.view / self.data.name).is_symlink())
        self.assertFalse((self.view / "unity.lock").exists())
        for excluded in ("player.log", "ONI_MP_Log.txt", "cache"):
            self.assertFalse((self.view / excluded).exists())
        self.assertEqual(result["copiedBytes"], self.launcher.stat().st_size)
        self.assertEqual(result["sharedEntries"], [self.data.name, "UnityPlayer.so"])
        self.assertTrue(result["nativeSingletonPreserved"])
        self.assertEqual(result["launcherSha256"], hashlib.sha256(self.launcher.read_bytes()).hexdigest())
        json.dumps(result, allow_nan=False)
        for path, contents in before.items():
            self.assertEqual(path.read_bytes(), contents)

    def test_repeated_prepare_preserves_private_lock_and_reuses_launcher(self):
        prepare_installation_view(self.game, self.view)
        launcher_inode = (self.view / self.launcher.name).stat().st_ino
        lock = self.view / "unity.lock"
        lock.write_bytes(b"active instance lock")
        lock_inode = lock.stat().st_ino
        result = prepare_installation_view(self.game, self.view)
        self.assertEqual(result["copiedBytes"], 0)
        self.assertEqual((self.view / self.launcher.name).stat().st_ino, launcher_inode)
        self.assertEqual(lock.read_bytes(), b"active instance lock")
        self.assertEqual(lock.stat().st_ino, lock_inode)

    def test_unknown_real_file_fails_before_writing_anything(self):
        self.view.mkdir(parents=True)
        unknown = self.view / "do-not-delete.txt"
        unknown.write_bytes(b"other work")
        with self.assertRaisesRegex(ValueError, "Unknown"):
            prepare_installation_view(self.game, self.view)
        self.assertEqual(list(self.view.iterdir()), [unknown])
        self.assertEqual(unknown.read_bytes(), b"other work")

    def test_modified_owned_launcher_and_redirected_link_are_rejected(self):
        prepare_installation_view(self.game, self.view)
        launcher = self.view / self.launcher.name
        original = launcher.read_bytes()
        launcher.write_bytes(original + b"other changes")
        with self.assertRaisesRegex(ValueError, "modified"):
            prepare_installation_view(self.game, self.view)
        self.assertEqual(launcher.read_bytes(), original + b"other changes")
        launcher.write_bytes(original)
        link = self.view / "UnityPlayer.so"
        link.unlink()
        link.symlink_to(self.root / "other library")
        with self.assertRaisesRegex(ValueError, "Unknown"):
            prepare_installation_view(self.game, self.view)

    def test_launcher_symlink_and_shared_lock_are_not_adopted(self):
        self.view.mkdir(parents=True)
        launcher = self.view / self.launcher.name
        launcher.symlink_to(self.launcher)
        with self.assertRaisesRegex(ValueError, "owned real"):
            prepare_installation_view(self.game, self.view)
        launcher.unlink()
        lock = self.view / "unity.lock"
        lock.symlink_to(self.game / "unity.lock")
        with self.assertRaisesRegex(ValueError, "private regular"):
            prepare_installation_view(self.game, self.view)
        self.assertTrue(lock.is_symlink())
        self.assertEqual((self.game / "unity.lock").read_bytes(), b"original lock")

    def test_rejects_overlapping_paths_and_redirected_view(self):
        for destination in (self.game, self.game / "view", self.root):
            with self.assertRaisesRegex(ValueError, "non-overlapping"):
                prepare_installation_view(self.game, destination)
        redirected = self.root / "redirected"
        redirected.symlink_to(self.game, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "real directory"):
            prepare_installation_view(self.game, redirected)

    def test_invalid_or_large_source_launcher_is_never_copied(self):
        for data in (b"shell script", b"\x7fELF" + b"x" * (1024 * 1024)):
            self.launcher.write_bytes(data)
            with self.assertRaises(ValueError):
                prepare_installation_view(self.game, self.view)
            self.assertFalse(self.view.exists())

    def test_hardlinked_launcher_and_real_asset_replacement_are_rejected(self):
        prepare_installation_view(self.game, self.view)
        launcher = self.view / self.launcher.name
        launcher.unlink()
        os.link(self.launcher, launcher)
        with self.assertRaisesRegex(ValueError, "own inode"):
            prepare_installation_view(self.game, self.view)
        launcher.unlink()
        launcher.write_bytes(self.launcher.read_bytes())
        asset = self.view / "UnityPlayer.so"
        asset.unlink()
        asset.write_bytes(b"other work")
        with self.assertRaisesRegex(ValueError, "Unknown"):
            prepare_installation_view(self.game, self.view)
        self.assertEqual(asset.read_bytes(), b"other work")

    def test_marker_from_another_source_or_invalid_shape_is_rejected(self):
        prepare_installation_view(self.game, self.view)
        marker = self.view / MARKER
        for value in ([], {"schema": 1, "gamePath": str(self.root / "other")}):
            marker.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, "different source"):
                prepare_installation_view(self.game, self.view)

    def test_owned_launcher_refresh_and_new_assets_keep_source_locks(self):
        prepare_installation_view(self.game, self.view)
        self.launcher.write_bytes(b"\x7fELF" + b"new synthetic version")
        (self.game / "new asset").write_bytes(b"shared")
        receipt = prepare_installation_view(self.game, self.view)
        self.assertEqual((self.view / self.launcher.name).read_bytes(), self.launcher.read_bytes())
        self.assertEqual(receipt["copiedBytes"], self.launcher.stat().st_size)
        self.assertTrue((self.view / "new asset").is_symlink())
        self.assertEqual(json.loads((self.view / MARKER).read_text()), receipt)
        self.assertEqual((self.game / "unity.lock").read_bytes(), b"original lock")


if __name__ == "__main__":
    unittest.main()
