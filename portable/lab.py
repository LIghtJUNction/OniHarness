#!/usr/bin/env python3
"""Portable Linux ONI lab. Steam authentication stays on the receiving machine."""
import argparse
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent
HARNESS = ROOT / "sources/OniHarness"
MODS = ROOT / "sources/OniMods"
sys.path.insert(0, str(HARNESS / "scripts"))
from runtime_mcp import Client, atomic_json
from install_runtime import game_pids
from runtime import read_status


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        while data := stream.read(1024 * 1024): h.update(data)
    return h.hexdigest()


def run(command, cwd=ROOT):
    subprocess.run(list(map(str, command)), cwd=cwd, check=True)


def user_data():
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "unity3d/Klei/Oxygen Not Included"


def steamapps(value=None):
    if value: return Path(value).expanduser().resolve()
    for p in (Path.home()/".local/share/Steam/steamapps", Path.home()/".steam/steam/steamapps"):
        if p.is_dir(): return p.resolve()
    raise RuntimeError("Steam is required. Install the supplied official client installer or your distro's Steam package, then sign in with your own licensed account.")


def verify():
    manifest = json.loads((ROOT / "MANIFEST.json").read_text())
    for item in manifest["files"]:
        p = ROOT / item["path"]
        if p.stat().st_size != item["bytes"] or digest(p) != item["sha256"]:
            raise RuntimeError("Bundle checksum mismatch: " + item["path"])
        p.chmod(item["mode"])
    print("PASS bundle bytes and executable modes:", len(manifest["files"]), "files")


def build():
    cfg = ROOT / "NuGet.Config"
    managed = ROOT / "game/OxygenNotIncluded_Data/Managed"
    for name in ("CycleTrim", "OniMcp"):
        run(["dotnet", "build", MODS / "mods" / name, "-c", "Release", "--configfile", cfg,
             "-p:OniManagedPath="+str(managed), "-p:OutputPath="+str(ROOT/"work/build"/name)+"/"])
    run(["dotnet", "build", HARNESS/"runtime/OniHarness.Runtime", "-c", "Release", "--configfile", cfg,
         "-p:OniGamePath="+str(ROOT/"game")])
    run(["dotnet", "run", "--project", MODS/"benchmarks/CycleTrim.BrainBenchmarks", "-c", "Release",
         "-p:ImportDirectoryBuildProps=false"])
    run([sys.executable, HARNESS/"scripts/verify.py"] , HARNESS)
    for folder,name in (("CycleTrimHarness","CycleTrim"),("OniMcpHarness","OniMcp")):
        for filename in (name+".dll","mod.yaml","mod_info.yaml"):
            shutil.copy2(ROOT/"work/build"/name/filename,ROOT/"mods"/folder/filename)
    shutil.copy2(HARNESS/"runtime/OniHarness.Runtime/bin/Release/net48/OniHarness.Runtime.dll",
                 ROOT/"mods/OniHarness.Runtime/OniHarness.Runtime.dll")
    print("Build passed; bundle mod staging now contains your rebuilt DLLs. Exit ONI, then run install.")


def install():
    if game_pids(): raise RuntimeError("Exit ONI before installing the lab mods")
    data = user_data(); directory = data / "mods"; manifest = directory/"mods.json"
    document = json.loads(manifest.read_text()) if manifest.exists() else {"version":1,"mods":[],"mod_load_in_progress":False}
    backup = Path.home()/"Backups/OniHarness/portable"/date.today().isoformat()/uuid.uuid4().hex
    backup.mkdir(parents=True)
    if manifest.exists(): shutil.copy2(manifest, backup/"mods.json")
    for definition in json.loads((ROOT/"lab-mods.json").read_text()):
        folder, static = definition["folder"], definition["staticID"]
        matching = [m for m in document["mods"] if m.get("staticID")==static]
        if len(matching)>1: raise RuntimeError("Duplicate mod identity: "+static)
        entry = matching[0] if matching else None
        if entry and (entry["label"].get("distribution_platform")!=0 or entry["label"]["id"]!=folder):
            raise RuntimeError("Existing mod uses another installation; preserve it and inspect: "+static)
        target=directory/"Local"/folder
        if target.exists(): shutil.copytree(target, backup/folder)
        shutil.copytree(ROOT/"mods"/folder, target, dirs_exist_ok=True)
        if entry is None:
            entry={"label":{"distribution_platform":0,"id":folder,"title":definition["title"],"version":0},
                   "status":1,"enabled":True,"enabledForDlc":[],"crash_count":0,"reinstall_path":None,"staticID":static}
            document["mods"].append(entry)
        entry["enabled"]=True;entry["enabledForDlc"]=list(dict.fromkeys([*(entry.get("enabledForDlc") or []),"EXPANSION1_ID"]))
    directory.mkdir(parents=True,exist_ok=True);atomic_json(manifest,document)
    target=data/"save_files/OniHarness-Lab/OniHarness-Lab.sav";target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists() and digest(target)!=digest(ROOT/"fixture/test.sav"):
        raise RuntimeError("Existing test save differs; preserved: "+str(target))
    shutil.copy2(ROOT/"fixture/test.sav",target)
    print("Installed three lab mods and test copy. Existing mod entries preserved. Backup:",backup)
    print("For the recorded minimal baseline, disable other mods in your own test profile. Native mod reinstall can require another enable/start cycle.")


