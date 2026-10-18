#!/usr/bin/env python3
"""Check source/package boundaries without reading or distributing game files."""
import argparse
from pathlib import Path, PurePosixPath
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BINARY_SUFFIXES = {".dll", ".so", ".dylib", ".exe", ".sav", ".pdb", ".bin"}
OWN_BINARIES = {"OniHarness.Core.dll", "OniHarness.Cli.dll", "OniHarness.Core.pdb", "OniHarness.Cli.pdb"}


def check_name(name, package=False):
    path = PurePosixPath(name)
    lower = path.name.lower()
    if any(part == ".." for part in path.parts) or path.is_absolute():
        raise ValueError(f"Unsafe archive path: {name}")
    if lower.startswith(("assembly-csharp", "unityengine", "unityplayer", "libsimdll", "simdll")) or lower in {".env", "mscorlib.dll"}:
        raise ValueError(f"Game/private file in distribution: {name}")
    if path.suffix.lower() in BINARY_SUFFIXES and not (package and path.name in OWN_BINARIES):
        raise ValueError(f"Unexpected binary in distribution: {name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packages", type=Path)
    args = parser.parse_args()
    names = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT).decode().split("\0")
    names = sorted(set(n for n in names if n))
    if not names:
        raise ValueError("No source files found")
    for name in names:
        check_name(name)
    entries = 0
    if args.packages:
        packages = sorted(args.packages.glob("*.nupkg"))
        if len(packages) != 2:
            raise ValueError("Expected CLI and Core packages")
        for package in packages:
            with zipfile.ZipFile(package) as archive:
                if archive.testzip() is not None:
                    raise ValueError(f"Corrupt package: {package.name}")
                for name in archive.namelist():
                    check_name(name, package=True)
                    entries += 1
    print(f"PASS: distribution boundary ({len(names)} source files, {entries} package entries)")


if __name__ == "__main__":
    main()
