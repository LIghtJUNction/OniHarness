#!/usr/bin/env python3
"""Measure guarded step latency and game CPU cost; never treat zero work as a speedup."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

from runtime import read_status
from runtime_mcp import atomic_json

ROOT = Path(__file__).resolve().parents[1]


def require_log_markers(output, markers):
    if not markers:
        return
    log = (output / "player.log").read_text(encoding="utf-8", errors="replace")
    for marker in markers:
        if not marker or marker not in log:
            raise RuntimeError("Required mod/probe log marker is absent: " + repr(marker))


def process_sample(pid):
    directory = Path("/proc") / str(pid)
    fields = directory.joinpath("stat").read_text().rsplit(")", 1)[1].split()
    memory = dict(line.split(":", 1) for line in directory.joinpath("status").read_text().splitlines() if ":" in line)
    return {"cpuSeconds": (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK"),
            "majorFaults": int(fields[9]), "rssKiB": int(memory["VmRSS"].split()[0]),
            "swapKiB": int(memory.get("VmSwap", "0").split()[0])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/runtime")
    parser.add_argument("--url", default="http://127.0.0.1:8788/mcp/")
    parser.add_argument("--seconds", type=float, default=1)
    parser.add_argument("--speed", type=int, choices=[1, 2, 3], default=1)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--label", required=True)
    parser.add_argument("--require-log-marker", action="append", default=[],
                        help="Require an active session's mod/probe startup marker before measuring; repeatable")
    args = parser.parse_args()
    if not 0 < args.seconds <= 10 or not 1 <= args.samples <= 20 or not 0 <= args.warmup <= 3:
        parser.error("Use 0..10 step seconds, 1..20 samples and 0..3 warmups")
    if not args.label or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in args.label):
        parser.error("Label must contain only letters, digits, hyphens and underscores")
    original = read_status(args.output)
    require_log_markers(args.output, args.require_log_marker)
    if not original.get("gameLoaded") or original.get("paused") is not True:
        raise RuntimeError("A fresh paused loaded companion is required")
    report = {"schema": 1, "label": args.label, "capturedAt": datetime.now(timezone.utc).isoformat(),
              "workload": "end-to-end guarded wall-time steps, including client startup, lease acknowledgment and final pause verification",
              "headless": original.get("headless"), "unity": original["unity"], "warmups": args.warmup,
              "controlNotifications": original.get("controlNotifications", False),
              "requiredLogMarkers": args.require_log_marker,
              "requestedStepSeconds": args.seconds, "speed": args.speed, "samples": [],
              "hostLoadAverage": list(os.getloadavg()), "hostCpuCount": os.cpu_count(),
              "limits": "Not maximum simulation throughput or FPS. Compare matched save/mod/cap/work configurations. RSS is resident process memory, not total memory saved. Paging and concurrent workloads add noise."}
    destination = args.output / ("step-benchmark-" + args.label + ".json")
    try:
        for index in range(args.warmup + args.samples):
            initial = read_status(args.output)
            if initial["pid"] != original["pid"] or initial.get("paused") is not True:
                raise RuntimeError("Game process changed or was not paused before a sample")
            before = process_sample(original["pid"])
            started = time.monotonic()
            result = subprocess.run([sys.executable, str(ROOT / "scripts/runtime.py"), "--output", str(args.output.resolve()),
                                     "--url", args.url, "step", "--seconds", str(args.seconds), "--speed", str(args.speed)],
                                    capture_output=True, text=True, timeout=args.seconds + 20, check=False)
            elapsed = time.monotonic() - started
            after = process_sample(original["pid"])
            if result.returncode:
                raise RuntimeError("Step failed: " + result.stderr[-4000:])
            step = json.loads(result.stdout)
            final = read_status(args.output)
            advanced = step["simulationSecondsAdvanced"]
            if not advanced > 0 or final.get("paused") is not True or final["pid"] != original["pid"]:
                raise RuntimeError("Step did not complete actual native work and pause")
            row = {"wallSeconds": elapsed, "controlOverheadSeconds": elapsed - args.seconds,
                   "nativeSeconds": advanced, "gameCpuSeconds": after["cpuSeconds"] - before["cpuSeconds"],
                   "majorFaults": after["majorFaults"] - before["majorFaults"], "rssKiB": after["rssKiB"],
                   "swapKiB": after["swapKiB"], "pausedAfter": True, "failed": False}
            row["stepTimings"] = step.get("timings")
            row["updateFrames"] = final["frameCount"] - initial["frameCount"]
            row["gameCpuSecondsPerNativeSecond"] = row["gameCpuSeconds"] / advanced
            if index >= args.warmup:
                report["samples"].append(row)
                atomic_json(destination, report)
                print(json.dumps(row), flush=True)
        report["summary"] = {key: {"median": statistics.median(row[key] for row in report["samples"]),
                                    "min": min(row[key] for row in report["samples"]),
                                    "max": max(row[key] for row in report["samples"])}
                             for key in ("wallSeconds", "controlOverheadSeconds", "gameCpuSecondsPerNativeSecond")}
        report["complete"] = True
        print(json.dumps(report["summary"]), flush=True)
    except BaseException as error:
        report["complete"], report["error"] = False, str(error)
        raise
    finally:
        atomic_json(destination, report)


if __name__ == "__main__":
    main()
