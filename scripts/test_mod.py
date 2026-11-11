#!/usr/bin/env python3
"""Unified Mod Testing Tool for OniHarness.

Supports:
1. Building and verifying a Mod project.
2. Executing managed unit/integration tests with the Mod loaded via HarnessContext.
3. Selected native Cargo tests with incremental compilation and timing reports.
4. Collecting physical and simulation telemetry data.
"""
import argparse
import hashlib
import json
import os
import math
import time
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

from native_tests import run_native

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "src/OniHarness.Cli/bin/Release/net10.0/OniHarness.Cli.dll"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def find_mod_dll(mod_path: Path) -> Path:
    if mod_path.is_file() and mod_path.suffix.lower() == ".dll":
        return mod_path.resolve()
    if mod_path.is_dir():
        projects = list(mod_path.glob("*.csproj"))
        if len(projects) == 1:
            assembly = ET.parse(projects[0]).findtext(".//AssemblyName") or projects[0].stem
            if "$" in assembly:
                raise ValueError("Pass the explicit output DLL for a computed AssemblyName")
            candidates = list(mod_path.glob(f"bin/Release/**/{assembly}.dll"))
            candidates = [path for path in candidates if "ref" not in path.parts and "refint" not in path.parts]
        else:
            candidates = list(mod_path.glob("*.dll"))
        if len(candidates) == 1:
            return candidates[0].resolve()
        if candidates:
            raise ValueError("Multiple mod outputs found; pass the explicit mod DLL")
    raise FileNotFoundError(f"Could not locate mod DLL in: {mod_path}")


def build_mod_if_needed(mod_path: Path) -> Path:
    csproj_files = list(mod_path.glob("*.csproj")) if mod_path.is_dir() else []
    if len(csproj_files) > 1:
        raise ValueError("Multiple projects found; select one mod project directory")
    if csproj_files:
        print(f"[*] Building mod project: {csproj_files[0]}...")
        subprocess.run(["dotnet", "build", str(csproj_files[0]), "-c", "Release", "--nologo"], check=True)
    return find_mod_dll(mod_path)


def run_managed_mod_test(mod_dll: Path, test_assembly: Path | None, game_path: Path | None, test_filter: str | None = None) -> dict:
    print(f"[*] Running managed mod test for {mod_dll.name}...")
    report_file = Path(tempfile.mktemp(suffix=".json", prefix="mod-report-"))
    cmd = ["dotnet", str(CLI), "test"]
    if test_assembly:
        cmd.extend(["--assembly", str(test_assembly.resolve())])
    else:
        raise ValueError("Provide --assembly with the mod's relevant tests.")
    if test_filter:
        cmd.extend(["--filter", test_filter])
    if game_path:
        cmd.extend(["--game-path", str(game_path.resolve())])
    cmd.extend(["--report", str(report_file)])

    env = dict(os.environ, ONIHARNESS_TEST_MOD_DLL=str(mod_dll.resolve()))
    proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True)
    if proc.stdout:
        print(proc.stdout)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)

    data = {}
    if report_file.exists():
        data = json.loads(report_file.read_text(encoding="utf-8"))
        report_file.unlink(missing_ok=True)

    tests = data.get("tests", [])
    passed = proc.returncode == 0 and bool(tests) and all(t.get("status") == "passed" for t in tests)
    return {
        "status": "passed" if passed else "failed",
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "report": data
    }


def deploy_and_test_headless(mod_path: Path, game_path: Path, dlc: str = "EXPANSION1_ID") -> dict:
    """Runtime execution is handled by the explicit runtime controller."""
    return {"status": "unsupported", "runtimeVerified": False,
            "error": "This entry point does not execute a game runtime test"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--mod", type=Path, help="Path to managed mod directory or DLL")
    source.add_argument("--native-project", type=Path, help="Cargo library directory or Cargo.toml")
    parser.add_argument("--filter", action="append", default=[], help="Relevant test name or prefix")
    parser.add_argument("--suite", action="append", default=[], help="Named suite from oniharness.json")
    parser.add_argument("--changed-file", action="append", default=[], help="Changed path relative to Cargo.toml")
    parser.add_argument("--all", action="store_true", help="Explicitly run all native library tests")
    parser.add_argument("--profile", choices=["dev", "release"], default="dev")
    parser.add_argument("--timeout", type=float, default=60, help="Native discovery/execution timeout in seconds")
    parser.add_argument("--skip-build", action="store_true", help="Reuse an explicitly selected managed DLL")
    parser.add_argument("--assembly", type=Path, help="Path to test assembly containing [HarnessTest]")
    parser.add_argument("--game-path", type=Path, default=os.environ.get("ONIHARNESS_GAME_PATH"),
                        help="Oxygen Not Included game installation path")
    parser.add_argument("--headless", action="store_true", help="Run full headless game runtime test")
    parser.add_argument("--report", type=Path, help="Save JSON test report to path")
    args = parser.parse_args()

    if args.headless:
        parser.error("This entry point has no implemented runtime verification. Use an explicit runtime workflow.")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be positive and finite")
    if args.native_project:
        result = run_native(args.native_project, suites=args.suite, filters=args.filter,
                            changed_files=args.changed_file, all_tests=args.all,
                            profile=args.profile, timeout=args.timeout)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({key: value for key, value in result.items()
                          if key not in ("stdout", "stderr", "selectedTests")}, indent=2))
        if result.get("stdout"):
            print(result["stdout"])
        if result.get("stderr"):
            print(result["stderr"], file=sys.stderr)
        return 0 if result["status"] == "passed" else 1
    if not args.assembly:
        parser.error("--assembly is required for managed mod tests")
    if len(args.filter) > 1:
        parser.error("Managed test execution accepts one --filter")
    if args.suite or args.changed_file or args.all:
        parser.error("--suite, --changed-file and --all select native tests")
    mod_path = args.mod.resolve()
    if not mod_path.exists():
        parser.error(f"Mod path does not exist: {mod_path}")

    started = time.perf_counter()
    mod_dll = find_mod_dll(mod_path) if args.skip_build else build_mod_if_needed(mod_path)
    build_seconds = time.perf_counter() - started
    print(f"[+] Mod DLL ready: {mod_dll}")

    results = {"mod": str(mod_path), "modDll": str(mod_dll), "sha256": digest(mod_dll),
               "evidence": "managed-host-tests", "runtimeVerified": False,
               "gameLaunched": False, "buildSeconds": build_seconds}

    # Run managed mod tests
    execution_started = time.perf_counter()
    managed_res = run_managed_mod_test(mod_dll, args.assembly, args.game_path, args.filter[0] if args.filter else None)
    results["executionSeconds"] = time.perf_counter() - execution_started
    results["totalSeconds"] = time.perf_counter() - started
    results["managed"] = managed_res

    passed = results["managed"]["status"] == "passed"

    results["passed"] = passed

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        print(f"[+] Report written to {args.report}")

    print(json.dumps({
        "passed": passed,
        "mod": mod_dll.name,
        "sha256": results["sha256"][:16] + "..."
    }, indent=2))

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
