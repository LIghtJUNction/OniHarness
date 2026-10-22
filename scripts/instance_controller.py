#!/usr/bin/env python3
"""Resident, Steam-only controller for isolated complete ONI game instances.

This is deliberately separate from runtime.py's conservative single-game route.
Protocol peers, direct game execution and Steam authorization bypasses are absent.
"""
import argparse
import concurrent.futures
import copy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import time
import uuid

from runtime_mcp import atomic_json
from instance_resources import ProcessIdentity, ResourceLimits, sample_group
from instance_network import FaultProfile, TCPFaultProxy
from instance_installation import prepare_installation_view
from steam_launch_options import parse as parse_steam_options, option_entry as find_steam_option
from instance_arguments import validate_game_arguments

SCHEMA = 1
APP_ID = "457140"
MAX_MESSAGE = 65536
NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,47}\Z")
MOD_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,95}\Z")


class NativeExitTimeout(RuntimeError):
    def __init__(self, message, name, retained, roots, quit_response):
        super().__init__(message)
        self.name, self.retained, self.roots = name, retained, roots
        self.quit_response = quit_response


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            result.update(block)
    return result.hexdigest()


def contained(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    if path == root or root not in path.parents:
        raise ValueError("Path must be strictly inside the owned test directory: " + str(path))
    return path


def owned_writable_path(path, root):
    """Reject redirected ancestors before touching an instance's writable tree."""
    root = Path(root).resolve()
    path = Path(os.path.abspath(path))
    if path == root or root not in path.parents:
        raise ValueError("Writable path must stay inside its owned instance directory")
    cursor = root
    for part in path.relative_to(root).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("Owned writable path contains a symlink: " + str(cursor))
    return path


def game_processes():
    """Executable identity, never pidof alone, excludes unrelated names/shells."""
    result = {}
    for folder in Path("/proc").iterdir():
        if not folder.name.isdigit():
            continue
        try:
            executable = (folder / "exe").resolve(strict=True)
            if executable.name not in ("OxygenNotIncluded", "OxygenNotIncluded.x86_64"):
                continue
            result[int(folder.name)] = {
                "executable": str(executable),
                "arguments": (folder / "cmdline").read_bytes().decode(errors="replace").split("\0")[:-1],
            }
        except (OSError, PermissionError):
            continue
    return result


def descendant_of(pid, ancestor):
    seen = set()
    for _ in range(64):
        if pid == ancestor:
            return True
        if pid <= 1 or pid in seen:
            return False
        seen.add(pid)
        try:
            fields = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
            pid = int(fields[1])
        except (OSError, ValueError, IndexError):
            return False
    return False


def read_vdf(path):
    tokens = re.findall(r'"((?:[^"\\]|\\.)*)"|([{}])', Path(path).read_text(errors="replace"))
    values = [bracket or re.sub(r'\\(["\\])', r'\1', token) for token, bracket in tokens]
    position = 0

    def parse():
        nonlocal position
        result = {}
        while position < len(values):
            key = values[position]
            position += 1
            if key == "}":
                return result
            if position >= len(values):
                raise ValueError("Malformed Steam configuration")
            value = values[position]
            position += 1
            result[key] = parse() if value == "{" else value
        return result

    return parse()


def steam_launch_options():
    """Read only the recent login's target option, never emit account details."""
    steam_root = Path.home() / ".local/share/Steam"
    try:
        users = read_vdf(steam_root / "config/loginusers.vdf").get("users", {})
        recent = [key for key, value in users.items() if isinstance(value, dict) and value.get("MostRecent") == "1" and key.isdigit()]
        if not recent and len(users) == 1:
            recent = [key for key in users if key.isdigit()]
        if len(recent) != 1:
            return []
        account = int(recent[0]) & 0xffffffff
        local = steam_root / "userdata" / str(account) / "config/localconfig.vdf"
        _, entry = find_steam_option(parse_steam_options(local.read_text(errors="surrogateescape")))
    except (OSError, ValueError):
        return []
    return [entry.value.value] if entry else []


def selects_steam_wrapper(option, wrapper):
    """Accept known exec-style desktop wrappers without rewriting Steam options."""
    try:
        words = shlex.split(option)
    except ValueError:
        return False
    if words[-3:] != ["python3", wrapper, "%command%"]:
        return False
    return tuple(words[:-3]) in ((), ("prime-run",), ("gamemoderun",),
                                ("prime-run", "gamemoderun"), ("gamemoderun", "prime-run"))


def validate_config(document):
    if document.get("schema") != SCHEMA:
        raise ValueError("Unsupported controller schema")
    game = Path(document["gamePath"]).resolve(strict=True)
    if not (game / "OxygenNotIncluded").is_file():
        raise ValueError("Expected the user's installed Linux ONI game")
    instances = document.get("instances")
    if not isinstance(instances, list) or not instances:
        raise ValueError("At least one complete-game instance is required")
    names, ports, identities = set(), set(), set()
    for instance in instances:
        name = instance["name"]
        if not isinstance(name, str) or not NAME.fullmatch(name) or name in names:
            raise ValueError("Instance names must be unique simple identifiers")
        names.add(name)
        if instance.get("graphics") not in ("normal", "headless"):
            raise ValueError("Use an explicit normal or genuine headless graphics mode")
        if not 1 <= instance.get("threads", 0) <= 16:
            raise ValueError("Instance threads must be 1..16")
        port = instance.get("port")
        identity = instance.get("identity")
        if not isinstance(port, int) or not 1024 <= port <= 65535 or port in ports:
            raise ValueError("Each instance needs a distinct unprivileged connection port")
        if not isinstance(identity, str) or len(identity) < 16 or identity in identities:
            raise ValueError("Each instance needs a distinct connection identity")
        ports.add(port)
        identities.add(identity)
        validate_game_arguments(instance.get("gameArgs", []))
        approvals = instance.get("ownedSaveApprovals", [])
        if not isinstance(approvals, list) or len(approvals) > 64:
            raise ValueError("ownedSaveApprovals must be an explicit list of at most64 checkpoints")
        seen_saves = set()
        for approval in approvals:
            if (not isinstance(approval, dict) or not isinstance(approval.get("path"), str) or
                    not Path(approval["path"]).is_absolute() or
                    not re.fullmatch(r"[0-9a-f]{64}", str(approval.get("sha256", ""))) or
                    type(approval.get("originPid")) is not int or approval["originPid"] <= 0 or
                    type(approval.get("originStartTicks")) is not int or approval["originStartTicks"] <= 0 or
                    not re.fullmatch(r"[0-9a-f]{32}", str(approval.get("originRunNonce", "")))):
                raise ValueError("Owned checkpoint approval requires absolute path, exact SHA256 and recorded native origin")
            if approval["path"] in seen_saves:
                raise ValueError("Duplicate owned checkpoint approval")
            seen_saves.add(approval["path"])
    mods = document.get("mods")
    if not isinstance(mods, list) or not mods:
        raise ValueError("Explicit, independently deployed test mods are required")
    mod_names = set()
    for mod in mods:
        if not MOD_NAME.fullmatch(mod["id"]) or mod["id"] in mod_names:
            raise ValueError("Test mod ids must be unique simple identifiers")
        mod_names.add(mod["id"])
        source = Path(mod["source"]).resolve(strict=True)
        if not source.is_dir() or not (source / "mod_info.yaml").is_file():
            raise ValueError("A test mod source must be a built mod folder with mod_info.yaml")
        if not isinstance(mod.get("staticID"), str) or not mod["staticID"]:
            raise ValueError("An explicit staticID is required for every test mod")
    if "OniHarness.Runtime" not in mod_names:
        raise ValueError("The independently deployed OniHarness.Runtime companion is required")
    return document


def merge_mod_manifest(previous, configured, dlc):
    """Retain this profile's native discovery cache without enabling extra mods."""
    if previous is not None and not isinstance(previous, dict):
        raise ValueError("Private mod manifest must be an object")
    document = copy.deepcopy(previous) if previous else {"version": 1, "mod_load_in_progress": False}
    entries = document.get("mods", [])
    if not isinstance(entries, list):
        raise ValueError("Private mod manifest must contain a mod list")
    declared = {mod["id"]: mod for mod in configured}
    found, keys = set(), set()
    result = []
    for entry in entries:
        label = entry.get("label") if isinstance(entry, dict) else None
        if not isinstance(label, dict) or not isinstance(label.get("id"), str):
            raise ValueError("Private mod manifest contains an invalid label")
        if type(label.get("distribution_platform")) is not int:
            raise ValueError("Private mod manifest contains an invalid distribution platform")
        if entry.get("enabledForDlc") is not None and not isinstance(entry["enabledForDlc"], list):
            raise ValueError("Private mod manifest contains an invalid DLC enablement list")
        key = (label.get("distribution_platform"), label["id"])
        if key in keys:
            raise ValueError("Private mod manifest contains duplicate labels")
        keys.add(key)
        mod = declared.get(label["id"]) if key[0] == 0 else None
        if mod:
            if entry.get("staticID") not in (None, mod["staticID"]):
                raise ValueError("Private local mod identity conflicts with the declared payload")
            entry["staticID"] = mod["staticID"]
            entry["enabled"] = not bool(dlc)
            entry["enabledForDlc"] = [dlc] if dlc else []
            found.add(mod["id"])
        else:
            entry["enabled"] = False
            if entry.get("enabledForDlc"):
                entry["enabledForDlc"] = []
        # Keep native labels, versions, statuses, discovery metadata and crash
        # counters. Erasing them causes false INSTALLED events on every boot.
        result.append(entry)
    for mod in configured:
        if mod["id"] not in found:
            result.append({"label": {"distribution_platform": 0, "id": mod["id"], "title": mod["id"], "version": 0},
                           "status": 1, "enabled": not bool(dlc), "enabledForDlc": [dlc] if dlc else [],
                           "crash_count": 0, "reinstall_path": None, "staticID": mod["staticID"]})
    document["mods"] = result
    return document


def mod_files(source):
    """Copy only an explicit built mod folder, never the game or build tree."""
    source = Path(source).resolve()
    excluded = {".git", "bin", "obj", ".local-tests", "dist", "__pycache__"}
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(part in excluded for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError("Mod source symlinks are not permitted: " + str(path))
        if path.is_file():
            yield relative, path


def manifest_after_owned_deployment(manifest, targets):
    """Resolve only the offline installs this controller actually performs.

    Native status 3 is ReinstallPending, not inert discovery metadata. Replaying
    its directory-valued reinstall_path makes native startup disable the mod.
    This proposed document is committed only after every deployed hash matches.
    """
    result = copy.deepcopy(manifest)
    for entry in result["mods"]:
        label = entry["label"]
        target = targets.get(label["id"]) if label["distribution_platform"] == 0 else None
        status, directive = entry.get("status", 0), entry.get("reinstall_path")
        if target is None:
            if status in (2, 3) or directive:
                raise ValueError("Unplanned native mod operation requires review; no pending operation was erased")
            continue
        if status not in (0, 1, 3):
            raise ValueError("Declared local mod has native uninstall/failed-install state; review required")
        if directive and Path(directive).resolve() != target.resolve():
            raise ValueError("Declared local mod has an external native reinstall directive; review required")
        entry["status"] = 1  # Installed, after the verified owned payload copy.
        entry["reinstall_path"] = None
    return result


class Controller:
    def __init__(self, root):
        self.root = Path(root).resolve()
        marker = json.loads((self.root / "controller.json").read_text())
        if marker.get("schema") != SCHEMA:
            raise ValueError("Not an initialized owned controller directory")
        self.config = validate_config(json.loads((self.root / "group.json").read_text()))
        self.instances = {i["name"]: i for i in self.config["instances"]}
        self.state_path = self.root / "state.json"
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {"instances": {}}
        self.lock = threading.RLock()
        self.monitor_stop = threading.Event()
        self.proxies = {}
        self.launch_request_lock = threading.Lock()
        self.launch_requests = []

    def reap_launch_requests(self):
        # Only Popen handles created by this controller are polled. Never use
        # waitpid(-1), adopt Steam-global processes, or hide zombies from limits.
        with self.launch_request_lock:
            self.launch_requests = [process for process in self.launch_requests if process.poll() is None]

    def paths(self, name):
        if name not in self.instances:
            raise ValueError("Unknown instance: " + str(name))
        base = owned_writable_path(self.root / "instances" / name, self.root)
        paths = {"base": base, "user": base / "xdg/unity3d/Klei/Oxygen Not Included", "control": base / "control", "logs": base / "logs", "installation": base / "installation"}
        for path in paths.values():
            owned_writable_path(path, self.root)
        return paths

    def save_state(self):
        atomic_json(self.state_path, self.state)

    def desired_files(self):
        result = {}
        for mod in self.config["mods"]:
            for relative, source in mod_files(mod["source"]):
                result[str(Path(mod["id"]) / relative)] = digest(source)
        return result

    def owned_save_approvals(self, name):
        paths = self.paths(name)
        record = self.state["instances"].get(name, {})
        issued = record.get("checkpointApprovals", [])
        origins = record.get("nativeOrigins", [])
        approved = []
        for item in self.instances[name].get("ownedSaveApprovals", []):
            origin = {"pid": item["originPid"], "startTicks": item["originStartTicks"], "runNonce": item["originRunNonce"]}
            if item not in issued or origin not in origins:
                raise ValueError("Checkpoint approval lacks independently recorded native origin and controller issuance")
            path = owned_writable_path(item["path"], paths["user"])
            if (str(path) != item["path"] or path.suffix != ".sav" or not path.is_file() or
                    path.parent != paths["user"] / "save_files" / ("OniOnlineHarness-" + str(item["originPid"]))):
                raise ValueError("Owned checkpoint must be the exact native-generated file inside this instance's private save directory")
            if digest(path) != item["sha256"]:
                raise RuntimeError("Owned checkpoint SHA256 changed; no load or deployment was authorized")
            approved.append({"path": str(path), "sha256": item["sha256"]})
        if len(approved) + bool(self.instances[name].get("save", self.config.get("save"))) > 64:
            raise ValueError("Combined save approvals exceed the native companion limit64")
        return approved

    def approve_owned_save(self, name, path, sha256):
        paths = self.paths(name)
        if self.live(name) or self.chain_identity(name):
            raise RuntimeError("Owned checkpoint approvals require this instance and its Steam chain to be stopped")
        record = self.state["instances"].get(name, {})
        origin_pid, origin_ticks, origin_nonce = record.get("pid"), record.get("startTicks"), record.get("runNonce")
        origin = {"pid": origin_pid, "startTicks": origin_ticks, "runNonce": origin_nonce}
        if not origin_pid or origin not in record.get("nativeOrigins", []):
            raise ValueError("An owned native run must create the checkpoint before approval")
        candidate = copy.deepcopy(self.config)
        instance = next(i for i in candidate["instances"] if i["name"] == name)
        item = {"path": str(path), "sha256": sha256, "originPid": origin_pid,
                "originStartTicks": origin_ticks, "originRunNonce": origin_nonce}
        approvals = instance.setdefault("ownedSaveApprovals", [])
        existing = next((i for i in approvals if i["path"] == str(path)), None)
        if existing and existing != item:
            raise ValueError("Conflicting checkpoint approval cannot replace its original hash or native provenance")
        if not existing:
            approvals.append(item)
        validate_config(candidate)
        # Validate before committing either in-memory or on-disk configuration.
        original = self.instances[name]
        prior_issued = record.get("checkpointApprovals", [])
        self.instances[name] = instance
        record["checkpointApprovals"] = prior_issued + ([] if item in prior_issued else [item])
        try:
            self.owned_save_approvals(name)
        except Exception:
            record["checkpointApprovals"] = prior_issued
            raise
        finally:
            self.instances[name] = original
        # A crash after this state commit leaves an unused issued approval,
        # never a config-only approval that bypasses independent provenance.
        self.save_state()
        atomic_json(self.root / "group.json", candidate)
        self.config = candidate
        self.instances = {i["name"]: i for i in candidate["instances"]}
        return {"approved": item, "copied": False, "activeProfileChanged": False}

    def prepare(self, name, fresh_save=False):
        paths = self.paths(name)
        if self.live(name) or self.chain_identity(name):
            raise RuntimeError("Refusing to deploy or recopy a save while its native game or Steam runtime chain is alive")
        native_approved = self.owned_save_approvals(name)
        mods_dir = paths["user"] / "mods"
        manifest_path = mods_dir / "mods.json"
        targets = {mod["id"]: mods_dir / "Local" / mod["id"] for mod in self.config["mods"]}
        save = paths["user"] / "save_files" / "OniHarness" / "test.sav"
        # Validate the complete write set before mkdir, copying, or deleting any
        # payload. resolve() alone would bless an escaped Local ancestor.
        for path in [manifest_path, *targets.values(), save,
                     paths["control"] / "instance-profile.json", paths["control"] / "render.json"]:
            owned_writable_path(path, paths["base"])
        for path in paths.values():
            path.mkdir(parents=True, exist_ok=True)
        installation = prepare_installation_view(self.config["gamePath"], paths["installation"])
        desired = self.desired_files()
        previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
        manifest = merge_mod_manifest(previous, self.config["mods"], self.config.get("dlc", "EXPANSION1_ID"))
        manifest = manifest_after_owned_deployment(manifest, targets)
        for mod in self.config["mods"]:
            target = mods_dir / "Local" / mod["id"]
            # Delete stale files only inside this controller-created mod folder.
            if target.exists():
                shutil.rmtree(target)
            target.mkdir(parents=True)
            for relative, source in mod_files(mod["source"]):
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                if digest(destination) != desired[mod["id"] + "/" + str(relative)]:
                    raise RuntimeError("Owned payload copy hash mismatch; native installation state was not committed")
        actual = {mod_id + "/" + str(relative): digest(path)
                  for mod_id, target in targets.items() for relative, path in mod_files(target)}
        if actual != desired:
            raise RuntimeError("Owned payload inventory mismatch; native installation state was not committed")
        atomic_json(manifest_path, manifest)
        source_save = self.instances[name].get("save", self.config.get("save"))
        approved = []
        if source_save:
            source_save = Path(source_save).resolve(strict=True)
            if source_save.suffix != ".sav" or not source_save.is_file():
                raise ValueError("Tests require an explicit existing .sav source")
            save.parent.mkdir(parents=True, exist_ok=True)
            if fresh_save or not save.exists():
                source_hash = digest(source_save)
                shutil.copy2(source_save, save)
                if digest(source_save) != source_hash or digest(save) != source_hash:
                    raise RuntimeError("Source save changed while copying; no test load was authorized")
            approved.append({"path": str(save), "sha256": digest(save)})
        # Exact native checkpoints are never copied, renamed or overwritten.
        # Re-read hashes before publishing their next-process allowlist.
        if self.owned_save_approvals(name) != native_approved:
            raise RuntimeError("Owned checkpoint approvals changed during preparation")
        approved.extend(native_approved)
        atomic_json(paths["control"] / "instance-profile.json", {
            "identity": self.instances[name]["identity"], "userRoot": str(paths["user"]), "approvedSaves": approved})
        atomic_json(paths["control"] / "render.json", {
            "mode": self.instances[name]["graphics"], "interval": 60, "updateRate": 15})
        prior = self.state["instances"].get(name, {})
        self.state["instances"][name] = {"deployed": desired, "preparedAt": utcnow(), "approvedSaves": approved,
                                        "installation": installation,
                                        "nativeOrigins": prior.get("nativeOrigins", []),
                                        "checkpointApprovals": prior.get("checkpointApprovals", [])}
        self.save_state()
        return paths

    def live(self, name):
        record = self.state["instances"].get(name, {})
        pid = record.get("pid")
        if not pid:
            return False
        try:
            current = ProcessIdentity.capture(pid)
            if current.start_ticks != record["startTicks"]:
                return False
            process = game_processes().get(pid)
            return process is not None and str(self.paths(name)["control"]) in process["arguments"]
        except (OSError, RuntimeError, ValueError, KeyError):
            return False

    def owned_identities(self):
        return {name: ProcessIdentity.capture(self.state["instances"][name]["pid"])
                for name in self.instances if self.live(name)}

    def chain_identity(self, name):
        record = self.state["instances"].get(name, {})
        try:
            current = ProcessIdentity.capture(record["steamChainPid"])
            return current if current.start_ticks == record["steamChainStartTicks"] else None
        except (OSError, ValueError, KeyError):
            return None

    def resources(self):
        limits = ResourceLimits(**self.config.get("resourceLimits", {}))
        return sample_group(self.resource_identities(), limits=limits)

    def resource_identities(self):
        self.reap_launch_requests()
        identities = self.owned_identities()
        for name in self.instances:
            identity = self.chain_identity(name)
            if identity and all(row.pid != identity.pid for row in identities.values()):
                identities[name + "/steam-chain"] = identity
        identities["controller"] = ProcessIdentity.capture(os.getpid())
        try:
            identities["steam-launcher"] = ProcessIdentity.capture(self.launcher_status()["pid"])
        except (OSError, RuntimeError, ValueError, KeyError):
            pass
        # Include crash handlers and other descendants, not just the main game
        # PIDs. Steam itself is an existing shared ancestor and is not adopted.
        parents = {}
        for folder in Path("/proc").iterdir():
            if folder.name.isdigit():
                try:
                    fields = (folder / "stat").read_text().rsplit(")", 1)[1].split()
                    parents[int(folder.name)] = int(fields[1])
                except (OSError, ValueError, IndexError):
                    pass
        seen = {identity.pid for identity in identities.values()}
        pending = list(seen)
        while pending:
            parent = pending.pop()
            for pid, owner in parents.items():
                if owner == parent and pid not in seen:
                    seen.add(pid)
                    try:
                        identities["child-" + str(pid)] = ProcessIdentity.capture(pid)
                        pending.append(pid)
                    except (OSError, ValueError):
                        pass
        return identities

    def status(self):
        result = {}
        for name in self.instances:
            try:
                result[name] = self.read_status(name)
            except (OSError, RuntimeError, ValueError, KeyError) as error:
                result[name] = {"alive": self.live(name), "verified": False, "error": str(error)}
        return {"schema": SCHEMA, "capturedAt": utcnow(), "kind": "complete-game-instances",
                "instances": result, "resources": self.resources(), "protocolClients": 0,
                "stopAllTail": copy.deepcopy(self.state.get("stopAllTail")),
                "proxies": {key: proxy.snapshot() for key, proxy in self.proxies.items()}}

    def read_status(self, name, cleanup=False):
        data = json.loads((self.paths(name)["control"] / "instance-status.json").read_text())
        instance = self.instances[name]
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(data["capturedAt"].replace("Z", "+00:00"))).total_seconds()
        if data.get("identity") != instance["identity"] or not data.get("runNonce"):
            raise RuntimeError("Instance identity or run nonce mismatch")
        process = game_processes().get(data["pid"])
        if process is None or str(self.paths(name)["control"]) not in process["arguments"]:
            raise RuntimeError("Telemetry does not belong to the isolated complete-game process")
        expected_game = self.paths(name)["installation"] / "OxygenNotIncluded"
        if Path(process["executable"]).resolve() != expected_game:
            raise RuntimeError("Native telemetry came from a different installation view")
        record = self.state["instances"].get(name, {})
        previously_confirmed = False
        if cleanup and record.get("pid") == data["pid"] and record.get("runNonce") == data["runNonce"]:
            try:
                previously_confirmed = ProcessIdentity.capture(data["pid"]).start_ticks == record["startTicks"]
            except (OSError, ValueError, KeyError):
                pass
        chain = self.chain_identity(name)
        chain_owned = chain is not None and descendant_of(data["pid"], chain.pid)
        if not previously_confirmed and not chain_owned:
            raise RuntimeError("Native game PID is not a descendant of its live Steam runtime incarnation")
        if not 0 <= age <= 10:
            if not (age > 10 and cleanup and previously_confirmed and chain_owned):
                raise RuntimeError("Instance telemetry is stale")
        if not cleanup and (not data.get("rootVerified") or Path(data["nativeUserRoot"]).resolve() != self.paths(name)["user"]):
            raise RuntimeError("Actual native user root is not isolated; no gameplay command permitted")
        if not cleanup and instance["graphics"] == "headless":
            if not data.get("batchMode") or data.get("graphicsDevice") != "Null" or data.get("cameraRenderCallbacks") != 0:
                raise RuntimeError("Headless telemetry failed; no graphical fallback")
        if not cleanup and (data.get("error") or data.get("gameHasCrash") or data.get("gameError")):
            raise RuntimeError("Native instance reported: " + str(data.get("error") or data.get("gameError") or "native crash"))
        return data

    def observe_native_pid(self, name):
        """Adopt only our exact native child, even if root/headless checks fail.

        This preserves an orderly pause/quit route for failed startup validation.
        The chain's outer Popen PID is never reported as a native game PID.
        """
        try:
            data = self.read_status(name, cleanup=True)
            chain = self.chain_identity(name)
            if chain is None or not descendant_of(data["pid"], chain.pid):
                return
            identity = ProcessIdentity.capture(data["pid"])
            record = self.state["instances"][name]
            changed = (record.get("pid") != identity.pid or record.get("startTicks") != identity.start_ticks or
                       record.get("runNonce") != data["runNonce"])
            if changed:
                record.update({"pid": identity.pid, "startTicks": identity.start_ticks, "runNonce": data["runNonce"]})
            # Cleanup-only adoption must never certify a checkpoint's origin.
            # Require the ordinary private-root/graphics/crash checks and actual
            # loaded mod hashes as well as the already validated Steam ancestry.
            try:
                strict = self.read_status(name)
                self.verify_hashes(name, strict)
                if (strict["pid"] != identity.pid or strict["runNonce"] != data["runNonce"] or
                        ProcessIdentity.capture(identity.pid) != identity):
                    raise RuntimeError("Native origin changed during strict validation")
                origin = {"pid": identity.pid, "startTicks": identity.start_ticks, "runNonce": data["runNonce"]}
                if origin not in record.get("nativeOrigins", []):
                    record.setdefault("nativeOrigins", []).append(origin)
                    changed = True
            except (OSError, RuntimeError, ValueError, KeyError):
                pass
            if changed:
                self.save_state()
        except (OSError, RuntimeError, ValueError, KeyError):
            pass

    def verify_hashes(self, name, status):
        deployed = self.state["instances"][name]["deployed"]
        expected = {}
        for path, sha in deployed.items():
            if path.lower().endswith(".dll"):
                filename = Path(path).name
                if filename in expected and expected[filename] != sha:
                    raise RuntimeError("Ambiguous same-name mod assembly: " + filename)
                expected[filename] = sha
        receipt_name = status.get("assemblyReceipt")
        if receipt_name != "instance-assemblies.json":
            raise RuntimeError("Missing compact status assembly receipt")
        receipt = json.loads((self.paths(name)["control"] / receipt_name).read_text())
        if any(receipt.get(key) != status.get(key) for key in ("pid", "identity", "runNonce")):
            raise RuntimeError("Loaded assembly receipt belongs to another process or run")
        actual = {Path(item["location"]).name: item["sha256"] for item in receipt.get("assemblies", []) if item.get("location")}
        mismatches = {key: {"expected": value, "loaded": actual.get(key)}
                      for key, value in expected.items() if actual.get(key) != value}
        if mismatches:
            raise RuntimeError("Actual loaded mod hashes do not match deployment: " + json.dumps(mismatches))
        return expected

    def launch_command(self, name):
        steam = shutil.which("steam")
        if not steam:
            raise RuntimeError("An existing authorized Steam client is required")
        instance, paths = self.instances[name], self.paths(name)
        command = [steam, "-applaunch", APP_ID, "--oniharness-group", str(self.root),
                   "--oniharness-group-token", json.loads((self.root / "controller.json").read_text())["token"]]
        validate_game_arguments(instance.get("gameArgs", []))
        return command

    def launcher_status(self):
        status = json.loads((self.root / "launcher-status.json").read_text())
        if not 0 <= time.time() - status["updatedAtUnix"] <= 5 or not status.get("steamAncestorVerified"):
            raise RuntimeError("Steam group launcher telemetry is stale or unauthenticated")
        arguments = (Path("/proc") / str(status["pid"]) / "cmdline").read_bytes().split(b"\0")
        if str(self.root).encode() not in arguments or str(Path(__file__).with_name("steam_instance_entry.py")).encode() not in arguments:
            raise RuntimeError("The launcher PID is not this group's Steam-selected wrapper")
        return status

    def ensure_launcher(self, timeout):
        try:
            return self.launcher_status()
        except (OSError, RuntimeError, ValueError, KeyError):
            pass
        # No global Steam configuration is edited while Steam is running. Users
        # can set the official per-game option once; inspection is read-only.
        wrapper = str(Path(__file__).with_name("steam_instance_entry.py").resolve())
        options = steam_launch_options()
        expected = f'python3 {shlex.quote(wrapper)} %command%'
        if not any(selects_steam_wrapper(option, wrapper) for option in options):
            raise RuntimeError("Steam Launch Options must select the official wrapper before isolated tests: " + expected +
                               "; existing options were left untouched")
        stale = self.root / "launcher-status.json"
        stale.unlink(missing_ok=True)
        command = self.launch_command(next(iter(self.instances)))
        with (self.root / "steam-launch.log").open("ab") as output:
            process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            with self.launch_request_lock:
                self.launch_requests.append(process)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                return self.launcher_status()
            except (OSError, RuntimeError, ValueError, KeyError):
                time.sleep(.25)
        raise RuntimeError("Steam did not enter its configured group wrapper; inspect steam-launch.log, no direct fallback")

    def launcher_command(self, action, name=None, expected_supervisor=None):
        status = self.launcher_status()
        if expected_supervisor is not None:
            identity = ProcessIdentity.capture(status["pid"])
            if (identity.pid, identity.start_ticks, status["nonce"]) != (
                    expected_supervisor["pid"], expected_supervisor["startTicks"], expected_supervisor["nonce"]):
                raise RuntimeError("Supervisor changed before the bound shutdown request; no command sent")
        request = {"id": uuid.uuid4().hex, "nonce": status["nonce"], "action": action, "name": name}
        atomic_json(self.root / "launcher-command.json", request)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                response = json.loads((self.root / "launcher-response.json").read_text())
                if response.get("id") == request["id"] and response.get("nonce") == status["nonce"]:
                    if not response.get("ok"):
                        raise RuntimeError("Steam group launcher rejected request: " + str(response.get("error")))
                    return response
            except (OSError, ValueError):
                pass
            time.sleep(.05)
        raise RuntimeError("Steam group launcher did not acknowledge command")

    def preflight(self, count):
        resource = self.resources()
        if resource.get("violations"):
            raise RuntimeError("Instance group resource limit exceeded: " + json.dumps(resource["violations"]))
        available = resource["system"]["MemAvailable"] * 1024
        own = (resource.get("totals", {}).get("pssKiB", 0) or 0) * 1024
        required = count * self.config.get("expectedInstanceBytes", 3 * 1024 ** 3) + self.config.get("reserveBytes", 1024 ** 3)
        if available + own < required:
            raise RuntimeError(f"Insufficient measured memory for {count} simultaneous complete games: available+owned PSS={available + own}, required={required}; no sequential substitute")

    def start(self, name, timeout=120):
        if not 1 <= timeout <= 300:
            raise ValueError("Startup timeout must be 1..300 seconds")
        tail = self.state.get("stopAllTail")
        if tail and tail.get("phase") in ("waiting_for_exit", "shutdown_requested", "shutdown_acknowledged"):
            tail.update({"phase": "superseded", "reason": "new start request; old automatic shutdown eligibility cancelled"})
            self.save_state()
        desired = self.desired_files()
        record = self.state["instances"].get(name, {})
        if self.chain_identity(name):
            # Recover a prior interrupted readiness wait without redeploying or
            # creating another chain. First ownership adoption stays strict.
            self.observe_native_pid(name)
        if not self.live(name) and self.chain_identity(name):
            raise RuntimeError("An owned Steam runtime chain is pending or failed; recover it before redeployment/restart")
        if self.live(name):
            if record.get("deployed") == desired:
                status = self.read_status(name)
                self.verify_hashes(name, status)
                return {"reused": True, "status": status}
            self.stop(name)
        unowned = set(game_processes()) - {i.pid for i in self.owned_identities().values()}
        if unowned:
            raise RuntimeError("Another task owns an ONI process; no launch/deployment: " + str(sorted(unowned)))
        self.preflight(len(self.owned_identities()) + 1)
        paths = self.prepare(name)
        try:
            self.ensure_launcher(timeout)
        except (OSError, RuntimeError, ValueError, KeyError) as error:
            destination = paths["control"] / "startup-failure.json"
            atomic_json(destination, {"failedAt": utcnow(), "name": name, "stage": "Steam-group-launcher",
                                      "error": str(error), "nativeGameInstancesStarted": 0,
                                      "directGameFallbackUsed": False})
            raise RuntimeError(str(error) + "; failure receipt: " + str(destination)) from error
        old_status = paths["control"] / "instance-status.json"
        if old_status.exists():
            old_status.unlink()
        for filename in ("instance-command.json", "instance-response.json", "run-lease.json", "failure.json"):
            stale = paths["control"] / filename
            if stale.exists():
                stale.unlink()
        command = self.launch_command(name)
        atomic_json(paths["control"] / "launch.json", {"requestedAt": utcnow(), "graphics": self.instances[name]["graphics"], "command": command})
        launch_response = self.launcher_command("start", name)
        chain = ProcessIdentity.capture(launch_response["pid"])
        self.state["instances"][name].update({"pid": None, "steamChainPid": chain.pid, "steamChainStartTicks": chain.start_ticks,
                                               "startedAt": utcnow()})
        self.save_state()
        deadline, last = time.monotonic() + timeout, "no instance telemetry"
        while time.monotonic() < deadline:
            try:
                self.observe_native_pid(name)
                status = self.read_status(name)
                process = ProcessIdentity.capture(status["pid"])
                self.state["instances"][name].update({"pid": process.pid, "startTicks": process.start_ticks,
                                                       "launcherPid": self.launcher_status()["pid"], "startedAt": utcnow()})
                self.save_state()
                hashes = self.verify_hashes(name, status)
                return {"reused": False, "status": status, "loadedHashes": hashes}
            except (OSError, RuntimeError, ValueError, KeyError) as error:
                last = str(error)
            child = self.launcher_status().get("children", {}).get(name, {})
            if child.get("pid") == chain.pid and child.get("exitCode") is not None and not self.live(name):
                last = "Steam runtime chain exited before native telemetry; exitCode=" + str(child["exitCode"]) + "; " + last
                break
            time.sleep(.25)
        # A Steam refusal remains a failed concurrent launch, never a queued pass.
        failure = {"failedAt": utcnow(), "name": name, "error": last, "steamLauncherPid": self.launcher_status()["pid"],
                   "gameProcesses": game_processes(), "note": "No direct launch, authorization bypass, or sequential fallback was used."}
        atomic_json(paths["control"] / "startup-failure.json", failure)
        raise RuntimeError("Steam launch did not produce a verified isolated complete game: " + last)

    def command(self, name, action, timeout=30, **arguments):
        status = self.read_status(name, cleanup=action in ("pause", "quit"))
        request = {"id": uuid.uuid4().hex, "instance": self.instances[name]["identity"], "nonce": status["runNonce"],
                   "action": action, **arguments}
        control = self.paths(name)["control"]
        atomic_json(control / "instance-command.json", request)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                response = json.loads((control / "instance-response.json").read_text())
                if response.get("id") == request["id"]:
                    if response.get("instance") != request["instance"] or response.get("nonce") != request["nonce"]:
                        raise RuntimeError("Response crossed instance/run identity")
                    if response.get("state") == "running":
                        time.sleep(.05)
                        continue
                    if response.get("error") or response.get("state") not in ("accepted", "completed"):
                        raise RuntimeError("Native command failed: " + str(response.get("error")))
                    return response
            except (OSError, ValueError):
                pass
            time.sleep(.05)
        raise RuntimeError("Native command acknowledgment timed out: " + name + "/" + action)

    def stop(self, name, timeout=30):
        if not self.live(name):
            if self.chain_identity(name):
                raise RuntimeError("Steam runtime chain is still alive without native cleanup telemetry; no force kill or overwrite")
            return {"alreadyStopped": True}
        pid = self.state["instances"][name]["pid"]
        owned = self.resource_identities()
        chain = self.chain_identity(name)
        roots = [pid] + ([chain.pid] if chain else [])
        retained = {label: identity for label, identity in owned.items()
                    if any(descendant_of(identity.pid, parent) for parent in roots)}
        self.command(name, "pause")
        quit_response = self.command(name, "quit")
        deadline = time.monotonic() + timeout
        def retained_alive():
            alive = []
            for identity in retained.values():
                try:
                    if not self.incarnation_gone({"pid": identity.pid, "startTicks": identity.start_ticks}):
                        alive.append(identity.pid)
                except (OSError, RuntimeError, ValueError, KeyError):
                    alive.append(identity.pid)  # Unknown is never proof of exit.
            return alive
        while (self.live(name) or retained_alive()) and time.monotonic() < deadline:
            for label, identity in self.resource_identities().items():
                if any(descendant_of(identity.pid, parent) for parent in roots):
                    retained[label] = identity
            time.sleep(.1)
        if self.live(name) or retained_alive():
            raise NativeExitTimeout("Owned native/runtime chain refused orderly exit; left alive, no force kill or DLL overwrite: " + name,
                                    name, retained, [ProcessIdentity(pid, self.state["instances"][name]["startTicks"])] +
                                    ([chain] if chain else []), quit_response)
        self.state["instances"][name]["stoppedAt"] = utcnow()
        self.save_state()
        return {"stopped": True, "pid": pid, "ownedProcessesReclaimed": len(retained)}

    def instance_generations(self):
        keys = ("pid", "startTicks", "runNonce", "steamChainPid", "steamChainStartTicks")
        return {name: {key: self.state["instances"].get(name, {}).get(key) for key in keys} for name in self.instances}

    @staticmethod
    def incarnation_gone(item):
        identity = ProcessIdentity(item["pid"], item["startTicks"])
        try:
            return ProcessIdentity.capture(identity.pid) != identity
        except FileNotFoundError:
            # A failed stat/permission read alone is not proof of process exit.
            try:
                os.stat(Path("/proc") / str(identity.pid))
            except FileNotFoundError:
                return True
            raise RuntimeError("Process identity unreadable; late cleanup deferred")

    def stop_all(self):
        supervisor = None
        try:
            status = self.launcher_status()
            identity = ProcessIdentity.capture(status["pid"])
            supervisor = {"pid": identity.pid, "startTicks": identity.start_ticks, "nonce": status["nonce"]}
        except (OSError, RuntimeError, ValueError, KeyError):
            pass
        try:
            result = {name: self.stop(name) for name in self.instances}
        except NativeExitTimeout as error:
            stamp = uuid.uuid4().hex
            launch_requests, launch_error = [], None
            with self.launch_request_lock:
                for process in self.launch_requests:
                    if process.poll() is None:
                        try:
                            identity = ProcessIdentity.capture(process.pid)
                            launch_requests.append({"pid": identity.pid, "startTicks": identity.start_ticks})
                        except (OSError, RuntimeError, ValueError) as capture_error:
                            launch_error = str(capture_error)
            retained = {label: {"pid": identity.pid, "startTicks": identity.start_ticks}
                        for label, identity in error.retained.items()}
            roots = [{"pid": identity.pid, "startTicks": identity.start_ticks} for identity in error.roots]
            generations = self.instance_generations()
            for record in generations.values():
                for pid_key, ticks_key in (("pid", "startTicks"), ("steamChainPid", "steamChainStartTicks")):
                    item = {"pid": record[pid_key], "startTicks": record[ticks_key]}
                    if item["pid"] is not None and item not in roots:
                        roots.append(item)
            observation_error = None
            try:
                for root in roots:
                    if self.incarnation_gone(root):
                        continue
                    additions = {label: {"pid": identity.pid, "startTicks": identity.start_ticks}
                                 for label, identity in self.resource_identities().items()
                                 if descendant_of(identity.pid, root["pid"])}
                    if additions and self.incarnation_gone(root):
                        raise RuntimeError("Owned root changed during descendant sampling; manual review required")
                    retained.update({label + "/" + str(item["startTicks"]): item for label, item in additions.items()})
            except (OSError, RuntimeError, ValueError, KeyError) as capture_error:
                observation_error = str(capture_error)
            failure_path = owned_writable_path(self.root / "receipts" / ("stop-all-" + stamp + "-failure.json"), self.root)
            tail = {"attemptId": stamp, "originalOutcome": "FAILED", "originalError": str(error),
                    "failedAt": utcnow(), "phase": "waiting_for_exit", "observeUntilUnix": time.time() + 300,
                    "generations": generations, "retained": retained, "roots": roots,
                    "initialObservationError": observation_error,
                    "supervisor": supervisor, "quitAcceptance": error.quit_response,
                    "launchRequests": launch_requests, "launchRequestCaptureError": launch_error,
                    "failureReceipt": str(failure_path), "additionalNativeCommandsSent": 0}
            # This immutable failure receipt is never rewritten by reconciliation.
            atomic_json(failure_path, copy.deepcopy(tail))
            self.state["stopAllTail"] = tail
            self.save_state()
            raise
        try:
            result["steamLauncher"] = self.launcher_command("shutdown")
        except (OSError, RuntimeError, ValueError, KeyError) as error:
            result["steamLauncher"] = {"error": str(error)}
        return result

    def reconcile_stop_tail(self):
        """Bounded observation only; never resend native pause/quit or signal."""
        tail = self.state.get("stopAllTail")
        if not tail or tail.get("phase") not in ("waiting_for_exit", "shutdown_requested", "shutdown_acknowledged"):
            return
        try:
            if tail["generations"] != self.instance_generations():
                tail.update({"phase": "superseded", "reason": "native/Steam instance generation changed"})
                return
            if time.time() > tail["observeUntilUnix"]:
                tail.update({"phase": "manual_cleanup_required", "reason": "bounded observation deadline exceeded; no signal sent"})
                return
            supervisor = tail["supervisor"]
            if not supervisor or not supervisor.get("nonce"):
                raise RuntimeError("No confirmed original supervisor identity; automatic cleanup forbidden")
            if tail.get("initialObservationError") or tail.get("uncertainDescendantObservation"):
                raise RuntimeError("Uncertain original owned descendants; automatic cleanup deferred")
            # Track additional observed descendants only while an original root
            # retains its exact incarnation. Never follow a reused numeric PID.
            try:
                for root in tail["roots"]:
                    if self.incarnation_gone(root):
                        continue
                    additions = {label: {"pid": identity.pid, "startTicks": identity.start_ticks}
                                 for label, identity in self.resource_identities().items()
                                 if descendant_of(identity.pid, root["pid"])}
                    if additions and self.incarnation_gone(root):
                        raise RuntimeError("Owned root changed during descendant sampling")
                    for label, item in additions.items():
                        tail["retained"][label + "/" + str(item["startTicks"])] = item
            except (OSError, RuntimeError, ValueError, KeyError) as observation_error:
                tail["uncertainDescendantObservation"] = str(observation_error)
                raise
            if not all(self.incarnation_gone(item) for item in [*tail["roots"], *tail["retained"].values()]):
                return
            for record in tail["generations"].values():
                for pid_key, ticks_key in (("pid", "startTicks"), ("steamChainPid", "steamChainStartTicks")):
                    if record[pid_key] is not None and not self.incarnation_gone({"pid": record[pid_key], "startTicks": record[ticks_key]}):
                        return
            self.reap_launch_requests()
            if tail.get("launchRequestCaptureError"):
                raise RuntimeError("Uncertain original launch-request identity; cleanup deferred")
            if not all(self.incarnation_gone(item) for item in tail.get("launchRequests", [])):
                return
            with self.launch_request_lock:
                if self.launch_requests:
                    return
            if self.incarnation_gone(supervisor):
                raw = json.loads((self.root / "launcher-status.json").read_text())
                if (raw["pid"], raw["nonce"]) != (supervisor["pid"], supervisor["nonce"]):
                    tail.update({"phase": "superseded", "reason": "supervisor telemetry generation changed"})
                    return
                tail.update({"phase": "eventual_stopped", "observedAt": utcnow(),
                             "originalOutcome": "FAILED", "allObservedOwnedIncarnationsGone": True})
                return
            status = self.launcher_status()
            if (status["pid"], status["nonce"]) != (supervisor["pid"], supervisor["nonce"]):
                tail.update({"phase": "superseded", "reason": "supervisor identity changed"})
                return
            if any(child.get("exitCode") is None for child in status.get("children", {}).values()):
                return
            if tail["phase"] == "waiting_for_exit":
                # Persist before issuing the one normal shutdown request. A crash
                # or ambiguous ACK never causes a second request on daemon resume.
                tail.update({"phase": "shutdown_requested", "shutdownRequestedAt": utcnow()})
                self.save_state()
                tail["shutdownAcceptance"] = self.launcher_command("shutdown", expected_supervisor=supervisor)
                tail["phase"] = "shutdown_acknowledged"
        except (OSError, RuntimeError, ValueError, KeyError) as error:
            tail["lastObservationError"] = str(error)
        finally:
            self.save_state()

    def reload(self, name, path=None):
        declared = self.owned_save_approvals(name)
        if path is None and len(declared) + bool(self.instances[name].get("save", self.config.get("save"))) != 1:
            raise RuntimeError("Select one exact approved checkpoint with reload --path before starting a native process")
        if path is not None:
            requested = owned_writable_path(path, self.paths(name)["user"])
            if str(requested) != path:
                raise ValueError("Reload requires the exact approved checkpoint path")
            if path not in {item["path"] for item in declared}:
                # The legacy disposable copy is also approved by preparation.
                copy_path = self.paths(name)["user"] / "save_files/OniHarness/test.sav"
                if path != str(copy_path) or not self.instances[name].get("save", self.config.get("save")):
                    raise ValueError("Checkpoint path is not explicitly approved; no native process was started")
        if not self.live(name):
            self.start(name)
        approved = self.state["instances"][name].get("approvedSaves", [])
        if path is not None:
            approved = [item for item in approved if item["path"] == path]
        if len(approved) != 1:
            raise RuntimeError("Select one exact approved checkpoint with reload --path when multiple saves are approved")
        if digest(approved[0]["path"]) != approved[0]["sha256"]:
            raise RuntimeError("Approved checkpoint SHA256 changed before reload")
        self.command(name, "pause")
        result = self.command(name, "load", **approved[0])
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            try:
                status = self.read_status(name)
                if (status.get("gameLoaded") and status.get("paused") is True and not status.get("loadingSave") and
                        status.get("activeSavePath") == approved[0]["path"] and status.get("ownedSave") == approved[0]["path"]):
                    return {"loaded": True, "status": status, "acceptance": result}
            except (OSError, RuntimeError, ValueError, KeyError):
                pass
            time.sleep(.25)
        raise RuntimeError("Exact disposable save did not finish loading and pausing")

    def verify_group(self, names, seconds=1):
        if not 0 < seconds <= 5:
            raise ValueError("Concurrent native step must be 0..5 wall seconds")
        if len(names) < 2 or len(set(names)) != len(names):
            raise ValueError("Concurrency verification requires at least two distinct complete games")
        before = {name: self.read_status(name) for name in names}
        pids = [data["pid"] for data in before.values()]
        if len(set(pids)) != len(names) or not any(self.instances[n]["graphics"] == "normal" for n in names):
            raise RuntimeError("Require distinct complete games including a graphical instance")
        if any(not s.get("gameLoaded") or s.get("paused") is not True for s in before.values()):
            raise RuntimeError("Every complete game must have a paused disposable colony loaded")
        self.preflight(len(names))
        started = utcnow()
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(names)) as pool:
            jobs = {name: pool.submit(self.command, name, "step", seconds=seconds) for name in names}
            results = {name: job.result() for name, job in jobs.items()}
        after = {}
        for name in names:
            deadline = time.monotonic() + 3
            while True:
                current = self.read_status(name)
                if current.get("paused") is True and current.get("simulationSeconds", 0) > before[name].get("simulationSeconds", 0):
                    after[name] = current
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("Completed step has no fresh paused positive-clock telemetry: " + name)
                time.sleep(.05)
        if any(after[n]["pid"] != before[n]["pid"] or after[n].get("paused") is not True or
               after[n].get("simulationSeconds", 0) <= before[n].get("simulationSeconds", 0) for n in names):
            raise RuntimeError("Every original process must advance its native clock and finish paused")
        intervals = [(datetime.fromisoformat(r["startedAt"].replace("Z", "+00:00")),
                      datetime.fromisoformat(r["completedAt"].replace("Z", "+00:00"))) for r in results.values()]
        overlap = (min(end for _, end in intervals) - max(start for start, _ in intervals)).total_seconds()
        if overlap <= 0:
            raise RuntimeError("Step intervals did not overlap; sequential execution is not a concurrent pass")
        resource = self.resources()
        if resource.get("violations"):
            raise RuntimeError("Group resource limits exceeded during the native step: " + json.dumps(resource["violations"]))
        receipt = {"schema": SCHEMA, "startedAt": started, "completedAt": utcnow(), "passed": True,
                   "kind": "complete-game-concurrency", "completeGameCount": len(names), "protocolClients": 0,
                   "overlapSeconds": overlap, "before": before, "after": after, "responses": results,
                   "resources": resource, "networkGameReconnectionVerified": False}
        destination = self.root / "receipts" / (uuid.uuid4().hex + ".json")
        atomic_json(destination, receipt)
        return {"receipt": str(destination), **receipt}

    def dispatch(self, request):
        action = request.get("action")
        if action == "status":
            return self.status()
        if action == "daemon-stop":
            return {"controllerStopping": True, "gamesLeftAlive": list(self.owned_identities())}
        with self.lock:
            if action == "start":
                return self.start(request["name"], request.get("timeout", 120))
            if action == "preflight":
                count = request.get("count", len(self.instances))
                if type(count) is not int or not 1 <= count <= len(self.instances):
                    raise ValueError("Preflight count must fit the declared complete-game group")
                receipt = {"capturedAt": utcnow(), "requestedCompleteGameCount": count, "protocolClients": 0,
                           "resources": self.resources(), "capacityReady": False}
                try:
                    self.preflight(count)
                    receipt["capacityReady"] = True
                except RuntimeError as error:
                    receipt["reason"] = str(error)
                destination = self.root / "capacity-preflight.json"
                atomic_json(destination, receipt)
                if not receipt["capacityReady"]:
                    raise RuntimeError(receipt["reason"] + "; receipt: " + str(destination))
                return receipt
            if action == "stop":
                return self.stop(request["name"])
            if action == "restart":
                self.stop(request["name"])
                return self.start(request["name"], request.get("timeout", 120))
            if action == "reload":
                return self.reload(request["name"], request.get("path"))
            if action == "approve-save":
                return self.approve_owned_save(request["name"], request["path"], request["sha256"])
            if action == "verify":
                try:
                    return self.verify_group(request["names"], request.get("seconds", 1))
                except (OSError, RuntimeError, ValueError, KeyError, TypeError) as error:
                    destination = self.root / "receipts" / ("failed-" + uuid.uuid4().hex + ".json")
                    atomic_json(destination, {"schema": SCHEMA, "capturedAt": utcnow(), "passed": False,
                                             "kind": "complete-game-concurrency", "names": request["names"],
                                             "protocolClients": 0, "error": str(error), "current": self.status()})
                    raise RuntimeError(str(error) + "; failed receipt: " + str(destination)) from error
            if action == "stop-all":
                return self.stop_all()
            if action == "plan":
                return {"steamCommand": self.launch_command(next(iter(self.instances))),
                        "launchOptions": f'python3 {shlex.quote(str(Path(__file__).with_name("steam_instance_entry.py")))} %command%',
                        "instances": {name: {**self.instances[name], "paths": {key: str(value) for key, value in self.paths(name).items()}}
                                      for name in self.instances},
                        "note": "Plan only; one Steam-selected resident wrapper starts complete games. Native single-instance behavior remains a runtime gate."}
            if action in ("proxy-start", "proxy-profile", "proxy-stop"):
                name = request["name"]
                if not NAME.fullmatch(name):
                    raise ValueError("Use a simple unique proxy name")
                if action == "proxy-start":
                    if name in self.proxies:
                        raise ValueError("Proxy already exists")
                    proxy = TCPFaultProxy("127.0.0.1", request["upstreamPort"], listen_port=request.get("listenPort", 0),
                                          profile=FaultProfile.from_dict(request.get("profile", {}))).start()
                    self.proxies[name] = proxy
                    return proxy.snapshot()
                proxy = self.proxies[name]
                if action == "proxy-profile":
                    proxy.set_profile(FaultProfile.from_dict(request["profile"]))
                    return proxy.snapshot()
                proxy.close()
                del self.proxies[name]
                return proxy.snapshot()
            raise ValueError("Unknown controller action")

    def monitor(self):
        previous = None
        while not self.monitor_stop.wait(2):
            try:
                if self.lock.acquire(blocking=False):
                    try:
                        self.reconcile_stop_tail()
                    finally:
                        self.lock.release()
                sample = sample_group(self.resource_identities(), limits=ResourceLimits(**self.config.get("resourceLimits", {})), previous=previous)
                previous = sample
                atomic_json(self.root / "resources.json", {"capturedAt": utcnow(), **sample})
                with (self.root / "resources.jsonl").open("a") as stream:
                    stream.write(json.dumps({"capturedAt": utcnow(), **sample}) + "\n")
            except (OSError, RuntimeError, ValueError) as error:
                atomic_json(self.root / "monitor-error.json", {"capturedAt": utcnow(), "error": str(error)})


