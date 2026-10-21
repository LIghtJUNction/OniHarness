#!/usr/bin/env python3
"""Official Steam Launch Options wrapper: python3 /absolute/this.py %command%.

Ordinary Steam launches pass through unchanged. Explicit Harness instances get a
separate Linux XDG configuration root before Unity starts. This does not change
Steam authorization or the game's single-instance protection.
"""
import json
import os
from pathlib import Path
import sys
import time
import subprocess
import hashlib
import fcntl


def argument(command, option):
    if command.count(option) != 1:
        raise ValueError("Expected exactly one " + option)
    position = command.index(option) + 1
    if position >= len(command):
        raise ValueError("Missing " + option)
    return command[position]


def steam_ancestor():
    parent = os.getppid()
    for _ in range(16):
        if parent <= 1:
            return False
        try:
            directory = Path("/proc") / str(parent)
            executable = (directory / "exe").resolve(strict=True)
            if executable.name == "steam":
                return True
            fields = (directory / "stat").read_text().rsplit(")", 1)[1].split()
            parent = int(fields[1])
        except OSError:
            return False
    return False


def unique_game_position(command, executable):
    expected = Path(executable).resolve(strict=True)
    positions = []
    for index, value in enumerate(command):
        if Path(value).is_absolute():
            try:
                if Path(value).resolve(strict=True) == expected:
                    positions.append(index)
            except OSError:
                pass
    if len(positions) != 1:
        raise RuntimeError("Steam command must contain the configured game executable exactly once")
    return positions[0]


def preserved_steam_prefix(command, game_path):
    """Validate known original Steam command shapes without dropping wrappers."""
    executable = Path(game_path).resolve(strict=True) / "OxygenNotIncluded"
    position = unique_game_position(command, executable)
    prefix = command[:position]
    if not prefix:
        return prefix
    # Observed native Linux Steam Runtime chain. Unknown chains fail explicitly;
    # the controller never retries by running only the final game executable.
    if (len(prefix) != 9 or Path(prefix[0]).name != "steam-launch-wrapper" or prefix[1] != "--" or
            Path(prefix[2]).name != "reaper" or prefix[3:6] != ["SteamLaunch", "AppId=457140", "--"] or
            Path(prefix[6]).name != "_v2-entry-point" or prefix[7:] != ["--verb=waitforexitandrun", "--"]):
        raise RuntimeError("Unsupported Steam launch chain; no direct-game fallback")
    game = Path(game_path).resolve(strict=True)
    if game.parent.name != "common" or game.parent.parent.name != "steamapps":
        raise RuntimeError("Cannot establish the configured Steam installation root")
    steam_root = game.parents[2]
    for index in (0, 2, 6):
        path = Path(prefix[index]).resolve(strict=True)
        if steam_root not in path.parents or not path.is_file():
            raise RuntimeError("Steam runtime command escaped its installed Steam root")
    if Path(prefix[6]).parent.name != "SteamLinuxRuntime_sniper":
        raise RuntimeError("Unknown Steam runtime selection; no direct-game fallback")
    return prefix


def prepare(command, environment, apply_affinity=True):
    if not command:
        raise ValueError("Steam must supply %command%; direct game invocation is not supported")
    if not steam_ancestor():
        raise RuntimeError("This wrapper must be invoked by the existing authorized Steam client")
    if "--oniharness-instance" not in command:
        return dict(environment), None
    root = Path(argument(command, "--oniharness-control")).resolve(strict=True)
    profile = json.loads((root / "instance-profile.json").read_text())
    identity = argument(command, "--oniharness-instance")
    expected = Path(argument(command, "--oniharness-user-root")).resolve(strict=True)
    if profile["identity"] != identity or Path(profile["userRoot"]).resolve() != expected:
        raise RuntimeError("Launch profile and instance identity do not match")
    native_suffix = Path("unity3d/Klei/Oxygen Not Included")
    if tuple(expected.parts[-3:]) != native_suffix.parts:
        raise RuntimeError("Expected the documented Linux Unity native data path")
    xdg_root = expected.parents[2]
    base = root.parent
    if xdg_root != base / "xdg" or root != base / "control":
        raise RuntimeError("XDG and control roots must belong to the same private instance")
    group = json.loads((base.parents[1] / "group.json").read_text())
    view_launcher = base / "installation/OxygenNotIncluded"
    position = unique_game_position(command, view_launcher)
    executable = Path(command[position]).resolve(strict=True)
    from instance_installation import prepare_installation_view
    view = prepare_installation_view(group["gamePath"], base / "installation")
    if view["launcherSha256"] != hashlib.sha256(executable.read_bytes()).hexdigest():
        raise RuntimeError("Installation view launcher hash changed")
    matching = [item for item in group["instances"] if item["identity"] == identity]
    if len(matching) != 1:
        raise RuntimeError("The instance is not unique in the resident controller group")
    env = dict(environment)
    env["XDG_CONFIG_HOME"] = str(xdg_root)
    threads = matching[0]["threads"]
    env.update({"OMP_NUM_THREADS": str(threads), "OPENBLAS_NUM_THREADS": str(threads),
                "MONO_THREADS_PER_CPU": "1"})
    allowed = sorted(os.sched_getaffinity(0))
    index = group["instances"].index(matching[0])
    cpus = sorted({allowed[(index * threads + offset) % len(allowed)] for offset in range(min(threads, len(allowed)))})
    if apply_affinity:
        os.sched_setaffinity(0, cpus)
    receipt = {"schema": 1, "pid": os.getpid(), "enteredAtUnix": time.time(), "identity": identity,
               "executable": str(executable), "xdgConfigHome": str(xdg_root), "expectedNativeUserRoot": str(expected),
               "cpuAffinity": cpus, "steamAncestorVerified": True}
    return env, (root, receipt)


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value))
    temporary.replace(path)


