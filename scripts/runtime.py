#!/usr/bin/env python3
"""Explicit Steam launch, reduced rendering, paused observations and optional text spectator."""
import argparse
from datetime import datetime, timezone, timedelta
import functools
import http.server
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid

from install_runtime import game_pids
from runtime_mcp import Client, atomic_json

ROOT = Path(__file__).resolve().parents[1]


def observe(client, directory, intent="Read-only observation", bounds=None):
    client.call("game_control", {"domain": "speed", "action": "pause"})
    status = client.call("game_control", {"domain": "speed", "action": "time"})
    if status.get("isPaused") is not True:
        raise RuntimeError("Pause was not verified; observation aborted")
    snapshot = client.call("world_editor", {"command": "read", "path": "/active/index.md", "includeState": True})
    if bounds is None:
        camera = client.call("navigation_control", {"action": "get_view"})["position"]
        x, y = math.floor(camera["x"]), math.floor(camera["y"])
        bounds = (max(0, x - 16), max(0, y - 12), x + 15, y + 11)
    x1, y1, x2, y2 = bounds
    if x1 < 0 or y1 < 0 or x2 < x1 or y2 < y1 or (x2 - x1 + 1) * (y2 - y1 + 1) > 1600:
        raise ValueError("Map bounds must be ordered, nonnegative and at most 1600 cells")
    arguments = {"command": "zoom", "path": "/active/map/viewport.md", "format": "edit", "compact": False,
                 "syncView": False, "focusCamera": False, "x1": x1, "y1": y1, "x2": x2, "y2": y2, "maxCells": 1600}
    text_map = client.call("world_editor", arguments)
    if not text_map.get("text"):
        raise RuntimeError("No virtual map text returned")
    dupes = client.call("world_editor", {"command": "read", "path": "/active/dupes/index.md"})
    directory = Path(directory)
    observation = {"schema": 1, "capturedAt": datetime.now(timezone.utc).isoformat(), "paused": True,
                   "intent": intent, "time": status, "snapshot": snapshot, "map": text_map,
                   "mapRequest": arguments, "dupes": dupes}
    action_file = directory / "action.json"
    if action_file.exists():
        observation["lastAction"] = json.loads(action_file.read_text(encoding="utf-8"))
    atomic_json(directory / "latest.json", observation)
    with (directory / "observations.jsonl").open("a", encoding="utf-8") as output:
        output.write(json.dumps(observation, ensure_ascii=False) + "\n")
    shutil.copyfile(ROOT / "scripts/text-view.html", directory / "index.html")
    return observation