def serve(root):
    controller = Controller(root)
    lock_file = (controller.root / "daemon.lock").open("a+")
    fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    endpoint = controller.root / "controller.sock"
    if len(str(endpoint).encode()) > 100:
        raise ValueError("Use a controller root short enough for a Unix socket (at most 100 bytes including /controller.sock)")
    if endpoint.exists():
        endpoint.unlink()

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            try:
                self.connection.settimeout(310)
                data = self.rfile.readline(MAX_MESSAGE + 1)
                if len(data) > MAX_MESSAGE or not data.endswith(b"\n"):
                    raise ValueError("Controller requests are bounded JSON lines")
                request = json.loads(data)
                response = {"ok": True, "result": controller.dispatch(request)}
            except Exception as error:
                response = {"ok": False, "error": str(error)}
            self.wfile.write((json.dumps(response, ensure_ascii=False) + "\n").encode())
            if response.get("ok") and request.get("action") == "daemon-stop":
                threading.Thread(target=self.server.shutdown, daemon=True).start()

    class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True
        block_on_close = False
        # Gameplay mutations are serialized in Controller. Status can still be
        # read while Steam/native loading takes minutes.
        request_queue_size = 8

        def __init__(self, *args, **kwargs):
            self.slots = threading.BoundedSemaphore(8)
            super().__init__(*args, **kwargs)

        def process_request(self, connection, address):
            if not self.slots.acquire(blocking=False):
                connection.sendall(b'{"ok":false,"error":"Controller request capacity reached"}\n')
                self.shutdown_request(connection)
                return
            try:
                super().process_request(connection, address)
            except BaseException:
                self.slots.release()
                raise

        def process_request_thread(self, *args, **kwargs):
            try:
                super().process_request_thread(*args, **kwargs)
            finally:
                self.slots.release()

    monitor = threading.Thread(target=controller.monitor, name="OniHarness-instance-monitor", daemon=True)
    monitor.start()
    try:
        with Server(str(endpoint), Handler) as server:
            os.chmod(endpoint, 0o600)
            atomic_json(controller.root / "daemon.json", {"pid": os.getpid(), "startedAt": utcnow(), "endpoint": str(endpoint)})
            server.serve_forever(poll_interval=.25)
    finally:
        controller.monitor_stop.set()
        monitor.join(timeout=3)
        for proxy in controller.proxies.values():
            proxy.close()
        endpoint.unlink(missing_ok=True)
        lock_file.close()


