"""Read-only Linux diagnostics for a Steam game missing companion telemetry."""
import os
from pathlib import Path
import stat
import sys

_ENV_KEYS = {b"HOME", b"XDG_CONFIG_HOME"}
_GAME_NAMES = {"OxygenNotIncluded", "OxygenNotIncluded.x86_64"}


def _matches_control(argv, control):
    values = []
    for index, value in enumerate(argv):
        if value == "--oniharness-control":
            values.append(argv[index + 1] if index + 1 < len(argv) else None)
        elif value.startswith("--oniharness-control="):
            values.append(value.split("=", 1)[1])
    return values == [str(control)]


def _file_present(path):
    try:
        return stat.S_ISREG(path.stat().st_mode)
    except FileNotFoundError:
        return False
    except OSError:
        return None


def diagnose_startup(control, *, proc_root=Path("/proc"), default_home=None, platform=None):
    """Inspect only ONI processes carrying this exact, unambiguous control path."""
    platform = sys.platform if platform is None else platform
    report = {"platform": platform, "controlDirectory": str(Path(control).resolve()),
              "matchingProcesses": []}
    if not platform.startswith("linux"):
        report["reason"] = "Process environment diagnostics are available on Linux only."
        return report
    control = Path(control).resolve()
    default_config = (Path(default_home) if default_home is not None else Path.home()) / ".config"
    report["defaultHostConfigHome"] = str(default_config)
    try:
        processes = sorted(Path(proc_root).iterdir(), key=lambda p: p.name)
    except OSError as error:
        report["reason"] = "Process listing unavailable: " + type(error).__name__
        return report
    for process in processes:
        if not process.name.isdecimal():
            continue
        try:
            if Path(os.readlink(process / "exe")).name not in _GAME_NAMES:
                continue
            argv = [os.fsdecode(arg) for arg in (process / "cmdline").read_bytes().split(b"\0") if arg]
            if not _matches_control(argv, control):
                continue
        except OSError:
            continue
        row = {"pid": int(process.name)}
        report["matchingProcesses"].append(row)
        try:
            # Only these two environment entries can enter the diagnostic result.
            environment = {}
            for entry in (process / "environ").read_bytes().split(b"\0"):
                key, separator, value = entry.partition(b"=")
                if separator and key in _ENV_KEYS:
                    environment[key.decode("ascii")] = os.fsdecode(value)
            row["environment"] = environment
        except OSError as error:
            row["reason"] = "Allowed process environment unavailable: " + type(error).__name__
            continue
        home = environment.get("HOME")
        configured = environment.get("XDG_CONFIG_HOME")
        if configured:
            config = Path(configured)
            origin = "XDG_CONFIG_HOME"
        elif home:
            config = Path(home) / ".config"
            origin = "HOME/.config"
        else:
            row["reason"] = "HOME and XDG_CONFIG_HOME are absent; effective config is unknown."
            continue
        if not config.is_absolute():
            row["reason"] = origin + " is not absolute; effective config is unknown."
            continue
        mods = config / "unity3d" / "Klei" / "Oxygen Not Included" / "mods"
        runtime = mods / "Local" / "OniHarness.Runtime"
        dll = runtime / "OniHarness.Runtime.dll"
        present = _file_present(dll)
        row.update(configSource=origin, effectiveConfigHome=str(config),
                   differsFromDefaultHostConfig=config != default_config,
                   modsDirectory=str(mods), localModsDirectory=str(mods / "Local"),
                   runtimeDirectory=str(runtime), runtimeDll=str(dll),
                   runtimeDllPresent=present, modYamlPresent=_file_present(runtime / "mod.yaml"))
        if present is False:
            row["next"] = ("After normal game exit, install and enable OniHarness Runtime in this effective "
                           "mods directory. Steam's child environment selects this location, which can differ "
                           "from the launcher's default host config. No files or processes were changed.")
        elif present is True:
            row["next"] = ("The Runtime DLL exists; file presence does not prove the mod is enabled or loaded. "
                           "Inspect this mods directory's mods.json and the requested player.log.")
        else:
            row["next"] = "Runtime file presence could not be determined; inspect directory permissions."
    if not report["matchingProcesses"]:
        report["reason"] = "No running ONI process carries this exact control directory."
    return report