def launch(args):
    if game_pids():
        raise RuntimeError("ONI is already running; no launch or restart was performed")
    steam = shutil.which("steam")
    if not steam:
        raise RuntimeError("Steam client is required; direct game execution is never used")
    control = args.output.resolve()
    command = [steam, "-applaunch", "457140", "--oniharness-control", str(control), "-logFile", str(control / "player.log")]
    if args.graphics == "none":
        command.extend(["-batchmode", "-nographics"])
    if args.dry_run:
        print(json.dumps({"dryRun": True, "command": command}, ensure_ascii=False, indent=2))
        return
    args.output.mkdir(parents=True, exist_ok=True)
    atomic_json(control / "render.json", {"mode": "normal" if args.graphics == "normal" else "reduced", "interval": args.interval, "updateRate": args.update_rate})
    atomic_json(control / "launch.json", {"requestedAt": datetime.now(timezone.utc).isoformat(), "graphics": args.graphics,
                                         "command": command, "status": "requested", "note": "Launch request is not proof of loaded gameplay or reduced rendering."})
    log = (control / "steam-launch.log").open("ab")
    process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    log.close()
    print(json.dumps({"launchRequested": True, "steamLauncherPid": process.pid, "graphicsRequested": args.graphics,
                      "verify": str(control / "status.json"), "note": "Check live companion status; no compatibility result is implied."}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8788/mcp/")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/runtime")
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("launch", help="Explicitly launch the real game through Steam")
    p.add_argument("--graphics", choices=["normal", "reduced", "none"], default="reduced")
    p.add_argument("--interval", type=int, choices=range(1, 121), default=60, metavar="1..120")
    p.add_argument("--update-rate", type=int, choices=range(15, 241), default=30, metavar="15..240")
    p.add_argument("--dry-run", action="store_true")
    p = commands.add_parser("render", help="Change companion render policy, without changing simulation speed")
    p.add_argument("mode", choices=["normal", "reduced"])
    p.add_argument("--interval", type=int, choices=range(1, 121), default=60, metavar="1..120")
    p.add_argument("--update-rate", type=int, choices=range(15, 241), default=30, metavar="15..240")
    commands.add_parser("status", help="Read companion telemetry; reject stale or exited sessions")
    p = commands.add_parser("observe", help="Pause and record exactly the map returned to the AI")
    p.add_argument("--intent", default="只读观测")
    p.add_argument("--bounds", nargs=4, type=int, metavar=("X1", "Y1", "X2", "Y2"))
    p.add_argument("--view", choices=["none", "text"], default="none")
    p = commands.add_parser("call", help="Issue an explicit MCP action and retain its actual result")
    p.add_argument("tool")
    p.add_argument("arguments", help="JSON object; normal OniMcp permissions and confirmations still apply")
    p = commands.add_parser("step", help="Bounded real-time smoke step; always attempts pause in finally")
    p.add_argument("--seconds", type=float, default=5)
    p.add_argument("--speed", type=int, choices=[1, 2, 3], default=1)
    p.add_argument("--release-native-pause", action="store_true", help="Explicitly resume an inspected test save's native pause through launch/start; never reload")
    p = commands.add_parser("serve", help="Optional read-only text spectator; never contacts MCP")
    p.add_argument("--port", type=int, default=8790)
    args = parser.parse_args()
    if args.command == "launch":
        return launch(args)
    if args.command == "render":
        atomic_json(args.output / "render.json", {"mode": args.mode, "interval": args.interval, "updateRate": args.update_rate})
        print("Requested", args.mode, "rendering. Confirm mode, renderInterval and fresh timestamp with status.")
        return
    if args.command == "status":
        data = json.loads((args.output / "status.json").read_text(encoding="utf-8"))
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(data["capturedAt"].replace("Z", "+00:00"))).total_seconds()
        if age > 5 or data["pid"] not in game_pids():
            raise RuntimeError("Companion status is stale or the game exited")
        print(json.dumps(data, indent=2))
        return
    if args.command == "serve":
        args.output.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "scripts/text-view.html", args.output / "index.html")
        # Serve only spectator artifacts; no MCP proxy or action endpoint.
        class SpectatorHandler(http.server.SimpleHTTPRequestHandler):
            def send_head(self):
                if self.path.split("?", 1)[0] not in ("/", "/index.html", "/latest.json"):
                    self.send_error(404)
                    return
                return super().send_head()
        handler = functools.partial(SpectatorHandler, directory=str(args.output.resolve()))
        print(f"Text spectator: http://127.0.0.1:{args.port}/ (Ctrl+C to stop)", flush=True)
        with http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
            server.serve_forever()
        return
    client = Client(args.url, os.environ.get("ONIHARNESS_MCP_TOKEN"))
    if args.command == "observe":
        data = observe(client, args.output, args.intent, args.bounds)
        print(json.dumps({"capturedAt": data["capturedAt"], "paused": data["paused"],
                          "observation": str((args.output / "latest.json").resolve()),
                          "mapRequest": data["mapRequest"]}, ensure_ascii=False, indent=2))
        if args.view == "text":
            print(data["map"]["text"])
        return
    if args.command == "call":
        arguments = json.loads(args.arguments)
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be a JSON object")
        if not (args.tool == "game_control" and arguments.get("domain") == "launch"):
            client.call("game_control", {"domain": "speed", "action": "pause"})
        result = client.call(args.tool, arguments)
        atomic_json(args.output / "action.json", {"capturedAt": datetime.now(timezone.utc).isoformat(),
                                                 "tool": args.tool, "arguments": arguments, "result": result})
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "step":
        if not 0 < args.seconds <= 10:
            raise ValueError("Smoke step must be between 0 and 10 wall seconds")
        client.call("game_control", {"domain": "speed", "action": "pause"})
        before = client.call("game_control", {"domain": "speed", "action": "time"})
        if before.get("isPaused") is not True:
            raise RuntimeError("Initial pause was not verified")
        status_path = args.output / "status.json"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        if status.get("pid") not in game_pids() or not status.get("gameLoaded"):
            raise RuntimeError("A live loaded companion is required for bounded stepping")
        lease_id = uuid.uuid4().hex
        # Leave time to acknowledge the lease before starting, but bound a lost client.
        deadline = datetime.now(timezone.utc) + timedelta(seconds=args.seconds + 4)
        atomic_json(args.output / "run-lease.json", {"Id": lease_id, "DeadlineUtc": deadline.isoformat()})
        acknowledgment_deadline = time.monotonic() + 2.5
        while time.monotonic() < acknowledgment_deadline:
            status = json.loads(status_path.read_text(encoding="utf-8"))
            if status.get("leaseId") == lease_id:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("Companion did not arm the stop deadline; game remains paused")
        try:
            client.call("game_control", {"domain": "speed", "action": "set_speed", "speed": args.speed})
            resumed = client.call("game_control", {"domain": "speed", "action": "resume"})
            if resumed.get("isPaused") and args.release_native_pause:
                active = client.call("game_control", {"domain": "launch", "action": "status", "limit": 1})
                if not active.get("loaded") or not active.get("activeSaveUsable"):
                    raise RuntimeError("Cannot release native pause without an already loaded exact save")
                client.call("game_control", {"domain": "launch", "action": "start", "path": active["activeSaveFile"],
                                             "resume": True, "speed": args.speed, "forceLoad": False, "confirm": True})
            running = client.call("game_control", {"domain": "speed", "action": "time"})
            if running.get("isPaused") is not False:
                raise RuntimeError("Native game pause remains. Inspect its cause; only for a known test pause use --release-native-pause.")
            time.sleep(args.seconds)
        finally:
            client.call("game_control", {"domain": "speed", "action": "pause"})
        after = client.call("game_control", {"domain": "speed", "action": "time"})
        if after.get("isPaused") is not True:
            raise RuntimeError("Pause after smoke step was not verified")
        refresh_deadline = time.monotonic() + 2.5
        while time.monotonic() < refresh_deadline:
            final_status = json.loads(status_path.read_text(encoding="utf-8"))
            if final_status.get("paused") and final_status["capturedAt"] != status["capturedAt"]:
                break
            time.sleep(.1)
        else:
            raise RuntimeError("No fresh paused telemetry after step")
        advanced = final_status["simulationSeconds"] - status["simulationSeconds"]
        if advanced <= 0:
            raise RuntimeError("Game clock did not advance; smoke step failed")
        record = {"capturedAt": datetime.now(timezone.utc).isoformat(), "tool": "bounded_smoke_step",
                  "wallSeconds": args.seconds, "speed": args.speed, "before": before, "after": after,
                  "simulationSecondsAdvanced": advanced, "releasedNativePause": args.release_native_pause}
        atomic_json(args.output / "action.json", record)
        print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
