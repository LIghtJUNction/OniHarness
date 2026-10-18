#!/usr/bin/env python3
"""Compare rendering policies in the same paused Linux game; this is not a gameplay FPS benchmark."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

from install_runtime import game_pids
from runtime_mcp import atomic_json

ROOT = Path(__file__).resolve().parents[1]


def status(root):
    value = json.loads((root / "status.json").read_text())
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(value["capturedAt"].replace("Z", "+00:00"))).total_seconds()
    if age > 5 or value["pid"] not in game_pids() or not value["gameLoaded"] or value["paused"] is not True or value.get("error"):
        raise RuntimeError("Measurement requires fresh, paused, loaded, error-free companion status")
    return value


def sample(root, pid):
    state = status(root)
    if state["pid"] != pid:
        raise RuntimeError("Game process changed during measurement")
    process = Path("/proc") / str(pid)
    fields = process.joinpath("stat").read_text().split()
    gpu = {}
    for path in process.joinpath("fdinfo").glob("*"):
        try:
            lines = path.read_text().splitlines()
        except OSError:
            continue
        values = {line.partition(":")[0]: line.partition(":")[2].strip() for line in lines if line.startswith("drm-")}
        if "drm-client-id" in values and "drm-engine-render" in values:
            # Multiple FDs can reference one DRM client; do not sum duplicates.
            gpu[values["drm-client-id"]] = int(values["drm-engine-render"].split()[0])
    return {"monotonic": time.monotonic(), "cpuSeconds": (int(fields[13]) + int(fields[14])) / os.sysconf("SC_CLK_TCK"),
            "gpuNs": sum(gpu.values()) if gpu else None, "state": state}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/runtime")
    parser.add_argument("--seconds", type=float, default=4)
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--update-rate", type=int, default=30)
    args = parser.parse_args()
    if not 2 <= args.seconds <= 15 or not 2 <= args.interval <= 120 or not 15 <= args.update_rate <= 240:
        raise ValueError("Use 2..15 sample seconds, 2..120 rendering interval and 15..240 update rate")
    original = status(args.output)
    report = {"schema": 1, "workload": "same paused loaded save; ABBA normal/reduced/reduced/normal",
              "settlingSeconds": 1.5, "readinessBudgetSeconds": 10, "unity": original["unity"], "rows": [],
              "limitations": "Paused workload only; short samples. Camera counters have 1s telemetry granularity. No active-colony FPS, simulation speedup, memory saving or universal compatibility claim. GPU counter is null when unavailable."}
    try:
        for mode in ["normal", "reduced", "reduced", "normal"]:
            atomic_json(args.output / "render.json", {"mode": mode, "interval": args.interval, "updateRate": args.update_rate})
            deadline = time.monotonic() + 10
            while status(args.output)["mode"] != mode:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Rendering policy was not acknowledged")
                time.sleep(.1)
            time.sleep(1.5)
            before = sample(args.output, original["pid"])
            time.sleep(args.seconds)
            after = sample(args.output, original["pid"])
            a, b = before["state"], after["state"]
            if a["simulationSeconds"] != b["simulationSeconds"] or a["mode"] != mode or b["mode"] != mode:
                raise RuntimeError("Workload/policy changed during a sample")
            gpu = None if before["gpuNs"] is None or after["gpuNs"] is None else (after["gpuNs"] - before["gpuNs"]) / 1e9
            row = {"mode": mode, "seconds": after["monotonic"] - before["monotonic"],
                   "cpuSeconds": after["cpuSeconds"] - before["cpuSeconds"], "gpuRenderSeconds": gpu,
                   "cameraFrames": b["cameraRenderFrames"] - a["cameraRenderFrames"],
                   "updateFrames": b["frameCount"] - a["frameCount"], "renderInterval": b["renderInterval"]}
            report["rows"].append(row)
            atomic_json(args.output / "render-comparison.json", report)
            print(json.dumps(row), flush=True)
        report["complete"] = True
    except BaseException as error:
        report["complete"] = False
        report["error"] = str(error)
        raise
    finally:
        atomic_json(args.output / "render.json", {"mode": original["mode"], "interval": original["renderInterval"], "updateRate": original.get("updateRate", 30)})
        atomic_json(args.output / "render-comparison.json", report)


if __name__ == "__main__":
    main()