def initialize(args):
    root = args.root.resolve()
    if root.exists() and any(root.iterdir()):
        raise RuntimeError("Initialize requires a new empty test directory")
    root.mkdir(parents=True, mode=0o700)
    os.chmod(root, 0o700)
    mods = [{"id": "OniHarness.Runtime", "staticID": "LIghtJUNction.OniHarness.Runtime", "source": str(args.companion.resolve())}]
    for raw in args.mod:
        entry = json.loads(raw)
        mods.append(entry)
    document = {"schema": SCHEMA, "gamePath": str(args.game_path.resolve()), "mods": mods,
                "dlc": args.dlc, "save": str(args.save.resolve()) if args.save else None,
                "expectedInstanceBytes": 3 * 1024 ** 3, "reserveBytes": 1024 ** 3,
                "instances": [{"name": "host" if index == 0 else "guest" + str(index),
                               "identity": uuid.uuid4().hex, "graphics": "normal" if index == 0 else "headless",
                               "threads": 2, "port": args.base_port + index} for index in range(args.count)]}
    validate_config(document)
    atomic_json(root / "group.json", document)
    atomic_json(root / "controller.json", {"schema": SCHEMA, "createdAt": utcnow(), "token": uuid.uuid4().hex})
    print(json.dumps({"initialized": str(root), "completeGameInstances": args.count, "protocolClients": 0}))