def import_game(folder):
    destination=steamapps(folder);target=destination/"common/OxygenNotIncluded"
    if target.exists():
        check_game(target);print("Existing Steam game matches tested core files:",target);return
    if subprocess.run(["pgrep","-x","steam"],capture_output=True).returncode==0:
        raise RuntimeError("Close Steam before importing a new app manifest; no client will be stopped automatically")
    manifest=destination/"appmanifest_457140.acf"
    if manifest.exists(): raise RuntimeError("Existing Steam app manifest preserved; inspect installation first")
    target.parent.mkdir(parents=True,exist_ok=True);target.symlink_to(ROOT/"game",target_is_directory=True)
    shutil.copy2(ROOT/"steam/appmanifest_457140.acf",manifest)
    print("Imported bundled game into this existing Steam library:",target)
    print("Start Steam and sign in with your own ONI/Spaced Out license. Do not move the extracted bundle while its game symlink is in use.")


def check_game(target):
    checks=json.loads((ROOT/"game-core-hashes.json").read_text())
    for name, expected in checks.items():
        if not (target/name).is_file() or digest(target/name)!=expected:
            raise RuntimeError("Steam game differs from supplied 744825 build: "+name)


def state(): return json.loads((ROOT/"work/session.json").read_text())


def launch(folder):
    check_game(steamapps(folder)/"common/OxygenNotIncluded")
    session=ROOT/"work"/("session-"+uuid.uuid4().hex[:12]);session.mkdir(parents=True)
    control=session/"layout.ctrl";control.write_text("baseline init")
    atomic_json(ROOT/"work/session.json",{"output":str(session),"control":str(control)})
    run([sys.executable,HARNESS/"scripts/runtime.py","--output",session,"launch",
         "--game-arg=--cycletrim-layout-control="+str(control)])
    log=(session/"player.log").read_text(errors="replace")
    if "[CycleTrim][LayoutLab] baseline; explicit control active" not in log:
        raise RuntimeError("CycleTrim was not loaded. Inspect native mod enablement; keep this failed session.")


def load():
    s=state();output=Path(s["output"]);read_status(output)
    save=user_data()/"save_files/OniHarness-Lab/OniHarness-Lab.sav"
    client=Client("http://127.0.0.1:8788/mcp/",os.environ.get("ONIHARNESS_MCP_TOKEN"))
    client.call("game_control",{"domain":"launch","action":"start","path":str(save),"resume":False,"forceLoad":True,"confirm":True})
    until=time.monotonic()+120
    while time.monotonic()<until:
        try:
            current=read_status(output)
            if current.get("gameLoaded") and current.get("duplicants")==3 and current.get("paused") is True: break
        except RuntimeError: pass
        time.sleep(.25)
    else: raise RuntimeError("Fixture did not load and pause; retain player.log")
    run([sys.executable,HARNESS/"scripts/runtime.py","--output",output,"step","--seconds","1","--release-native-pause"])


def stop():
    s=state();status=read_status(Path(s["output"]));pid=status["pid"]
    command=Path("/proc",str(pid),"cmdline").read_bytes()
    if str(Path(s["output"]).resolve()).encode() not in command or b"--oniharness-headless" not in command:
        raise RuntimeError("Process is not this lab's owned headless session")
    Client("http://127.0.0.1:8788/mcp/",os.environ.get("ONIHARNESS_MCP_TOKEN")).call("game_control",{"domain":"speed","action":"pause"})
    os.kill(pid,signal.SIGTERM)
    until=time.monotonic()+30
    while Path("/proc",str(pid)).exists() and time.monotonic()<until: time.sleep(.2)
    if Path("/proc",str(pid)).exists(): raise RuntimeError("Owned process still exiting; inspect before installing mods")
    print("Owned game exited")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=["verify","doctor","build","install","import-game","launch","load","measure","micro","stop"])
    parser.add_argument("--steamapps");parser.add_argument("--cpu",type=int,default=min(os.sched_getaffinity(0)))
    args=parser.parse_args();(ROOT/"work").mkdir(exist_ok=True)
    if args.command=="verify": verify()
    elif args.command=="doctor":
        print("Python",sys.version.split()[0],"Steam",shutil.which("steam"),"CPU affinity",sorted(os.sched_getaffinity(0)))
        run(["dotnet","--version"]);check_game(ROOT/"game")
        from pmu import CounterGroup
        try:
            counter=CounterGroup(0);counter.start();sum(range(100000));print("PMU",counter.stop());counter.close()
        except OSError as error:
            print("PMU unavailable:",error,". CPU/native-clock testing remains available with measure_steps.py; do not claim cache measurements.")
        print("PASS local toolchain/game. Steam login/license and a reachable Steam client are separate prerequisites.")
    elif args.command=="build": build()
    elif args.command=="install": install()
    elif args.command=="import-game": import_game(args.steamapps)
    elif args.command=="launch": launch(args.steamapps)
    elif args.command=="load": load()
    elif args.command=="measure":
        s=state();run([sys.executable,HARNESS/"scripts/measure_fetch_layout.py","--output",s["output"],"--control",s["control"],"--cpu",args.cpu])
    elif args.command=="micro":
        run([sys.executable,HARNESS/"scripts/measure_candidate_layout.py","--assembly",MODS/"benchmarks/CycleTrim.BrainBenchmarks/bin/Release/net10.0/CycleTrim.BrainBenchmarks.dll",
             "--output",ROOT/"work/host-layout.json","--cpu",args.cpu])
    elif args.command=="stop": stop()


if __name__=="__main__": main()
