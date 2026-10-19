#!/usr/bin/env python3
"""Alternating production-helper layout experiments with a scoped hardware-counter window."""
import argparse
import json
import os
from pathlib import Path
import select
import statistics
import subprocess
import time

from pmu import CounterGroup, metrics
from runtime_mcp import atomic_json


def line(process, expected, timeout=60):
    ready, _, _ = select.select([process.stdout], [], [], timeout)
    if not ready:
        raise RuntimeError("Benchmark handshake timed out: " + expected)
    value = process.stdout.readline().strip()
    if value != expected:
        raise RuntimeError("Expected " + expected + "; got " + value)


def run(assembly, dotnet, side, keys, iterations, cpu):
    env = dict(os.environ, DOTNET_TieredCompilation="0", DOTNET_CLI_TELEMETRY_OPTOUT="1")
    process = subprocess.Popen([dotnet, str(assembly), "--candidate-layout-benchmark", side,
                                str(keys), str(iterations)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, env=env)
    group = None
    try:
        # Set before warmup finishes so the measured loop and warmup use the same core.
        for task in Path("/proc", str(process.pid), "task").iterdir():
            try: os.sched_setaffinity(int(task.name), {cpu})
            except ProcessLookupError: pass
        line(process, "READY")
        group = CounterGroup(process.pid)
        group.start()
        process.stdin.write("GO\n"); process.stdin.flush()
        line(process, "DONE")
        counters = group.stop()
        process.stdin.write("REPORT\n"); process.stdin.flush()
        stdout, stderr = process.communicate(timeout=15)
        if process.returncode:
            raise RuntimeError("Benchmark failed: " + stderr[-2000:])
        result = json.loads(stdout)
        result["pmu"], result["cacheMetrics"] = counters, metrics(counters)
        if counters["runningFraction"] is None or counters["runningFraction"] < .98:
            raise RuntimeError("PMU group was not scheduled for at least 98% of enabled time")
        if result["winners"] != keys or not result["wallSeconds"] > 0:
            raise RuntimeError("Benchmark did not select the required winners")
        return result
    finally:
        if group: group.close()
        if process.poll() is None:
            process.kill(); process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assembly", required=True, type=Path)
    parser.add_argument("--dotnet", default="dotnet")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--keys", nargs="+", type=int, default=[64, 4096, 16384])
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--cpu", type=int, default=min(os.sched_getaffinity(0)))
    args = parser.parse_args()
    if args.cpu not in os.sched_getaffinity(0) or not 5 <= args.samples <= 20:
        parser.error("Use an available CPU and 5..20 samples")
    if any(not 1 <= n <= 65536 for n in args.keys): parser.error("Keys must be 1..65536")
    report = {"schema": 1, "cpu": args.cpu, "loadAverage": list(os.getloadavg()),
              "claim": "Compare warmed synthetic collection time and generic cache events for the same production helper and winners; no ONI FPS claim.",
              "samples": [], "complete": False}
    try:
        for keys in args.keys:
            iterations = max(100, 8_000_000 // (keys * 4))
            for index in range(args.samples):
                for side in (("baseline", "compact") if index % 2 == 0 else ("compact", "baseline")):
                    row = run(args.assembly.resolve(), args.dotnet, side, keys, iterations, args.cpu)
                    row["pair"] = index
                    report["samples"].append(row)
                    atomic_json(args.output, report)
                    print(json.dumps({k:row[k] for k in ("layout", "keys", "wallSeconds", "allocatedBytes", "cacheMetrics")}), flush=True)
            paired = [r for r in report["samples"] if r["keys"] == keys]
            if len({r["checksum"] for r in paired}) != 1:
                raise RuntimeError("Layouts selected different winners")
        report["summary"] = {}
        for keys in args.keys:
            report["summary"][str(keys)] = {}
            for side in ("baseline", "compact"):
                rows = [r for r in report["samples"] if r["keys"] == keys and r["layout"] == side]
                report["summary"][str(keys)][side] = {k:{"median":statistics.median(values),"min":min(values),"max":max(values)}
                    for k,values in {"wallSeconds":[r["wallSeconds"] for r in rows],
                                     "cacheMisses":[r["pmu"]["cacheMisses"] for r in rows],
                                     "cacheReferences":[r["pmu"]["cacheReferences"] for r in rows]}.items()}
        report["complete"] = True
    except BaseException as error:
        report["error"] = str(error)
        raise
    finally:
        atomic_json(args.output, report)


if __name__ == "__main__": main()
