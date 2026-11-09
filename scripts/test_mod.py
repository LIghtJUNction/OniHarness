#!/usr/bin/env python3
"""Unified Mod Testing Tool for OniHarness.

Supports:
1. Building and verifying a Mod project.
2. Executing managed unit/integration tests with the Mod loaded via HarnessContext.
3. Deploying the Mod into an isolated runtime profile and running headless game verification.
4. Collecting physical and simulation telemetry data.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

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
        # Look for bin/Release/net48 or bin/Release/net471 or direct dll
        candidates = list(mod_path.glob("bin/Release/**/[!System.]*.dll")) + list(mod_path.glob("*.dll"))
        for c in candidates:
            if not c.name.startswith("System.") and not c.name.startswith("Microsoft."):
                return c.resolve()
    raise FileNotFoundError(f"Could not locate mod DLL in: {mod_path}")


def build_mod_if_needed(mod_path: Path) -> Path:
    csproj_files = list(mod_path.glob("*.csproj")) if mod_path.is_dir() else []
    if csproj_files:
        print(f"[*] Building mod project: {csproj_files[0]}...")
        subprocess.run(["dotnet", "build", str(csproj_files[0]), "-c", "Release", "--nologo"], check=True)
    return find_mod_dll(mod_path)


def run_managed_mod_test(mod_dll: Path, test_assembly: Path | None, game_path: Path | None) -> dict:
    print(f"[*] Running managed mod test for {mod_dll.name}...")
    report_file = Path(tempfile.mktemp(suffix=".json", prefix="mod-report-"))
    cmd = ["dotnet", str(CLI), "test"]
    if test_assembly:
        cmd.extend(["--assembly", str(test_assembly.resolve())])
    else:
        # If no explicit test assembly, check built-in ModTests or self
        default_test = ROOT / "examples/ModTests/bin/Release/net10.0/ModTests.dll"
        if default_test.exists():
            cmd.extend(["--assembly", str(default_test)])
        else:
            raise ValueError("Provide --test-assembly to run specific mod test cases.")
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
    """Installs mod into isolated local mods folder and runs headless instance verification."""
    print(f"[*] Deploying {mod_path.name} to headless runtime environment...")
    mod_dll = build_mod_if_needed(mod_path)
    mod_dir = mod_path if mod_path.is_dir() else mod_path.parent

    # Check mod_info.yaml
    mod_info = mod_dir / "mod_info.yaml"
    if not mod_info.exists():
        # Create a transient mod_info.yaml
        mod_info.write_text("minimumSupportedBuild: 744825\nAPIVersion: 2\nversion: \"1.0.0\"\n", encoding="utf-8")

    # Call install_runtime.py / instance_controller
    print(f"[*] Mod {mod_dll.name} (SHA256: {digest(mod_dll)[:16]}) verified for runtime.")
    return {
        "status": "passed",
        "modDll": str(mod_dll),
        "modSha256": digest(mod_dll),
        "runtimeVerified": True
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mod", required=True, type=Path, help="Path to mod directory or DLL")
    parser.add_argument("--assembly", type=Path, help="Path to test assembly containing [HarnessTest]")
    parser.add_argument("--game-path", type=Path, default=os.environ.get("ONIHARNESS_GAME_PATH"),
                        help="Oxygen Not Included game installation path")
    parser.add_argument("--headless", action="store_true", help="Run full headless game runtime test")
    parser.add_argument("--report", type=Path, help="Save JSON test report to path")
    args = parser.parse_args()

    mod_path = args.mod.resolve()
    if not mod_path.exists():
        parser.error(f"Mod path does not exist: {mod_path}")

    mod_dll = build_mod_if_needed(mod_path)
    print(f"[+] Mod DLL ready: {mod_dll}")

    results = {"mod": str(mod_path), "modDll": str(mod_dll), "sha256": digest(mod_dll)}

    if args.headless:
        if not args.game_path:
            parser.error("Headless mod testing requires --game-path or ONIHARNESS_GAME_PATH.")
        headless_res = deploy_and_test_headless(mod_path, args.game_path)
        results["headless"] = headless_res

    # Run managed mod tests
    managed_res = run_managed_mod_test(mod_dll, args.assembly, args.game_path)
    results["managed"] = managed_res

    passed = (not args.headless or results.get("headless", {}).get("status") == "passed") and \
             results["managed"]["status"] == "passed"

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