def supervisor(command):
    """Stay in Steam's normal launch chain; never remove a native instance lock."""
    if not steam_ancestor():
        raise RuntimeError("Group launcher must be selected through Steam Launch Options")
    root = Path(argument(command, "--oniharness-group")).resolve(strict=True)
    marker = json.loads((root / "controller.json").read_text())
    if argument(command, "--oniharness-group-token") != marker["token"]:
        raise RuntimeError("Group launch token mismatch")
    lease = (root / "steam-launcher.lock").open("a+")
    fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
    group = json.loads((root / "group.json").read_text())
    prefix = preserved_steam_prefix(command, group["gamePath"])
    names = {item["name"]: item for item in group["instances"]}
    children = {}
    handled = None
    nonce = os.urandom(16).hex()
    next_status = 0
    while True:
        now = time.monotonic()
        if now >= next_status:
            write_json(root / "launcher-status.json", {"pid": os.getpid(), "nonce": nonce, "updatedAtUnix": time.time(),
                       "steamAncestorVerified": True, "children": {name: {"pid": child.pid, "exitCode": child.poll()}
                                                                  for name, child in children.items()},
                       "steamRuntimePrefix": prefix, "nativeGameFallbackAllowed": False})
            next_status = now + .5
        request_path = root / "launcher-command.json"
        if not request_path.exists():
            time.sleep(.05)
            continue
        if request_path.stat().st_size > 16384:
            raise ValueError("Launcher command exceeds 16 KiB")
        try:
            request = json.loads(request_path.read_text())
        except (OSError, ValueError):
            time.sleep(.05)
            continue
        if request.get("id") == handled:
            time.sleep(.05)
            continue
        handled = request.get("id")
        response = {"id": handled, "nonce": nonce, "ok": False}
        try:
            if request.get("nonce") != nonce:
                raise ValueError("Launcher run nonce mismatch")
            if request.get("action") == "shutdown":
                if any(child.poll() is None for child in children.values()):
                    raise RuntimeError("Orderly exit each game before stopping its Steam launcher")
                response["ok"] = True
                write_json(root / "launcher-response.json", response)
                return
            if request.get("action") != "start" or request.get("name") not in names:
                raise ValueError("Unknown launcher action or instance")
            name = request["name"]
            instance = names[name]
            if name in children and children[name].poll() is None:
                raise RuntimeError("Requested instance is already alive")
            base = root / "instances" / name
            user = base / "xdg/unity3d/Klei/Oxygen Not Included"
            control = base / "control"
            from instance_installation import prepare_installation_view
            view = prepare_installation_view(group["gamePath"], base / "installation")
            argv = prefix + [view["executablePath"], "--oniharness-control", str(control),
                    "--oniharness-instance", instance["identity"], "--oniharness-user-root", str(user),
                    "--oniharness-port", str(instance["port"]), "-logFile", str(base / "logs/player.log"),
                    "-job-worker-count", str(instance["threads"])]
            if instance["graphics"] == "headless":
                argv.extend(["-batchmode", "-nographics", "--oniharness-headless"])
            from instance_arguments import resolve_game_arguments
            launch_nonce = os.urandom(16).hex()
            external_args = resolve_game_arguments(instance.get("gameArgs", []), control, launch_nonce)
            argv.extend(external_args)
            environment, receipt = prepare(argv, os.environ, apply_affinity=False)
            work = base / "work"
            work.mkdir(exist_ok=True)
            cpus = receipt[1]["cpuAffinity"]
            with (base / "logs/wrapper-launch.log").open("ab") as output:
                child = subprocess.Popen(argv, cwd=work, env=environment, stdout=output, stderr=subprocess.STDOUT,
                                         start_new_session=True, preexec_fn=lambda: os.sched_setaffinity(0, cpus))
            children[name] = child
            receipt[1]["pid"] = child.pid
            receipt[1]["steamLauncherPid"] = os.getpid()
            receipt[1]["workingDirectory"] = str(work)
            receipt[1]["steamRuntimePrefix"] = prefix
            receipt[1]["nativeGameFallbackAllowed"] = False
            receipt[1]["externalControlRunNonce"] = launch_nonce
            receipt[1]["resolvedGameArgs"] = external_args
            write_json(control / "wrapper-entry.json", receipt[1])
            response.update({"ok": True, "pid": child.pid, "name": name})
        except (OSError, ValueError, RuntimeError, KeyError) as error:
            response["error"] = str(error)
        write_json(root / "launcher-response.json", response)


def main():
    command = sys.argv[1:]
    if "--oniharness-group" in command:
        return supervisor(command)
    environment, result = prepare(command, os.environ)
    if result:
        root, receipt = result
        temporary = root / "wrapper-entry.tmp"
        temporary.write_text(json.dumps(receipt))
        temporary.replace(root / "wrapper-entry.json")
    # This exec is the command selected by Steam's documented Launch Options.
    # There is no standalone direct-launch fallback in the controller.
    os.execvpe(command[0], command, environment)


if __name__ == "__main__":
    main()
