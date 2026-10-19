#!/usr/bin/env python3
"""Alternate CycleTrim layouts in one paused, loaded headless game; record main-thread PMU work."""
import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
import uuid

from measure_steps import process_sample, require_log_markers
from pmu import CounterGroup, metrics
from runtime import read_status
from runtime_mcp import atomic_json, Client

ROOT = Path(__file__).resolve().parents[1]


def request(control, layout, pid):
    nonce = uuid.uuid4().hex
    temp = control.with_name(control.name + ".tmp")
    temp.write_text(layout + " " + nonce, encoding="utf-8")
    os.replace(temp, control)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            fields = Path(str(control) + ".ack").read_text().split()
            if len(fields) == 5 and fields[:3] == [str(pid), layout, nonce]:
                return dict(zip(("baseline", "compact"), map(int, fields[3:])))
        except FileNotFoundError:
            pass
        time.sleep(.05)
    raise RuntimeError("No current-process layout acknowledgment: " + layout)


def step(output, seconds, url):
    result = subprocess.run([sys.executable, str(ROOT / "scripts/runtime.py"), "--output", str(output),
                             "--url", url, "step", "--seconds", str(seconds)],
                            capture_output=True, text=True, timeout=seconds + 20)
    if result.returncode:
        raise RuntimeError(result.stderr[-3000:])
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--control", required=True, type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:8788/mcp/")
    parser.add_argument("--seconds", type=float, default=3)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--cpu", type=int, default=min(os.sched_getaffinity(0)))
    args = parser.parse_args()
    if not 0 < args.seconds <= 10 or not 5 <= args.samples <= 20:
        parser.error("Use 0..10 seconds and 5..20 samples per layout")
    output, control = args.output.resolve(), args.control.resolve()
    initial = read_status(output)
    if initial.get("paused") is not True or not initial.get("gameLoaded"):
        raise RuntimeError("A paused loaded game is required")
    require_log_markers(output, ["[CycleTrim][LayoutLab] baseline; explicit control active"])
    pid = initial["pid"]
    affinity = os.sched_getaffinity(pid)
    if args.cpu not in affinity: parser.error("Requested CPU unavailable for the game")
    report = {"schema": 1, "samples": [], "complete": False, "mainThreadCpu": args.cpu,
              "hostLoadAverage": list(os.getloadavg()), "seconds": args.seconds,
              "limits": "One native process, alternating continuation of one development save at 1x; state evolves. PMU covers only the user-space main thread; process CPU covers all game threads. Controlled affinity, paging and concurrent host load limit attribution. No maximum-throughput or full-game cache-hit claim."}
    destination = output / "fetch-layout-benchmark.json"
    group = None
    try:
        os.sched_setaffinity(pid, {args.cpu})
        group = CounterGroup(pid)
        for pair in range(args.samples):
            for layout in (("baseline", "compact") if pair % 2 == 0 else ("compact", "baseline")):
                current = read_status(output)
                if current["pid"] != pid or current.get("paused") is not True:
                    raise RuntimeError("Game changed or was not paused")
                request(control, layout, pid)
                step(output, args.seconds, args.url)  # identical per-layout warmup
                calls_before = request(control, layout, pid)
                before = process_sample(pid)
                group.start()
                started = time.monotonic()
                try:
                    result = step(output, args.seconds, args.url)
                finally:
                    counters = group.stop()
                wall = time.monotonic() - started
                after = process_sample(pid)
                calls_after = request(control, layout, pid)
                final = read_status(output)
                advanced = result["simulationSecondsAdvanced"]
                actual_calls = calls_after[layout] - calls_before[layout]
                other = "compact" if layout == "baseline" else "baseline"
                if final["pid"] != pid or final.get("paused") is not True or not advanced > 0 or actual_calls <= 0:
                    raise RuntimeError("Sample did not execute the selected fetch layout and pause")
                if calls_after[other] != calls_before[other]:
                    raise RuntimeError("Another layout ran within this sample")
                if counters["runningFraction"] is None or counters["runningFraction"] < .98:
                    raise RuntimeError("Insufficient PMU scheduling coverage")
                row = {"layout": layout, "pair": pair, "wallSeconds": wall, "nativeSeconds": advanced,
                       "gameCpuSecondsPerNativeSecond": (after["cpuSeconds"] - before["cpuSeconds"]) / advanced,
                       "mainThreadCacheMissesPerNativeSecond": counters["cacheMisses"] / advanced,
                       "majorFaults": after["majorFaults"] - before["majorFaults"],
                       "rssKiB": after["rssKiB"], "swapKiB": after["swapKiB"], "fetchCalls": actual_calls,
                       "pmu": counters, "cacheMetrics": metrics(counters), "pausedAfter": True}
                report["samples"].append(row)
                atomic_json(destination, report)
                print(json.dumps(row), flush=True)
        report["summary"] = {}
        for layout in ("baseline", "compact"):
            rows = [r for r in report["samples"] if r["layout"] == layout]
            report["summary"][layout] = {key:{"median":statistics.median(values), "min":min(values), "max":max(values)}
                for key,values in {k:[r[k] for r in rows] for k in
                    ("gameCpuSecondsPerNativeSecond", "mainThreadCacheMissesPerNativeSecond", "wallSeconds")}.items()}
        report["complete"] = True
    except BaseException as error:
        report["error"] = str(error)
        raise
    finally:
        if group: group.close()
        try:
            Client(args.url, os.environ.get("ONIHARNESS_MCP_TOKEN")).call("game_control", {"domain":"speed", "action":"pause"})
            request(control, "baseline", pid)
        finally:
            if Path("/proc", str(pid)).exists(): os.sched_setaffinity(pid, affinity)
            atomic_json(destination, report)


if __name__ == "__main__": main()
