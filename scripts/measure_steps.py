#!/usr/bin/env python3
"""Measure guarded step latency and game CPU cost; never treat zero work as a speedup."""
import argparse
from datetime import datetime, timezone
import json
import math
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


def number(value, name, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value) or (value <= 0 if positive else value < 0):
        raise RuntimeError(name + " must be a finite " + ("positive" if positive else "non-negative") + " number")
    return value


def counter_delta(before, after, name):
    return number(number(after, name) - number(before, name), name + " delta")


def process_sample(pid, proc=Path("/proc")):
    directory = proc / str(pid)
    fields = directory.joinpath("stat").read_text().rsplit(")", 1)[1].split()
    if fields[0] in ("Z", "X", "x"):
        raise RuntimeError("Game process exited before sampling")
    start_ticks = int(fields[19])
    memory = dict(line.split(":", 1) for line in directory.joinpath("status").read_text().splitlines() if ":" in line)
    result = {"startTicks": start_ticks,
              "cpuSeconds": (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK"),
              "majorFaults": int(fields[9]), "rssKiB": int(memory["VmRSS"].split()[0]),
              "swapKiB": int(memory.get("VmSwap", "0").split()[0])}
    final = directory.joinpath("stat").read_text().rsplit(")", 1)[1].split()
    if final[0] in ("Z", "X", "x") or int(final[19]) != start_ticks:
        raise RuntimeError("Game process exited or changed identity during sampling")
    for name, value in result.items():
        number(value, name)
    return result


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
    report = {"schema": 1, "label": args.label, "capturedAt": datetime.now(timezone.utc).isoformat(),
              "workload": "end-to-end guarded wall-time steps, including client startup, lease acknowledgment and final pause verification",
              "warmups": args.warmup,
              "requiredLogMarkers": args.require_log_marker,
              "requestedStepSeconds": args.seconds, "speed": args.speed, "samples": [], "attempts": [], "complete": False,
              "hostLoadAverage": list(os.getloadavg()), "hostCpuCount": os.cpu_count(),
              "limits": "Not maximum simulation throughput or FPS. Compare matched save/mod/cap/work configurations. RSS is resident process memory, not total memory saved. Paging and concurrent workloads add noise."}
    destination = args.output / ("step-benchmark-" + args.label + ".json")
    # Reserve before any game control. A repeated/concurrent label must never
    # replace prior evidence, including an interrupted run's incomplete report.
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    attempt, result = None, None
    try:
        original = read_status(args.output)
        require_log_markers(args.output, args.require_log_marker)
        if (not original.get("gameLoaded") or original.get("paused") is not True or
                type(original.get("pid")) is not int or original["pid"] <= 0):
            raise RuntimeError("A fresh paused loaded companion is required")
        report.update(headless=original.get("headless"), unity=original["unity"],
                      controlNotifications=original.get("controlNotifications", False))
        start_ticks = None
        for index in range(args.warmup + args.samples):
            attempt = {"index": index + 1, "warmup": index < args.warmup, "stage": "initial-status", "failed": True}
            report["attempts"].append(attempt)
            result = None
            initial = read_status(args.output)
            if (initial["pid"] != original["pid"] or initial.get("paused") is not True or
                    not initial.get("gameLoaded")):
                raise RuntimeError("Game process changed or was not paused before a sample")
            attempt["stage"] = "process-before"
            before = process_sample(original["pid"])
            if start_ticks is None:
                start_ticks = before["startTicks"]
                report["processIdentity"] = {"pid": original["pid"], "startTicks": start_ticks}
            if before["startTicks"] != start_ticks:
                raise RuntimeError("Game process changed identity before a sample")
            attempt["stage"] = "step"
            atomic_json(destination, report)
            started = time.monotonic()
            result = subprocess.run([sys.executable, str(ROOT / "scripts/runtime.py"), "--output", str(args.output.resolve()),
                                     "--url", args.url, "step", "--seconds", str(args.seconds), "--speed", str(args.speed)],
                                    capture_output=True, text=True, timeout=args.seconds + 20, check=False)
            elapsed = number(time.monotonic() - started, "wallSeconds", positive=True)
            attempt.update(wallSeconds=elapsed, stepExitCode=result.returncode)
            if result.returncode:
                raise RuntimeError("Step failed: " + result.stderr[-4000:])
            attempt["stage"] = "step-result"
            step = json.loads(result.stdout)
            advanced = number(step["simulationSecondsAdvanced"], "simulationSecondsAdvanced", positive=True)
            timings = step.get("timings")
            if timings is not None:
                if not isinstance(timings, dict):
                    raise RuntimeError("Step timings must be an object")
                for name, value in timings.items():
                    number(value, "stepTimings." + name)
            attempt["stage"] = "final-status"
            final = read_status(args.output)
            if final.get("paused") is not True or final["pid"] != original["pid"] or not final.get("gameLoaded"):
                raise RuntimeError("Step did not complete actual native work and pause")
            attempt["stage"] = "process-after"
            after = process_sample(original["pid"])
            if after["startTicks"] != start_ticks:
                raise RuntimeError("Game process changed identity during a sample")
            attempt["stage"] = "metrics"
            row = {"wallSeconds": elapsed, "controlOverheadSeconds": number(elapsed - args.seconds, "controlOverheadSeconds"),
                   "nativeSeconds": advanced, "gameCpuSeconds": counter_delta(before["cpuSeconds"], after["cpuSeconds"], "gameCpuSeconds"),
                   "majorFaults": counter_delta(before["majorFaults"], after["majorFaults"], "majorFaults"),
                   "rssKiB": number(after["rssKiB"], "rssKiB"), "swapKiB": number(after["swapKiB"], "swapKiB"),
                   "pausedAfter": True, "failed": False, "stepTimings": timings}
            row["updateFrames"] = counter_delta(initial["frameCount"], final["frameCount"], "updateFrames")
            row["gameCpuSecondsPerNativeSecond"] = number(row["gameCpuSeconds"] / advanced, "gameCpuSecondsPerNativeSecond")
            attempt.update(stage="complete", failed=False, measurement=row)
            if index >= args.warmup:
                report["samples"].append(row)
                print(json.dumps(row), flush=True)
            atomic_json(destination, report)
        report["summary"] = {key: {"median": statistics.median(row[key] for row in report["samples"]),
                                    "min": min(row[key] for row in report["samples"]),
                                    "max": max(row[key] for row in report["samples"])}
                             for key in ("wallSeconds", "controlOverheadSeconds", "gameCpuSecondsPerNativeSecond")}
        report["complete"] = True
        print(json.dumps(report["summary"]), flush=True)
    except BaseException as error:
        report.update(complete=False, error=str(error), errorType=type(error).__name__)
        if attempt is not None and attempt["failed"]:
            attempt.update(error=str(error), errorType=type(error).__name__)
            output = error if isinstance(error, subprocess.TimeoutExpired) else result
            if output is not None:
                for name in ("stdout", "stderr"):
                    value = getattr(output, name, None)
                    if isinstance(value, bytes):
                        value = value.decode("utf-8", errors="replace")
                    if value:
                        attempt[name] = value[-4000:]
        raise
    finally:
        atomic_json(destination, report)


if __name__ == "__main__":
    main()
