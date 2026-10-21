"""Small native installation views; does not launch ONI or change its protection.

Each view has its own real launcher and therefore its own native installation
lock path. Assets remain shared with the installed game. Steam authorization,
boot configuration and existing locks are left intact.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid


LAUNCHER = "OxygenNotIncluded"
MARKER = ".oniharness-installation.json"
MAX_LAUNCHER_BYTES = 1024 * 1024


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _excluded(name):
    lower = name.lower()
    return (lower in {"unity.lock", "cache", "caches", "logs", "screenshots", "imgui.ini"}
            or lower.endswith(".log") or ".log." in lower
            or lower.endswith("_log.txt") or lower.startswith("mono_crash.")
            or lower == "core" or lower.startswith("core."))


def prepare_installation_view(game_path, view_path):
    """Prepare or verify a private view and return a JSON-safe receipt.

    Unknown existing entries fail before any writes. A marker records ownership;
    a changed installed launcher may replace only a previously owned, unmodified
    launcher. Existing unity.lock files are never removed or rewritten.
    """
    game = Path(game_path).resolve(strict=True)
    requested_view = Path(view_path).absolute()
    if requested_view.is_symlink():
        raise ValueError("Installation view must be a real directory, not a symlink")
    view = requested_view.resolve()
    if not game.is_dir() or view == game or game in view.parents or view in game.parents:
        raise ValueError("Installation and view must be separate, non-overlapping directories")
    if view.exists() and not view.is_dir():
        raise ValueError("Installation view must be a directory")
    source_launcher = game / LAUNCHER
    launcher_info = source_launcher.lstat()
    if (not stat.S_ISREG(launcher_info.st_mode)
            or not 4 <= launcher_info.st_size <= MAX_LAUNCHER_BYTES):
        raise ValueError("Installed launcher must be a small, real ELF file (at most 1 MiB)")
    with source_launcher.open("rb") as source:
        launcher_bytes = source.read(MAX_LAUNCHER_BYTES + 1)
    if len(launcher_bytes) > MAX_LAUNCHER_BYTES or not launcher_bytes.startswith(b"\x7fELF"):
        raise ValueError("Installed launcher must be an ELF file at most 1 MiB")
    launcher_hash = hashlib.sha256(launcher_bytes).hexdigest()
    boot = game / "OxygenNotIncluded_Data" / "boot.config"
    boot_hash = _sha256(boot)
    entries = {entry.name: entry for entry in game.iterdir()
               if entry.name != LAUNCHER and not _excluded(entry.name)}
    if MARKER in entries:
        raise ValueError("Installed game contains a reserved Harness marker")
    marker_path = view / MARKER
    previous = None
    if marker_path.is_symlink():
        raise ValueError("Installation marker must not be a symlink")
    if marker_path.exists():
        if not marker_path.is_file():
            raise ValueError("Installation marker is not a regular file")
        previous = json.loads(marker_path.read_text(encoding="utf-8"))
        if (not isinstance(previous, dict)
                or previous.get("schema") != 1 or previous.get("gamePath") != str(game)
                or previous.get("viewPath") != str(view)):
            raise ValueError("Installation view belongs to a different source or directory")
    destination = view / LAUNCHER
    if view.exists():
        for entry in view.iterdir():
            if entry.name == "unity.lock":
                if entry.is_symlink() or not entry.is_file():
                    raise ValueError("Existing native lock must be a private regular file")
                continue
            if entry.name == MARKER:
                continue
            if entry.name == LAUNCHER:
                if entry.is_symlink() or not entry.is_file() or previous is None:
                    raise ValueError("Existing launcher is not an owned real file")
                if _sha256(entry) != previous.get("launcherSha256"):
                    raise ValueError("Existing view launcher was modified; refusing to overwrite it")
                source_info = source_launcher.stat()
                if (entry.stat().st_dev, entry.stat().st_ino) == (source_info.st_dev, source_info.st_ino):
                    raise ValueError("View launcher must have its own inode")
                continue
            if (entry.name not in entries or not entry.is_symlink()
                    or os.readlink(entry) != str(entries[entry.name])):
                raise ValueError("Unknown installation view entry: " + entry.name)
    # Check the source again before touching the private view.
    if _sha256(source_launcher) != launcher_hash or _sha256(boot) != boot_hash:
        raise RuntimeError("Installed game changed during view preparation")
    view.mkdir(parents=True, exist_ok=True)
    copied_bytes = 0
    if not destination.exists() or _sha256(destination) != launcher_hash:
        temporary = view / (".launcher-" + uuid.uuid4().hex)
        try:
            with temporary.open("xb") as target:
                target.write(launcher_bytes)
                target.flush()
                os.fsync(target.fileno())
            temporary.chmod(stat.S_IMODE(launcher_info.st_mode))
            temporary.replace(destination)
            copied_bytes = len(launcher_bytes)
        finally:
            temporary.unlink(missing_ok=True)
    for name, entry in entries.items():
        link = view / name
        if not link.is_symlink():
            link.symlink_to(entry, target_is_directory=entry.is_dir())
    if _sha256(source_launcher) != launcher_hash or _sha256(boot) != boot_hash:
        raise RuntimeError("Installed game changed during view preparation")
    receipt = {"schema": 1, "gamePath": str(game), "viewPath": str(view),
               "executablePath": str(destination), "launcherSha256": launcher_hash,
               "bootConfigSha256": boot_hash, "copiedBytes": copied_bytes,
               "sharedEntries": sorted(entries), "nativeSingletonPreserved": True}
    temporary_marker = view / (".marker-" + uuid.uuid4().hex)
    try:
        with temporary_marker.open("x", encoding="utf-8") as target:
            json.dump(receipt, target, indent=2)
            target.write("\n")
        temporary_marker.replace(marker_path)
    finally:
        temporary_marker.unlink(missing_ok=True)
    return receipt