def client(root, request):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(310)
        connection.connect(str(root.resolve() / "controller.sock"))
        connection.sendall((json.dumps(request) + "\n").encode())
        stream = connection.makefile("rb")
        data = stream.readline(16 * 1024 * 1024)
    response = json.loads(data)
    if not response.get("ok"):
        raise RuntimeError(response.get("error"))
    print(json.dumps(response["result"], ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path, help="Short, new private test directory, e.g. /tmp/oni-group")
    commands = parser.add_subparsers(dest="action", required=True)
    p = commands.add_parser("init")
    p.add_argument("--game-path", required=True, type=Path)
    p.add_argument("--companion", required=True, type=Path, help="Built companion mod folder including metadata")
    p.add_argument("--mod", action="append", default=[], help='Explicit test mod JSON: {"id":"X","staticID":"X","source":"/built/mod"}')
    p.add_argument("--save", type=Path, help="Original save is read-only; each instance gets a separate copy")
    p.add_argument("--count", type=int, choices=range(2, 17), default=4)
    p.add_argument("--base-port", type=int, default=28740)
    p.add_argument("--dlc", default="EXPANSION1_ID")
    commands.add_parser("serve", help="Foreground resident controller; leaves games intact if the daemon exits")
    for action in ("status", "plan", "stop-all", "daemon-stop"):
        commands.add_parser(action)
    for action in ("start", "stop", "restart", "reload"):
        p = commands.add_parser(action)
        p.add_argument("name")
        if action in ("start", "restart"):
            p.add_argument("--timeout", type=float, default=120)
        if action == "reload":
            p.add_argument("--path", help="Exact existing approved native checkpoint; never copied")
    p = commands.add_parser("approve-save", help="Approve a checkpoint generated by this instance's most recent stopped native run")
    p.add_argument("name")
    p.add_argument("path")
    p.add_argument("sha256")
    p = commands.add_parser("verify")
    p.add_argument("names", nargs="+")
    p.add_argument("--seconds", type=float, default=1)
    p = commands.add_parser("preflight")
    p.add_argument("--count", type=int, default=4)
    p = commands.add_parser("proxy-start")
    p.add_argument("name")
    p.add_argument("upstreamPort", type=int)
    p.add_argument("--listen-port", dest="listenPort", type=int, default=0)
    p.add_argument("--profile", type=json.loads, default={})
    p = commands.add_parser("proxy-profile")
    p.add_argument("name")
    p.add_argument("profile", type=json.loads)
    p = commands.add_parser("proxy-stop")
    p.add_argument("name")
    args = parser.parse_args()
    if args.action == "init":
        return initialize(args)
    if args.action == "serve":
        return serve(args.root)
    request = vars(args).copy()
    request.pop("root")
    return client(args.root, request)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Controller stopped; game processes were left intact.", file=sys.stderr)
        raise SystemExit(130)
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print("FAILED: " + str(error), file=sys.stderr)
        raise SystemExit(1)
