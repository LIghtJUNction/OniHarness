#!/usr/bin/env python3
"""Build, run behavioral tests and inspect packages; optionally test installed ONI libraries."""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    print("+ " + " ".join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game-path")
    args = parser.parse_args()
    run("dotnet", "build", "OniHarness.slnx", "-c", "Release", "--nologo")
    run("dotnet", "run", "--project", "tests/OniHarness.Tests", "-c", "Release", "--no-build")
    cli = ["dotnet", "run", "--project", "src/OniHarness.Cli", "-c", "Release", "--no-build", "--"]
    run(*cli, "test", "--assembly", "examples/ModTests/bin/Release/net10.0/ModTests.dll", "--filter", "PolicyCanRunWithoutGameFiles")
    for project in ("Core", "Cli"):
        run("dotnet", "pack", f"src/OniHarness.{project}", "-c", "Release", "--no-build", "-o", "artifacts/packages")
    run(sys.executable, "scripts/verify_distribution.py", "--packages", "artifacts/packages")
    run("git", "diff", "--check")
    if args.game_path:
        run(*cli, "doctor", "--game-path", args.game_path)
        run(*cli, "test", "examples/scenarios", "--game-path", args.game_path, "--repeat", "3", "--report", "artifacts/native-results.json")
        run(*cli, "test", "--assembly", "examples/ModTests/bin/Release/net10.0/ModTests.dll", "--game-path", args.game_path,
            "--report", "artifacts/mod-tests.json")
    else:
        print("NOT RUN: real native simulation and installed game methods (provide --game-path).")


if __name__ == "__main__":
    main()
