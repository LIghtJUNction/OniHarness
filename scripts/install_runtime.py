#!/usr/bin/env python3
"""Build and enable the optional companion from the user's installed Linux ONI files."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

from runtime_mcp import atomic_json

ROOT = Path(__file__).resolve().parents[1]
STATIC_ID = "LIghtJUNction.OniHarness.Runtime"


def game_pids():
    if not sys.platform.startswith("linux"):
        raise RuntimeError("Runtime installation/launch is currently verified on Linux only")
    result = subprocess.run(["pidof", "OxygenNotIncluded", "OxygenNotIncluded.x86_64"], capture_output=True, text=True)
    if result.returncode not in (0, 1):
        raise RuntimeError("Could not determine whether ONI is running")
    return [int(p) for p in result.stdout.split()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-path", required=True, type=Path)
    parser.add_argument("--mods-dir", required=True, type=Path, help="ONI user data's mods directory")
    parser.add_argument("--dlc", default="EXPANSION1_ID", help="DLC id to enable; use an empty string for base game")
    args = parser.parse_args()
    if game_pids():
        raise RuntimeError("Exit ONI before installing; no running game will be stopped automatically")
    manifest = args.mods_dir / "mods.json"
    document = json.loads(manifest.read_text(encoding="utf-8"))
    entries = document["mods"]
    matching = [m for m in entries if m.get("staticID") == STATIC_ID]
    if len(matching) > 1:
        raise RuntimeError("Duplicate companion entries; inspect the manifest before installing")
    entry = matching[0] if matching else None
    if entry is not None and entry["label"].get("distribution_platform") != 0:
        raise RuntimeError("Existing companion entry is not Local; inspect it before migrating")
    if entry is None and any(m.get("label", {}).get("id") == "OniHarness.Runtime" for m in entries):
        raise RuntimeError("Mod id already belongs to a different staticID; manifest left untouched")
    folder = args.mods_dir / "Local" / "OniHarness.Runtime"
    if folder.exists() and any(p.name not in {"OniHarness.Runtime.dll", "mod.yaml", "mod_info.yaml"} for p in folder.iterdir()):
        raise RuntimeError("Existing companion directory has unknown files; preserve it and inspect first")
    subprocess.run(["dotnet", "build", "runtime/OniHarness.Runtime", "-c", "Release",
                    "-p:OniGamePath=" + str(args.game_path.resolve()), "--nologo"], cwd=ROOT, check=True)
    backup = ROOT / "artifacts/runtime-install" / ("mods-before-" + uuid.uuid4().hex + ".json")
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(manifest, backup)
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "runtime/OniHarness.Runtime/bin/Release/net48/OniHarness.Runtime.dll", folder)
    (folder / "mod.yaml").write_text('title: "OniHarness Runtime"\ndescription: "Optional rendering control; native game simulation."\nstaticID: ' + STATIC_ID + '\n')
    (folder / "mod_info.yaml").write_text('minimumSupportedBuild: 744825\nAPIVersion: 2\nversion: "0.1.0"\n')
    if entry is None:
        entry = {"label": {"distribution_platform": 0, "id": "OniHarness.Runtime", "title": "OniHarness Runtime", "version": 0},
                 "status": 1, "enabled": True, "enabledForDlc": [], "crash_count": 0,
                 "reinstall_path": None, "staticID": STATIC_ID}
        entries.append(entry)
    entry["enabled"] = True
    if args.dlc not in entry.setdefault("enabledForDlc", []):
        entry["enabledForDlc"].append(args.dlc)
    atomic_json(manifest, document)
    print("Installed own companion only:", folder)
    print("Original mod manifest retained locally:", backup)
    print("No rendering changes occur until launched with --oniharness-control PATH.")


if __name__ == "__main__":
    main()
