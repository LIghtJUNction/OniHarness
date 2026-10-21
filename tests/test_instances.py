"""Controller decisions and real daemon/socket lifecycle; no ONI processes started."""
import argparse
import contextlib
from datetime import datetime, timezone, timedelta
import json
import io
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import instance_controller as control
import steam_instance_entry as wrapper


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="oni-ins-")
        self.base = Path(self.temporary.name)
        game = self.base / "game"
        game.mkdir()
        (game / "OxygenNotIncluded").write_bytes(b"\x7fELFnot executable: fixtures never launch ONI")
        (game / "OxygenNotIncluded_Data").mkdir()
        (game / "OxygenNotIncluded_Data/boot.config").write_text("single-instance=\n")
        companion = self.base / "built"
        companion.mkdir()
        (companion / "mod_info.yaml").write_text("APIVersion: 2\n")
        (companion / "mod.yaml").write_text("staticID: LIghtJUNction.OniHarness.Runtime\n")
        (companion / "OniHarness.Runtime.dll").write_bytes(b"fixture DLL metadata only")
        save = self.base / "original.sav"
        save.write_bytes(b"original-save-never-written")
        self.args = argparse.Namespace(root=self.base / "group", game_path=game, companion=companion,
                                       save=save, mod=[], dlc="EXPANSION1_ID", count=4, base_port=28740)
        with contextlib.redirect_stdout(io.StringIO()):
            control.initialize(self.args)
        self.controller = control.Controller(self.args.root)

    def tearDown(self):
        self.temporary.cleanup()

    def test_four_profiles_and_original_save_are_independent(self):
        original = self.args.save.read_bytes()
        roots, identities, ports = [], [], []
        for name, instance in self.controller.instances.items():
            paths = self.controller.prepare(name)
            roots.append(str(paths["user"]))
            identities.append(instance["identity"])
            ports.append(instance["port"])
            profile = json.loads((paths["control"] / "instance-profile.json").read_text())
            self.assertEqual(profile["identity"], instance["identity"])
            self.assertEqual(profile["userRoot"], str(paths["user"]))
            manifest = json.loads((paths["user"] / "mods/mods.json").read_text())
            self.assertEqual(len(manifest["mods"]), 1)
            self.assertFalse(manifest["mod_load_in_progress"])
            Path(profile["approvedSaves"][0]["path"]).write_bytes(name.encode())
        self.assertEqual(len(set(roots)), 4)
        self.assertEqual(len(set(identities)), 4)
        self.assertEqual(len(set(ports)), 4)
        self.assertEqual(self.args.save.read_bytes(), original)

    def test_reuse_unchanged_loaded_dll_and_restart_changed_dll(self):
        self.controller.prepare("host")
        self.controller.state["instances"]["host"]["pid"] = 123
        status = {"pid": 123}
        with patch.object(self.controller, "live", return_value=True), \
             patch.object(self.controller, "read_status", return_value=status), \
             patch.object(self.controller, "verify_hashes", return_value={}), \
             patch.object(self.controller, "stop") as stop:
            self.assertTrue(self.controller.start("host")["reused"])
            stop.assert_not_called()
        (self.args.companion / "OniHarness.Runtime.dll").write_bytes(b"updated DLL")
        with patch.object(self.controller, "live", return_value=True), \
             patch.object(self.controller, "stop", side_effect=RuntimeError("orderly exit blocked")) as stop:
            with self.assertRaisesRegex(RuntimeError, "orderly exit blocked"):
                self.controller.start("host")
            stop.assert_called_once_with("host")
        # No successful exit means no deployment of new bytes.
        deployed = self.controller.paths("host")["user"] / "mods/Local/OniHarness.Runtime/OniHarness.Runtime.dll"
        self.assertNotEqual(deployed.read_bytes(), (self.args.companion / "OniHarness.Runtime.dll").read_bytes())

    def test_repeat_prepare_retains_discovered_disabled_mod_metadata(self):
        self.controller.config["mods"].extend([
            {"id": name, "staticID": "Test." + name, "source": str(self.args.companion)}
            for name in ("Online", "OtherDeclared")])
        paths = self.controller.prepare("host")
        manifest_path = paths["user"] / "mods/mods.json"
        native = json.loads(manifest_path.read_text())
        native["nativeCacheVersion"] = "preserve-me"
        for index, entry in enumerate(native["mods"]):
            entry.update({"status": 1, "enabled": False, "crash_count": index})
            entry["label"].update({"title": "Native title " + str(index), "version": 700 + index})
        discovered = {"label": {"id": "123456789", "distribution_platform": 2,
                                  "title": "Discovered workshop mod", "version": 87654},
                      "staticID": "Workshop.Discovered", "status": 1, "enabled": False,
                      "enabledForDlc": None, "crash_count": 4,
                      "nativeDiscoveryMetadata": {"installed": True, "unchanged": "yes"}}
        native["mods"].append(discovered)
        control.atomic_json(manifest_path, native)
        for _ in range(2):
            self.controller.prepare("host")
            actual = json.loads(manifest_path.read_text())
            self.assertEqual(actual, native)
            self.assertEqual(sum("EXPANSION1_ID" in (m.get("enabledForDlc") or []) for m in actual["mods"]), 3)
        guest = self.controller.prepare("guest1")
        self.assertEqual(len(json.loads((guest["user"] / "mods/mods.json").read_text())["mods"]), 3)

    def test_manifest_merge_disables_unplanned_mods_without_erasing_cache_or_mutating_input(self):
        previous = {"version": 1, "mod_load_in_progress": True, "mods": [
            {"label": {"id": "Foreign", "distribution_platform": 0, "version": 42},
             "enabled": True, "enabledForDlc": ["EXPANSION1_ID", "OTHER"], "status": 3, "crash_count": 5}]}
        before = json.dumps(previous, sort_keys=True)
        merged = control.merge_mod_manifest(previous, self.controller.config["mods"], "EXPANSION1_ID")
        self.assertEqual(json.dumps(previous, sort_keys=True), before)
        foreign = merged["mods"][0]
        self.assertFalse(foreign["enabled"])
        self.assertEqual(foreign["enabledForDlc"], [])
        self.assertEqual(foreign["label"]["version"], 42)
        self.assertEqual(foreign["crash_count"], 5)
        self.assertTrue(merged["mod_load_in_progress"])
        self.assertEqual(sum("EXPANSION1_ID" in (m.get("enabledForDlc") or []) for m in merged["mods"]), 1)

    def test_manifest_conflicts_fail_before_redeploying_payload(self):
        paths = self.controller.prepare("host")
        manifest = paths["user"] / "mods/mods.json"
        document = json.loads(manifest.read_text())
        document["mods"][0]["staticID"] = "Unrelated.ForeignMod"
        control.atomic_json(manifest, document)
        sentinel = paths["user"] / "mods/Local/OniHarness.Runtime/retained-on-failure.txt"
        sentinel.write_text("untouched")
        with self.assertRaisesRegex(ValueError, "identity conflicts"):
            self.controller.prepare("host")
        self.assertEqual(sentinel.read_text(), "untouched")
        duplicate = {"mods": [document["mods"][0], document["mods"][0]]}
        with self.assertRaisesRegex(ValueError, "duplicate labels"):
            control.merge_mod_manifest(duplicate, [], "EXPANSION1_ID")

    def test_loaded_hash_mismatch_fails(self):
        paths = self.controller.prepare("host")
        status = {"pid": 123, "identity": "test", "runNonce": "run", "assemblyReceipt": "instance-assemblies.json"}
        receipt = {"pid": 123, "identity": "test", "runNonce": "run",
                   "assemblies": [{"location": "/foreign/OniHarness.Runtime.dll", "sha256": "0" * 64}]}
        control.atomic_json(paths["control"] / "instance-assemblies.json", receipt)
        with self.assertRaisesRegex(RuntimeError, "loaded mod hashes"):
            self.controller.verify_hashes("host", status)
        receipt["assemblies"][0]["sha256"] = control.digest(self.args.companion / "OniHarness.Runtime.dll")
        control.atomic_json(paths["control"] / "instance-assemblies.json", receipt)
        self.controller.verify_hashes("host", status)
        receipt["runNonce"] = "old-run"
        control.atomic_json(paths["control"] / "instance-assemblies.json", receipt)
        with self.assertRaisesRegex(RuntimeError, "another process or run"):
            self.controller.verify_hashes("host", status)

    def test_owned_reinstall_is_finalized_after_verified_copy_without_resetting_metadata(self):
        paths = self.controller.prepare("host")
        manifest = paths["user"] / "mods/mods.json"
        document = json.loads(manifest.read_text())
        entry = document["mods"][0]
        entry.update({"status": 3, "reinstall_path": str(paths["user"] / "mods/Local/OniHarness.Runtime"), "crash_count": 4})
        entry["label"]["version"] = 12345
        control.atomic_json(manifest, document)
        self.controller.prepare("host")
        installed = json.loads(manifest.read_text())["mods"][0]
        self.assertEqual(installed["status"], 1)  # Current native KMod.Mod.Status.Installed.
        self.assertIsNone(installed["reinstall_path"])
        self.assertEqual(installed["label"]["version"], 12345)
        self.assertEqual(installed["crash_count"], 4)
        self.assertEqual(installed["enabledForDlc"], ["EXPANSION1_ID"])
        entry["reinstall_path"] = str(self.base / "foreign.zip")
        control.atomic_json(manifest, document)
        with self.assertRaisesRegex(ValueError, "external native reinstall"):
            self.controller.prepare("host")

    def test_unplanned_pending_operations_are_not_erased_and_failed_copy_cannot_commit_install(self):
        paths = self.controller.prepare("host")
        manifest = paths["user"] / "mods/mods.json"
        document = json.loads(manifest.read_text())
        document["mods"].append({"label": {"id": "123456", "distribution_platform": 2},
                                 "status": 3, "enabled": False, "enabledForDlc": None})
        control.atomic_json(manifest, document)
        before = manifest.read_bytes()
        with self.assertRaisesRegex(ValueError, "Unplanned native mod operation"):
            self.controller.prepare("host")
        self.assertEqual(manifest.read_bytes(), before)
        document["mods"].pop()
        document["mods"][0].update({"status": 3, "reinstall_path": str(paths["user"] / "mods/Local/OniHarness.Runtime")})
        control.atomic_json(manifest, document)
        before = manifest.read_bytes()
        original = shutil.copy2

        def corrupt(source, destination, **arguments):
            original(source, destination, **arguments)
            if str(destination).endswith(".dll"):
                Path(destination).write_bytes(b"corrupt copied fixture")

        with patch.object(control.shutil, "copy2", side_effect=corrupt):
            with self.assertRaisesRegex(RuntimeError, "copy hash mismatch"):
                self.controller.prepare("host")
        self.assertEqual(manifest.read_bytes(), before)

    def test_memory_capacity_fails_without_launch(self):
        measured = {"violations": [], "system": {"MemAvailable": 4 * 1024 ** 2}, "totals": {"pssKiB": 0}}
        with patch.object(self.controller, "resources", return_value=measured):
            self.controller.preflight(1)
            with self.assertRaisesRegex(RuntimeError, "no sequential substitute"):
                self.controller.preflight(4)

    def native_checkpoint(self, name="host"):
        paths = self.controller.prepare(name)
        record = self.controller.state["instances"][name]
        origin = {"pid": 424242, "startTicks": 12345, "runNonce": "a" * 32}
        record.update({**origin, "nativeOrigins": [origin]})
        self.controller.save_state()
        checkpoint = paths["user"] / "save_files/OniOnlineHarness-424242/archive-source-7.sav"
        checkpoint.parent.mkdir(parents=True)
        checkpoint.write_bytes(b"native-generated-three-starter-checkpoint")
        return paths, checkpoint, control.digest(checkpoint)

    def test_owned_checkpoint_approval_survives_prepare_and_same_path_restart(self):
        paths, checkpoint, sha = self.native_checkpoint()
        original_bytes = checkpoint.read_bytes()
        self.controller.approve_owned_save("host", str(checkpoint), sha)
        self.controller.approve_owned_save("host", str(checkpoint), sha)
        self.assertEqual(len(self.controller.instances["host"]["ownedSaveApprovals"]), 1)
        # Every start/restart invokes prepare; recreate the daemon too.
        self.controller = control.Controller(self.args.root)
        with patch.object(self.controller, "start") as start:
            with self.assertRaisesRegex(RuntimeError, "Select one exact"):
                self.controller.reload("host")
            start.assert_not_called()
        for _ in range(2):
            self.controller.prepare("host")
            profile = json.loads((paths["control"] / "instance-profile.json").read_text())
            self.assertIn({"path": str(checkpoint), "sha256": sha}, profile["approvedSaves"])
            self.assertEqual(checkpoint.read_bytes(), original_bytes)
        with patch.object(self.controller, "live", return_value=True), \
             patch.object(self.controller, "command", return_value={}) as command, \
             patch.object(self.controller, "read_status", return_value={"gameLoaded": True, "paused": True,
                 "loadingSave": None, "activeSavePath": str(checkpoint), "ownedSave": str(checkpoint)}):
            self.assertTrue(self.controller.reload("host", str(checkpoint))["loaded"])
            self.assertEqual(command.call_args_list[-1].kwargs, {"path": str(checkpoint), "sha256": sha})

    def test_owned_approval_rejects_outside_wrong_origin_symlink_and_conflicting_hash(self):
        paths, checkpoint, sha = self.native_checkpoint()
        group_before = (self.args.root / "group.json").read_bytes()
        foreign = self.base / "foreign.sav"
        foreign.write_bytes(checkpoint.read_bytes())
        link = checkpoint.parent / "link.sav"
        link.symlink_to(foreign)
        wrong_origin = paths["user"] / "save_files/OniOnlineHarness-888888/other.sav"
        wrong_origin.parent.mkdir()
        wrong_origin.write_bytes(checkpoint.read_bytes())
        for path, requested in ((foreign, sha), (link, sha), (wrong_origin, sha), (checkpoint, "0" * 64),
                                (checkpoint.parent / ".." / checkpoint.parent.name / checkpoint.name, sha)):
            with self.subTest(path=path, requested=requested):
                with self.assertRaises((ValueError, RuntimeError)):
                    self.controller.approve_owned_save("host", str(path), requested)
                self.assertEqual((self.args.root / "group.json").read_bytes(), group_before)
        self.controller.approve_owned_save("host", str(checkpoint), sha)
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            self.controller.approve_owned_save("host", str(checkpoint), "0" * 64)

    def test_owned_checkpoint_tampering_blocks_prepare_and_reload_before_mutation_or_launch(self):
        paths, checkpoint, sha = self.native_checkpoint()
        self.controller.approve_owned_save("host", str(checkpoint), sha)
        sentinel = paths["user"] / "mods/Local/OniHarness.Runtime/retained.txt"
        sentinel.write_text("retain")
        before = (paths["user"] / "mods/mods.json").read_bytes()
        checkpoint.write_bytes(b"tampered")
        with self.assertRaisesRegex(RuntimeError, "SHA256 changed"):
            self.controller.prepare("host")
        self.assertEqual(sentinel.read_text(), "retain")
        self.assertEqual((paths["user"] / "mods/mods.json").read_bytes(), before)
        with patch.object(self.controller, "start") as start:
            with self.assertRaisesRegex(RuntimeError, "SHA256 changed"):
                self.controller.reload("host", str(checkpoint))
            start.assert_not_called()

    def test_unapproved_or_live_checkpoint_cannot_change_profile_or_spawn(self):
        paths, checkpoint, sha = self.native_checkpoint()
        before = (paths["control"] / "instance-profile.json").read_bytes()
        with patch.object(self.controller, "start") as start:
            with self.assertRaisesRegex(ValueError, "not explicitly approved"):
                self.controller.reload("host", str(checkpoint))
            start.assert_not_called()
        with patch.object(self.controller, "chain_identity", return_value=object()):
            with self.assertRaisesRegex(RuntimeError, "stopped"):
                self.controller.approve_owned_save("host", str(checkpoint), sha)
        self.assertEqual((paths["control"] / "instance-profile.json").read_bytes(), before)

    def test_owned_checkpoint_approvals_have_explicit_combined_limit_and_no_duplicate_paths(self):
        _, checkpoint, sha = self.native_checkpoint()
        self.controller.approve_owned_save("host", str(checkpoint), sha)
        configured = self.controller.instances["host"]["ownedSaveApprovals"]
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            candidate = json.loads(json.dumps(self.controller.config))
            candidate["instances"][0]["ownedSaveApprovals"] *= 2
            control.validate_config(candidate)
        for index in range(1, 64):
            path = checkpoint.parent / (str(index) + ".sav")
            path.write_bytes(checkpoint.read_bytes())
            configured.append({**configured[0], "path": str(path)})
        self.controller.state["instances"]["host"]["checkpointApprovals"] = configured[:]
        with self.assertRaisesRegex(ValueError, "Combined"):
            self.controller.prepare("host")

    def test_config_only_forged_checkpoint_provenance_is_rejected(self):
        paths, checkpoint, sha = self.native_checkpoint()
        config = json.loads((self.args.root / "group.json").read_text())
        item = {"path": str(checkpoint), "sha256": sha, "originPid": 424242,
                "originStartTicks": 12345, "originRunNonce": "a" * 32}
        config["instances"][0]["ownedSaveApprovals"] = [item]
        control.atomic_json(self.args.root / "group.json", config)
        # A known native origin alone does not issue a checkpoint approval.
        rebuilt = control.Controller(self.args.root)
        before = (paths["control"] / "instance-profile.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "independently recorded"):
            rebuilt.prepare("host")
        self.assertEqual((paths["control"] / "instance-profile.json").read_bytes(), before)
        rebuilt.state["instances"]["host"].pop("nativeOrigins")
        with self.assertRaisesRegex(ValueError, "owned native run"):
            rebuilt.approve_owned_save("host", str(checkpoint), sha)

    def test_source_deletion_between_enumerations_cannot_commit_an_incomplete_install(self):
        paths = self.controller.prepare("host")
        manifest = paths["user"] / "mods/mods.json"
        document = json.loads(manifest.read_text())
        document["mods"][0].update({"status": 3, "reinstall_path": str(paths["user"] / "mods/Local/OniHarness.Runtime")})
        control.atomic_json(manifest, document)
        before = manifest.read_bytes()
        original = self.controller.desired_files

        def desired_then_delete_source():
            planned = original()
            (self.args.companion / "OniHarness.Runtime.dll").unlink()
            return planned

        with patch.object(self.controller, "desired_files", side_effect=desired_then_delete_source):
            with self.assertRaisesRegex(RuntimeError, "inventory mismatch"):
                self.controller.prepare("host")
        self.assertEqual(manifest.read_bytes(), before)
        self.assertEqual(json.loads(manifest.read_text())["mods"][0]["status"], 3)

    def test_redirected_writable_ancestors_preserve_foreign_files_and_manifest(self):
        for relative in ("mods/Local", "mods/Local/OniHarness.Runtime", "save_files", "save_files/OniHarness/test.sav"):
            with self.subTest(relative=relative):
                paths = self.controller.prepare("host")
                manifest = paths["user"] / "mods/mods.json"
                document = json.loads(manifest.read_text())
                foreign = self.base / ("foreign-" + relative.replace("/", "-"))
                foreign.mkdir()
                sentinel = foreign / "sentinel"
                sentinel.write_text("never modify outside the owned instance")
                redirected = paths["user"] / relative
                if redirected.is_dir():
                    shutil.rmtree(redirected)
                    redirected.symlink_to(foreign, target_is_directory=True)
                else:
                    redirected.unlink()
                    redirected.symlink_to(sentinel)
                document["mods"][0].update({"status": 3, "reinstall_path": str(paths["user"] / "mods/Local/OniHarness.Runtime")})
                control.atomic_json(manifest, document)
                before = manifest.read_bytes()
                with self.assertRaisesRegex(ValueError, "symlink"):
                    self.controller.prepare("host", fresh_save=True)
                self.assertEqual(sentinel.read_text(), "never modify outside the owned instance")
                self.assertEqual(manifest.read_bytes(), before)
                redirected.unlink()

    def test_cross_instance_and_control_redirections_are_rejected_before_prepare(self):
        host = self.controller.prepare("host")
        guest = self.controller.prepare("guest1")
        for path, target in ((host["control"], guest["control"]), (host["base"] / "xdg", guest["base"] / "xdg")):
            with self.subTest(path=path):
                before = (guest["control"] / "instance-profile.json").read_bytes()
                shutil.rmtree(path)
                path.symlink_to(target, target_is_directory=True)
                with self.assertRaisesRegex(ValueError, "symlink"):
                    self.controller.prepare("host", fresh_save=True)
                self.assertEqual((guest["control"] / "instance-profile.json").read_bytes(), before)
                path.unlink()
                self.controller.prepare("host")

    def test_never_launch_over_another_tasks_game(self):
        with patch.object(self.controller, "live", return_value=False), \
             patch.object(self.controller, "owned_identities", return_value={}), \
             patch.object(control, "game_processes", return_value={987: {"arguments": []}}), \
             patch.object(self.controller, "prepare") as prepare:
            with self.assertRaisesRegex(RuntimeError, "Another task"):
                self.controller.start("host")
            prepare.assert_not_called()

    def test_no_official_wrapper_means_no_steam_launch(self):
        with patch.object(control, "steam_launch_options", return_value=[""]), \
             patch.object(control.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(RuntimeError, "Launch Options"):
                self.controller.ensure_launcher(1)
            launch.assert_not_called()

    def test_owned_launch_request_is_reaped_before_resource_sampling(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        self.controller.launch_requests.append(child)
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                # Observe kernel exit without poll/wait, reproducing the zombie
                # that previously blocked a subsequent game's memory admission.
                path = Path("/proc") / str(child.pid) / "stat"
                if path.exists() and path.read_text().rsplit(")", 1)[1].split()[0] == "Z":
                    break
                time.sleep(.01)
            else:
                self.fail("Owned process fixture did not exit")
            resource = self.controller.resources()
            self.assertEqual(self.controller.launch_requests, [])
            self.assertEqual(child.returncode, 0)
            self.assertFalse((Path("/proc") / str(child.pid)).exists())
            self.assertFalse(any("process_zombie" in error for error in resource["violations"]))
        finally:
            child.wait(timeout=5)

    def test_previous_chain_exit_status_cannot_abort_a_new_start(self):
        status = {"pid": 123, "runNonce": "new-native"}
        stale = {"pid": 555, "children": {"guest1": {"pid": 888, "exitCode": 0}}}
        with patch.object(self.controller, "live", return_value=False), \
             patch.object(self.controller, "chain_identity", return_value=None), \
             patch.object(control, "game_processes", return_value={}), \
             patch.object(self.controller, "preflight"), \
             patch.object(self.controller, "ensure_launcher", return_value={"pid": 555}), \
             patch.object(self.controller, "launcher_command", return_value={"pid": 999}), \
             patch.object(control.ProcessIdentity, "capture", side_effect=lambda pid: control.ProcessIdentity(pid, 20)), \
             patch.object(self.controller, "observe_native_pid"), \
             patch.object(self.controller, "read_status", side_effect=[OSError("not ready yet"), status]) as read, \
             patch.object(self.controller, "launcher_status", return_value=stale), \
             patch.object(self.controller, "verify_hashes", return_value={}), \
             patch.object(control.time, "sleep"):
            result = self.controller.start("guest1")
        self.assertFalse(result["reused"])
        self.assertEqual(result["status"]["pid"], 123)
        self.assertEqual(read.call_count, 2)

    def test_interrupted_start_recovers_its_confirmed_child_without_deploy(self):
        self.controller.prepare("guest1")
        record = self.controller.state["instances"]["guest1"]
        record.update({"steamChainPid": 999, "steamChainStartTicks": 20, "pid": None})
        status = {"pid": 123, "runNonce": "recovered"}
        paths = self.controller.paths("guest1")
        process = {123: {"executable": str(paths["installation"] / "OxygenNotIncluded"), "arguments": [str(paths["control"])]}}
        with patch.object(self.controller, "chain_identity", return_value=control.ProcessIdentity(999, 20)), \
             patch.object(self.controller, "read_status", return_value=status), \
             patch.object(control.ProcessIdentity, "capture", return_value=control.ProcessIdentity(123, 10)), \
             patch.object(control, "descendant_of", return_value=True), \
             patch.object(control, "game_processes", return_value=process), \
             patch.object(self.controller, "verify_hashes", return_value={}), \
             patch.object(self.controller, "prepare") as deploy, \
             patch.object(self.controller, "launcher_command") as launch:
            result = self.controller.start("guest1")
        self.assertTrue(result["reused"])
        self.assertEqual(record["pid"], 123)
        self.assertEqual(record["runNonce"], "recovered")
        deploy.assert_not_called()
        launch.assert_not_called()

    def test_native_pid_is_adopted_separately_from_the_runtime_chain(self):
        self.controller.prepare("guest1")
        record = self.controller.state["instances"]["guest1"]
        record.update({"pid": None, "steamChainPid": 999, "steamChainStartTicks": 20})
        with patch.object(self.controller, "read_status", return_value={"pid": 123, "runNonce": "native-run"}), \
             patch.object(self.controller, "chain_identity", return_value=control.ProcessIdentity(999, 20)), \
             patch.object(control, "descendant_of", return_value=True), \
             patch.object(control.ProcessIdentity, "capture", return_value=control.ProcessIdentity(123, 10)):
            self.controller.observe_native_pid("guest1")
        self.assertEqual(record["pid"], 123)
        self.assertEqual(record["startTicks"], 10)
        self.assertEqual(record["steamChainPid"], 999)
        self.assertEqual(record["runNonce"], "native-run")
        with patch.object(control.ProcessIdentity, "capture", return_value=control.ProcessIdentity(999, 21)):
            self.assertIsNone(self.controller.chain_identity("guest1"))
        with patch.object(control.ProcessIdentity, "capture", return_value=control.ProcessIdentity(999, 20)):
            self.assertEqual(self.controller.chain_identity("guest1").pid, 999)

    def test_cleanup_only_native_adoption_cannot_issue_checkpoint_provenance(self):
        paths = self.controller.prepare("host")
        record = self.controller.state["instances"]["host"]
        data = {"pid": 123, "runNonce": "b" * 32}

        def status(_name, cleanup=False):
            if not cleanup:
                raise RuntimeError("Actual native user root is not isolated")
            return data

        with patch.object(self.controller, "read_status", side_effect=status), \
             patch.object(self.controller, "chain_identity", return_value=control.ProcessIdentity(999, 20)), \
             patch.object(control, "descendant_of", return_value=True), \
             patch.object(control.ProcessIdentity, "capture", return_value=control.ProcessIdentity(123, 10)):
            self.controller.observe_native_pid("host")
        self.assertEqual(record["pid"], 123)  # Retain its orderly cleanup route.
        self.assertEqual(record.get("nativeOrigins", []), [])
        checkpoint = paths["user"] / "save_files/OniOnlineHarness-123/fake.sav"
        checkpoint.parent.mkdir(parents=True)
        checkpoint.write_bytes(b"not a native-proven checkpoint")
        with self.assertRaisesRegex(ValueError, "owned native run"):
            self.controller.approve_owned_save("host", str(checkpoint), control.digest(checkpoint))
        with patch.object(self.controller, "read_status", return_value=data), \
             patch.object(self.controller, "verify_hashes", return_value={}), \
             patch.object(self.controller, "chain_identity", return_value=control.ProcessIdentity(999, 20)), \
             patch.object(control, "descendant_of", return_value=True), \
             patch.object(control.ProcessIdentity, "capture", return_value=control.ProcessIdentity(123, 10)):
            self.controller.observe_native_pid("host")
        self.assertEqual(record["nativeOrigins"], [{"pid": 123, "startTicks": 10, "runNonce": "b" * 32}])

    def test_pending_chain_is_not_reported_as_an_already_stopped_game(self):
        with patch.object(self.controller, "live", return_value=False), \
             patch.object(self.controller, "chain_identity", return_value=control.ProcessIdentity(123, 20)):
            with self.assertRaisesRegex(RuntimeError, "still alive"):
                self.controller.stop("guest1")

    def failed_stop_tail(self, other_live=False, launch_request=None, observed=None):
        self.controller.prepare("host")
        self.controller.state["instances"]["host"].update({"pid": 123, "startTicks": 10, "runNonce": "a" * 32,
                                                            "steamChainPid": 999, "steamChainStartTicks": 20})
        if other_live:
            self.controller.state["instances"]["guest1"] = {"pid": 888, "startTicks": 88}
        supervisor = {"pid": 555, "nonce": "b" * 32, "children": {"host": {"pid": 999, "exitCode": 0}}}
        control.atomic_json(self.args.root / "launcher-status.json", supervisor)
        if launch_request:
            self.controller.launch_requests.append(launch_request)
        error = control.NativeExitTimeout("original native quit timeout", "host",
            {"host": control.ProcessIdentity(123, 10), "reparented-child": control.ProcessIdentity(321, 30)},
            [control.ProcessIdentity(123, 10), control.ProcessIdentity(999, 20)],
            {"id": "original-quit", "nonce": "a" * 32, "state": "accepted"})
        with patch.object(self.controller, "launcher_status", return_value=supervisor), \
             patch.object(control.ProcessIdentity, "capture", side_effect=lambda pid: control.ProcessIdentity(pid, {123: 10, 999: 20, 888: 88, 555: 50}.get(pid, 70))), \
             patch.object(self.controller, "resource_identities", return_value=observed or {}), \
             patch.object(control, "descendant_of", side_effect=lambda pid, parent: pid == 666 and parent == 888), \
             patch.object(self.controller, "stop", side_effect=error):
            with self.assertRaises(control.NativeExitTimeout):
                self.controller.stop_all()
        tail = self.controller.state["stopAllTail"]
        return tail, supervisor, Path(tail["failureReceipt"]).read_bytes()

    def test_late_normal_exit_keeps_failure_and_shuts_only_original_supervisor_once(self):
        tail, supervisor, failed = self.failed_stop_tail()
        alive = {555}
        with patch.object(self.controller, "incarnation_gone", side_effect=lambda item: item["pid"] not in alive), \
             patch.object(self.controller, "launcher_status", return_value=supervisor), \
             patch.object(self.controller, "launcher_command", return_value={"ok": True}) as shutdown, \
             patch.object(self.controller, "command") as native_command:
            self.controller.reconcile_stop_tail()
            self.assertEqual(tail["phase"], "shutdown_acknowledged")
            self.controller.reconcile_stop_tail()  # ACK alone is not process exit.
            self.assertNotEqual(tail["phase"], "eventual_stopped")
            alive.clear()
            self.controller.reconcile_stop_tail()
            self.assertEqual(tail["phase"], "eventual_stopped")
            self.assertEqual(tail["originalOutcome"], "FAILED")
            shutdown.assert_called_once_with("shutdown", expected_supervisor=tail["supervisor"])
            native_command.assert_not_called()
        self.assertEqual(Path(tail["failureReceipt"]).read_bytes(), failed)

    def test_late_cleanup_waits_for_retained_reparented_child_and_other_roles(self):
        tail, supervisor, _ = self.failed_stop_tail(other_live=True)
        alive = {321, 888, 555}
        with patch.object(self.controller, "incarnation_gone", side_effect=lambda item: item["pid"] not in alive), \
             patch.object(self.controller, "launcher_status", return_value=supervisor), \
             patch.object(self.controller, "launcher_command", return_value={"ok": True}) as shutdown:
            self.controller.reconcile_stop_tail()
            alive.remove(321)
            self.controller.reconcile_stop_tail()
            shutdown.assert_not_called()
            alive.remove(888)
            self.controller.reconcile_stop_tail()
            shutdown.assert_called_once()
        self.assertEqual(tail["phase"], "shutdown_acknowledged")

    def test_late_cleanup_tracks_new_descendant_before_original_root_exits(self):
        tail, supervisor, _ = self.failed_stop_tail()
        alive = {123, 666, 555}
        with patch.object(self.controller, "incarnation_gone", side_effect=lambda item: item["pid"] not in alive), \
             patch.object(self.controller, "resource_identities", return_value={"new-child": control.ProcessIdentity(666, 60)}), \
             patch.object(control, "descendant_of", return_value=True), \
             patch.object(self.controller, "launcher_status", return_value=supervisor), \
             patch.object(self.controller, "launcher_command") as shutdown:
            self.controller.reconcile_stop_tail()
            self.assertIn({"pid": 666, "startTicks": 60}, tail["retained"].values())
            alive.remove(123)
            self.controller.reconcile_stop_tail()
            shutdown.assert_not_called()

    def test_late_cleanup_retains_other_role_descendants_before_their_parent_exits(self):
        for initial in (False, True):
            with self.subTest(observedAtFailure=initial):
                observed = {"guest-child": control.ProcessIdentity(666, 60)}
                tail, supervisor, _ = self.failed_stop_tail(other_live=True, observed=observed if initial else None)
                alive = {888, 666, 555}
                with patch.object(self.controller, "incarnation_gone", side_effect=lambda item: item["pid"] not in alive), \
                     patch.object(self.controller, "resource_identities", return_value=observed), \
                     patch.object(control, "descendant_of", side_effect=lambda pid, parent: pid == 666 and parent == 888), \
                     patch.object(self.controller, "launcher_status", return_value=supervisor), \
                     patch.object(self.controller, "launcher_command", return_value={"ok": True}) as shutdown:
                    if not initial:
                        self.controller.reconcile_stop_tail()
                    alive.remove(888)
                    self.controller.reconcile_stop_tail()
                    self.assertIn({"pid": 666, "startTicks": 60}, tail["retained"].values())
                    shutdown.assert_not_called()
                    alive.remove(666)
                    self.controller.reconcile_stop_tail()
                    shutdown.assert_called_once()

    def test_late_cleanup_restores_pending_state_without_repeating_ambiguous_shutdown(self):
        tail, supervisor, failed = self.failed_stop_tail()
        with patch.object(self.controller, "incarnation_gone", side_effect=lambda item: item["pid"] != 555), \
             patch.object(self.controller, "launcher_status", return_value=supervisor), \
             patch.object(self.controller, "launcher_command", side_effect=RuntimeError("ACK unknown")) as shutdown:
            self.controller.reconcile_stop_tail()
            shutdown.assert_called_once()
        self.controller = control.Controller(self.args.root)
        with patch.object(self.controller, "incarnation_gone", side_effect=lambda item: item["pid"] != 555), \
             patch.object(self.controller, "launcher_status", return_value=supervisor), \
             patch.object(self.controller, "launcher_command") as shutdown:
            self.controller.reconcile_stop_tail()
            shutdown.assert_not_called()
        self.assertEqual(self.controller.state["stopAllTail"]["phase"], "shutdown_requested")
        self.assertEqual(Path(tail["failureReceipt"]).read_bytes(), failed)

    def test_late_cleanup_defers_when_root_changes_during_descendant_sampling(self):
        tail, supervisor, _ = self.failed_stop_tail()
        calls = []
        def gone(item):
            if item["pid"] == 123:
                calls.append(123)
                return len(calls) > 1
            return item["pid"] != 555
        with patch.object(self.controller, "incarnation_gone", side_effect=gone), \
             patch.object(self.controller, "resource_identities", return_value={"child": control.ProcessIdentity(666, 60)}), \
             patch.object(control, "descendant_of", return_value=True), \
             patch.object(self.controller, "launcher_status", return_value=supervisor), \
             patch.object(self.controller, "launcher_command") as shutdown:
            self.controller.reconcile_stop_tail()
            self.assertIn("uncertainDescendantObservation", tail)
            self.controller.reconcile_stop_tail()
            shutdown.assert_not_called()

    def test_late_cleanup_defers_when_failure_snapshot_root_changes(self):
        calls = []
        def gone(item):
            if item["pid"] == 888:
                calls.append(888)
                return len(calls) > 1
            return True
        with patch.object(self.controller, "incarnation_gone", side_effect=gone):
            tail, _, _ = self.failed_stop_tail(other_live=True, observed={"guest-child": control.ProcessIdentity(666, 60)})
        self.assertIn("root changed", tail["initialObservationError"])
        self.controller = control.Controller(self.args.root)
        with patch.object(self.controller, "incarnation_gone", return_value=True), \
             patch.object(self.controller, "launcher_command") as shutdown:
            self.controller.reconcile_stop_tail()
            shutdown.assert_not_called()

    def test_late_cleanup_persists_descendant_post_sample_identity_errors(self):
        for failure in (PermissionError("denied"), ValueError("bad stat")):
            with self.subTest(error=failure):
                tail, _, _ = self.failed_stop_tail()
                calls = []
                def gone(item):
                    if item["pid"] == 123:
                        calls.append(123)
                        if len(calls) > 1:
                            raise failure
                        return False
                    return True
                with patch.object(self.controller, "incarnation_gone", side_effect=gone), \
                     patch.object(self.controller, "resource_identities", return_value={"child": control.ProcessIdentity(666, 60)}), \
                     patch.object(control, "descendant_of", return_value=True):
                    self.controller.reconcile_stop_tail()
                self.assertIn("uncertainDescendantObservation", tail)
                self.controller = control.Controller(self.args.root)
                with patch.object(self.controller, "incarnation_gone", return_value=True), \
                     patch.object(self.controller, "launcher_command") as shutdown:
                    self.controller.reconcile_stop_tail()
                    shutdown.assert_not_called()
        self.controller = control.Controller(self.args.root)
        with patch.object(self.controller, "incarnation_gone", return_value=True), \
             patch.object(self.controller, "launcher_command") as shutdown:
            self.controller.reconcile_stop_tail()
            shutdown.assert_not_called()

    def test_late_cleanup_does_not_treat_identity_read_failures_as_exit(self):
        tail, _, _ = self.failed_stop_tail()
        for failure in (PermissionError("denied"), ValueError("bad stat")):
            with self.subTest(failure=failure), \
                 patch.object(control.ProcessIdentity, "capture", side_effect=failure), \
                 patch.object(self.controller, "launcher_command") as shutdown:
                self.controller.reconcile_stop_tail()
                shutdown.assert_not_called()
                self.assertEqual(tail["phase"], "waiting_for_exit")
        # PID reuse proves the OLD incarnation gone without operating the new PID.
        with patch.object(control.ProcessIdentity, "capture", return_value=control.ProcessIdentity(123, 11)):
            self.assertTrue(self.controller.incarnation_gone({"pid": 123, "startTicks": 10}))
        with patch.object(control.ProcessIdentity, "capture", side_effect=FileNotFoundError()), \
             patch.object(control.os, "stat", return_value=object()):
            with self.assertRaisesRegex(RuntimeError, "unreadable"):
                self.controller.incarnation_gone({"pid": 123, "startTicks": 10})

    def test_late_cleanup_binds_supervisor_nonce_and_cancels_on_new_generation(self):
        tail, supervisor, failed = self.failed_stop_tail()
        with patch.object(self.controller, "incarnation_gone", side_effect=lambda item: item["pid"] != 555), \
             patch.object(self.controller, "launcher_status", return_value={**supervisor, "nonce": "c" * 32}), \
             patch.object(self.controller, "launcher_command") as shutdown:
            self.controller.reconcile_stop_tail()
            self.assertEqual(tail["phase"], "superseded")
            shutdown.assert_not_called()
        tail["phase"] = "waiting_for_exit"
        self.controller.state["instances"]["host"]["startTicks"] += 1
        self.controller.reconcile_stop_tail()
        self.assertEqual(tail["phase"], "superseded")
        self.assertEqual(Path(tail["failureReceipt"]).read_bytes(), failed)

    def test_late_cleanup_blocks_pending_child_and_bounds_observation_without_signals(self):
        tail, supervisor, _ = self.failed_stop_tail()
        with patch.object(self.controller, "incarnation_gone", side_effect=lambda item: item["pid"] != 555), \
             patch.object(self.controller, "launcher_status", return_value={**supervisor, "children": {"guest1": {"pid": 888, "exitCode": None}}}), \
             patch.object(self.controller, "launcher_command") as shutdown, \
             patch.object(self.controller, "command") as native:
            self.controller.reconcile_stop_tail()
            shutdown.assert_not_called()
            tail["observeUntilUnix"] = 0
            self.controller.reconcile_stop_tail()
            self.assertEqual(tail["phase"], "manual_cleanup_required")
            native.assert_not_called()

    def test_late_cleanup_persists_inflight_owned_launch_request_across_daemon_restart(self):
        class OwnRequest:
            pid = 777
            def poll(self): return None
        tail, supervisor, _ = self.failed_stop_tail(launch_request=OwnRequest())
        self.assertEqual(tail["launchRequests"], [{"pid": 777, "startTicks": 70}])
        self.controller = control.Controller(self.args.root)
        with patch.object(self.controller, "incarnation_gone", side_effect=lambda item: item["pid"] not in (555, 777)), \
             patch.object(self.controller, "launcher_status", return_value=supervisor), \
             patch.object(self.controller, "launcher_command") as shutdown:
            self.controller.reconcile_stop_tail()
            shutdown.assert_not_called()

    def test_bound_shutdown_refuses_supervisor_change_before_writing_any_request(self):
        expected = {"pid": 555, "startTicks": 50, "nonce": "b" * 32}
        for status, ticks in (({"pid": 555, "nonce": "b" * 32}, 51), ({"pid": 555, "nonce": "c" * 32}, 50)):
            with patch.object(self.controller, "launcher_status", return_value=status), \
                 patch.object(control.ProcessIdentity, "capture", return_value=control.ProcessIdentity(555, ticks)):
                with self.assertRaisesRegex(RuntimeError, "Supervisor changed"):
                    self.controller.launcher_command("shutdown", expected_supervisor=expected)
            self.assertFalse((self.args.root / "launcher-command.json").exists())

    def test_native_stop_never_reports_exit_when_retained_identity_is_unreadable(self):
        self.controller.state["instances"]["host"] = {"pid": 123, "startTicks": 10}
        with patch.object(self.controller, "live", side_effect=[True, False, False]), \
             patch.object(self.controller, "chain_identity", return_value=None), \
             patch.object(self.controller, "resource_identities", return_value={"host": control.ProcessIdentity(123, 10)}), \
             patch.object(control, "descendant_of", return_value=True), \
             patch.object(self.controller, "command", return_value={"state": "accepted"}), \
             patch.object(self.controller, "incarnation_gone", side_effect=PermissionError("denied")):
            with self.assertRaises(control.NativeExitTimeout):
                self.controller.stop("host", timeout=0)

    def test_pending_chain_prevents_prepare_and_preserves_ownership(self):
        paths = self.controller.prepare("guest1")
        record = self.controller.state["instances"]["guest1"]
        record.update({"steamChainPid": 999, "steamChainStartTicks": 20})
        deployed = paths["user"] / "mods/Local/OniHarness.Runtime/OniHarness.Runtime.dll"
        before = deployed.read_bytes()
        with patch.object(self.controller, "live", return_value=False), \
             patch.object(self.controller, "chain_identity", return_value=control.ProcessIdentity(999, 20)):
            with self.assertRaisesRegex(RuntimeError, "chain is alive"):
                self.controller.prepare("guest1", fresh_save=True)
            with self.assertRaisesRegex(RuntimeError, "pending or failed"):
                self.controller.start("guest1")
        self.assertEqual(deployed.read_bytes(), before)
        self.assertEqual(self.controller.state["instances"]["guest1"]["steamChainPid"], 999)
        self.assertEqual(self.controller.state["instances"]["guest1"]["steamChainStartTicks"], 20)

    def test_first_adoption_never_uses_cleanup_to_claim_a_foreign_pid(self):
        paths = self.controller.prepare("guest1")
        record = self.controller.state["instances"]["guest1"]
        record.update({"steamChainPid": 999, "steamChainStartTicks": 20})
        status = {"capturedAt": control.utcnow(), "identity": self.controller.instances["guest1"]["identity"],
                  "runNonce": "foreign-run", "pid": 123, "rootVerified": True, "nativeUserRoot": str(paths["user"]),
                  "batchMode": True, "graphicsDevice": "Null", "cameraRenderCallbacks": 0}
        control.atomic_json(paths["control"] / "instance-status.json", status)
        process = {123: {"executable": str(paths["installation"] / "OxygenNotIncluded"), "arguments": [str(paths["control"])]}}
        with patch.object(control, "game_processes", return_value=process), \
             patch.object(self.controller, "chain_identity", return_value=control.ProcessIdentity(999, 20)), \
             patch.object(control, "descendant_of", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "not a descendant"):
                self.controller.read_status("guest1", cleanup=True)
            self.controller.observe_native_pid("guest1")
        self.assertIsNone(record.get("pid"))
        self.assertNotIn("runNonce", record)

    def test_cleanup_exception_requires_confirmed_pid_ticks_and_nonce(self):
        paths = self.controller.prepare("guest1")
        record = self.controller.state["instances"]["guest1"]
        record.update({"steamChainPid": 999, "steamChainStartTicks": 20, "pid": 123, "startTicks": 10, "runNonce": "confirmed"})
        status = {"capturedAt": control.utcnow(), "identity": self.controller.instances["guest1"]["identity"],
                  "runNonce": "confirmed", "pid": 123, "rootVerified": False, "error": "failed root isolation"}
        control.atomic_json(paths["control"] / "instance-status.json", status)
        process = {123: {"executable": str(paths["installation"] / "OxygenNotIncluded"), "arguments": [str(paths["control"])]}}
        with patch.object(control, "game_processes", return_value=process), \
             patch.object(self.controller, "chain_identity", return_value=None), \
             patch.object(control.ProcessIdentity, "capture", return_value=control.ProcessIdentity(123, 10)):
            self.assertEqual(self.controller.read_status("guest1", cleanup=True)["pid"], 123)
            record["runNonce"] = "different-run"
            with self.assertRaisesRegex(RuntimeError, "not a descendant"):
                self.controller.read_status("guest1", cleanup=True)
            record["runNonce"] = "confirmed"
            record["startTicks"] = 11
            with self.assertRaisesRegex(RuntimeError, "not a descendant"):
                self.controller.read_status("guest1", cleanup=True)

    def test_steam_options_do_not_use_an_inactive_accounts_wrapper(self):
        home = self.base / "fake-home"
        steam = home / ".local/share/Steam"
        (steam / "config").mkdir(parents=True)
        first = 76561197960265729
        second = 76561197960265730
        (steam / "config/loginusers.vdf").write_text(f'"users" {{ "{first}" {{ "MostRecent" "1" }} "{second}" {{ "MostRecent" "0" }} }}')
        for account, option in ((1, ""), (2, "inactive-wrapper %command%")):
            local = steam / f"userdata/{account}/config/localconfig.vdf"
            local.parent.mkdir(parents=True)
            local.write_text('"UserLocalConfigStore" { "Software" { "Valve" { "Steam" { "apps" { "457140" { "LaunchOptions" "' + option + '" } } } } } }')
        with patch.object(control.Path, "home", return_value=home):
            self.assertEqual(control.steam_launch_options(), [""])

    def test_stale_telemetry_allows_only_owned_pause_quit_and_never_first_adoption(self):
        paths = self.controller.prepare("guest1")
        record = self.controller.state["instances"]["guest1"]
        record.update({"steamChainPid": 999, "steamChainStartTicks": 20, "pid": 123, "startTicks": 10, "runNonce": "confirmed"})
        status = {"capturedAt": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
                  "identity": self.controller.instances["guest1"]["identity"], "pid": 123, "runNonce": "confirmed"}
        control.atomic_json(paths["control"] / "instance-status.json", status)
        process = {123: {"executable": str(paths["installation"] / "OxygenNotIncluded"), "arguments": [str(paths["control"])]}}
        capture = lambda pid: control.ProcessIdentity(pid, 10 if pid == 123 else 20)
        original_write = control.atomic_json

        def acknowledge(path, value):
            original_write(path, value)
            if path.name == "instance-command.json":
                original_write(paths["control"] / "instance-response.json", {
                    "id": value["id"], "instance": value["instance"], "nonce": value["nonce"], "state": "completed"})

        with patch.object(control, "game_processes", return_value=process), \
             patch.object(control.ProcessIdentity, "capture", side_effect=capture), \
             patch.object(control, "descendant_of", return_value=True), \
             patch.object(control, "atomic_json", side_effect=acknowledge):
            for action in ("pause", "quit"):
                self.assertEqual(self.controller.command("guest1", action)["state"], "completed")
            command = (paths["control"] / "instance-command.json").read_bytes()
            for action in ("step", "load"):
                with self.assertRaisesRegex(RuntimeError, "stale"):
                    self.controller.command("guest1", action)
                self.assertEqual((paths["control"] / "instance-command.json").read_bytes(), command)
            record.pop("pid")
            with self.assertRaisesRegex(RuntimeError, "stale"):
                self.controller.read_status("guest1", cleanup=True)
            self.controller.observe_native_pid("guest1")
            self.assertNotIn("pid", record)

    def test_stale_cleanup_rejects_changed_native_or_chain_incarnations(self):
        paths = self.controller.prepare("guest1")
        record = self.controller.state["instances"]["guest1"]
        record.update({"steamChainPid": 999, "steamChainStartTicks": 20, "pid": 123, "startTicks": 10, "runNonce": "confirmed"})
        original = {"capturedAt": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
                    "identity": self.controller.instances["guest1"]["identity"], "pid": 123, "runNonce": "confirmed"}
        process = {123: {"executable": str(paths["installation"] / "OxygenNotIncluded"), "arguments": [str(paths["control"])]}}
        cases = [(11, 20, True, "confirmed"), (10, 21, True, "confirmed"),
                 (10, 20, False, "confirmed"), (10, 20, True, "other-run")]
        for native_ticks, chain_ticks, descendant, nonce in cases:
            control.atomic_json(paths["control"] / "instance-status.json", {**original, "runNonce": nonce})
            with self.subTest(case=(native_ticks, chain_ticks, descendant, nonce)), \
                 patch.object(control, "game_processes", return_value=process), \
                 patch.object(control.ProcessIdentity, "capture", side_effect=lambda pid: control.ProcessIdentity(pid, native_ticks if pid == 123 else chain_ticks)), \
                 patch.object(control, "descendant_of", return_value=descendant):
                with self.assertRaisesRegex(RuntimeError, "stale"):
                    self.controller.read_status("guest1", cleanup=True)
        control.atomic_json(paths["control"] / "instance-status.json", {
            **original, "capturedAt": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()})
        with patch.object(control, "game_processes", return_value=process), \
             patch.object(control.ProcessIdentity, "capture", side_effect=lambda pid: control.ProcessIdentity(pid, 10 if pid == 123 else 20)), \
             patch.object(control, "descendant_of", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "stale"):
                self.controller.read_status("guest1", cleanup=True)

    def test_status_rejects_cross_control_root_and_stale_nonce(self):
        paths = self.controller.prepare("guest1")
        status = {"capturedAt": control.utcnow(), "identity": self.controller.instances["guest1"]["identity"],
                  "runNonce": "nonce", "pid": 123, "rootVerified": True, "nativeUserRoot": str(paths["user"]),
                  "batchMode": True, "graphicsDevice": "Null", "cameraRenderCallbacks": 0}
        control.atomic_json(paths["control"] / "instance-status.json", status)
        with patch.object(control, "game_processes", return_value={123: {"executable": str(paths["installation"] / "OxygenNotIncluded"), "arguments": ["--oniharness-control", "foreign"]}}):
            with self.assertRaisesRegex(RuntimeError, "does not belong"):
                self.controller.read_status("guest1")
        with patch.object(control, "game_processes", return_value={123: {"executable": str(paths["installation"] / "OxygenNotIncluded"), "arguments": [str(paths["control"])]}}), \
             patch.object(self.controller, "chain_identity", return_value=control.ProcessIdentity(999, 20)), \
             patch.object(control, "descendant_of", return_value=True):
            self.assertEqual(self.controller.read_status("guest1")["pid"], 123)
            status["runNonce"] = ""
            control.atomic_json(paths["control"] / "instance-status.json", status)
            with self.assertRaisesRegex(RuntimeError, "nonce mismatch"):
                self.controller.read_status("guest1")

    def test_command_waits_for_completed_step_and_rejects_wrong_identity(self):
        paths = self.controller.prepare("host")
        identity = self.controller.instances["host"]["identity"]
        def responder():
            path = paths["control"] / "instance-command.json"
            while not path.exists():
                time.sleep(.005)
            request = json.loads(path.read_text())
            control.atomic_json(paths["control"] / "instance-response.json", {**request, "state": "running"})
            time.sleep(.1)
            control.atomic_json(paths["control"] / "instance-response.json", {**request, "state": "completed", "nativeSeconds": .2})
        thread = threading.Thread(target=responder)
        thread.start()
        try:
            with patch.object(self.controller, "read_status", return_value={"runNonce": "nonce"}):
                response = self.controller.command("host", "step", seconds=1)
                self.assertEqual(response["state"], "completed")
                self.assertEqual(response["instance"], identity)
        finally:
            thread.join(timeout=2)

    def test_concurrency_receipt_requires_overlapping_intervals(self):
        statuses = {"host": {"pid": 1, "gameLoaded": True, "paused": True, "simulationSeconds": 1},
                    "guest1": {"pid": 2, "gameLoaded": True, "paused": True, "simulationSeconds": 1}}
        calls = {}
        def read(name):
            row = dict(statuses[name])
            row["simulationSeconds"] += 1 if calls.get(name) else 0
            return row
        def native_double(name, action, **kwargs):
            start = datetime.now(timezone.utc)
            time.sleep(.15)
            calls[name] = True
            return {"startedAt": start.isoformat(), "completedAt": datetime.now(timezone.utc).isoformat()}
        with patch.object(self.controller, "read_status", side_effect=read), \
             patch.object(self.controller, "command", side_effect=native_double), \
             patch.object(self.controller, "preflight"), patch.object(self.controller, "resources", return_value={}):
            result = self.controller.verify_group(["host", "guest1"], .1)
            self.assertGreater(result["overlapSeconds"], .05)
            self.assertEqual(result["protocolClients"], 0)
        calls.clear()
        now = datetime.now(timezone.utc)
        def nonoverlap(name, action, **kwargs):
            calls[name] = True
            start = now if name == "host" else now + timedelta(seconds=3)
            return {"startedAt": start.isoformat(), "completedAt": (start + timedelta(seconds=1)).isoformat()}
        with patch.object(self.controller, "read_status", side_effect=read), \
             patch.object(self.controller, "command", side_effect=nonoverlap), patch.object(self.controller, "preflight"):
            with self.assertRaisesRegex(RuntimeError, "sequential execution"):
                self.controller.verify_group(["host", "guest1"], .1)

    def test_resident_daemon_repeated_requests_and_exit_cleanup(self):
        process = subprocess.Popen([sys.executable, str(ROOT / "scripts/instance_controller.py"), "--root", str(self.args.root), "serve"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        endpoint = self.args.root / "controller.sock"
        try:
            deadline = time.monotonic() + 5
            while not endpoint.exists() and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertTrue(endpoint.exists())
            for _ in range(3):
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                    connection.settimeout(2)
                    connection.connect(str(endpoint))
                    connection.sendall(b'{"action":"status"}\n')
                    result = json.loads(connection.makefile("rb").readline())
                    self.assertTrue(result["ok"])
                    self.assertEqual(len(result["result"]["instances"]), 4)
                    self.assertEqual(result["result"]["protocolClients"], 0)
            self.assertIsNone(process.poll())
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(2)
                connection.connect(str(endpoint))
                connection.sendall(b'{"action":"daemon-stop"}\n')
                result = json.loads(connection.makefile("rb").readline())
                self.assertTrue(result["result"]["controllerStopping"])
            process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0)
        finally:
            if process.poll() is None:
                process.send_signal(2)
                process.communicate(timeout=5)
        self.assertFalse(endpoint.exists())

    def test_wrapper_refuses_non_steam_parent_and_never_executes(self):
        with patch.object(wrapper, "steam_ancestor", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "authorized Steam"):
                wrapper.prepare([str(self.args.game_path / "OxygenNotIncluded")], {})

    def test_wrapper_isolates_xdg_affinity_without_touching_home(self):
        paths = self.controller.prepare("host")
        command = [str(paths["installation"] / "OxygenNotIncluded"), "--oniharness-control", str(paths["control"]),
                   "--oniharness-instance", self.controller.instances["host"]["identity"],
                   "--oniharness-user-root", str(paths["user"])]
        with patch.object(wrapper, "steam_ancestor", return_value=True), \
             patch.object(wrapper.os, "sched_getaffinity", return_value={2, 3, 4, 5}), \
             patch.object(wrapper.os, "sched_setaffinity") as affinity:
            environment, (_, receipt) = wrapper.prepare(command, {"HOME": "/unchanged"})
            self.assertEqual(environment["HOME"], "/unchanged")
            self.assertEqual(environment["XDG_CONFIG_HOME"], str(paths["base"] / "xdg"))
            self.assertEqual(receipt["cpuAffinity"], [2, 3])
            affinity.assert_called_once_with(0, [2, 3])

    def _run_supervisor_fixture(self, use_runtime_prefix):
        # A system true executable is a labelled process fixture, never an ONI
        # client. Only this test replaces Steam ancestry; production does not.
        prefix = []
        trace = self.base / "runtime-chain.trace"
        if use_runtime_prefix:
            steam = self.base / "fake-steam"
            game = steam / "steamapps/common/OxygenNotIncluded"
            game.mkdir(parents=True)
            (game / "OxygenNotIncluded_Data").mkdir()
            (game / "OxygenNotIncluded_Data/boot.config").write_text("single-instance=\n")
            self.args.game_path = game
            self.controller.config["gamePath"] = str(game)
            control.atomic_json(self.args.root / "group.json", self.controller.config)
            paths = [steam / "steamrt64/steam-launch-wrapper", steam / "steamrt64/reaper",
                     steam / "steamapps/common/SteamLinuxRuntime_sniper/_v2-entry-point"]
            for path, offset in zip(paths, (2, 4, 3)):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f'#!{sys.executable}\nimport os,sys\nwith open({str(trace)!r},"a") as f:f.write({path.name!r}+"\\n")\nos.execv(sys.argv[{offset}],sys.argv[{offset}:])\n')
                path.chmod(0o755)
            prefix = [str(paths[0]), "--", str(paths[1]), "SteamLaunch", "AppId=457140", "--",
                      str(paths[2]), "--verb=waitforexitandrun", "--"]
        shutil.copy2(shutil.which("true"), self.args.game_path / "OxygenNotIncluded")
        self.controller.prepare("guest1")
        marker = json.loads((self.args.root / "controller.json").read_text())
        source = ("import sys;sys.path.insert(0,sys.argv.pop(1));import steam_instance_entry as w;"
                  "w.steam_ancestor=lambda:True;w.supervisor(sys.argv[1:])")
        process = subprocess.Popen([sys.executable, "-c", source, str(ROOT / "scripts"),
                                    *prefix, str(self.args.game_path / "OxygenNotIncluded"),
                                    "--oniharness-group", str(self.args.root), "--oniharness-group-token", marker["token"]],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        def receipt(filename, predicate):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    value = json.loads((self.args.root / filename).read_text())
                    if predicate(value):
                        return value
                except (OSError, ValueError):
                    pass
                time.sleep(.01)
            raise AssertionError("Fixture supervisor receipt missing: " + filename)
        try:
            status = receipt("launcher-status.json", lambda r: True)
            control.atomic_json(self.args.root / "launcher-command.json", {"id": "bad-nonce", "nonce": "foreign", "action": "start", "name": "guest1"})
            denied = receipt("launcher-response.json", lambda r: r.get("id") == "bad-nonce")
            self.assertFalse(denied["ok"])
            control.atomic_json(self.args.root / "launcher-command.json", {"id": "start-fixture", "nonce": status["nonce"], "action": "start", "name": "guest1"})
            started = receipt("launcher-response.json", lambda r: r.get("id") == "start-fixture")
            self.assertTrue(started["ok"], started.get("error"))
            exited = receipt("launcher-status.json", lambda r: r.get("children", {}).get("guest1", {}).get("exitCode") == 0)
            self.assertEqual(exited["children"]["guest1"]["pid"], started["pid"])
            entry = json.loads((self.controller.paths("guest1")["control"] / "wrapper-entry.json").read_text())
            self.assertEqual(entry["pid"], started["pid"])
            self.assertEqual(entry["steamLauncherPid"], process.pid)
            self.assertEqual(entry["steamRuntimePrefix"], prefix)
            self.assertFalse(entry["nativeGameFallbackAllowed"])
            if use_runtime_prefix:
                self.assertEqual(trace.read_text().splitlines(), ["steam-launch-wrapper", "reaper", "_v2-entry-point"])
            control.atomic_json(self.args.root / "launcher-command.json", {"id": "stop-fixture", "nonce": status["nonce"], "action": "shutdown"})
            receipt("launcher-response.json", lambda r: r.get("id") == "stop-fixture" and r.get("ok"))
            process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0)
        finally:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=5)

    def test_resident_wrapper_supervises_explicit_non_game_fixture(self):
        self._run_supervisor_fixture(False)

    def test_resident_wrapper_preserves_and_executes_full_runtime_fixture(self):
        self._run_supervisor_fixture(True)

    def test_steam_chain_rejects_unknown_and_ambiguous_executables(self):
        game = self.args.game_path
        executable = str(game / "OxygenNotIncluded")
        with self.assertRaisesRegex(RuntimeError, "exactly once"):
            wrapper.preserved_steam_prefix([executable, executable], game)
        with self.assertRaisesRegex(RuntimeError, "exactly once"):
            wrapper.preserved_steam_prefix(["/not/the/game"], game)
        with self.assertRaisesRegex(RuntimeError, "Unsupported Steam launch chain"):
            wrapper.preserved_steam_prefix([sys.executable, "unknown-wrapper", executable], game)


if __name__ == "__main__":
    unittest.main()
