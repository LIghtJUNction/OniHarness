#!/usr/bin/env python3
"""Write/check a sanitized local receipt tied to this source tree, excluding receipts themselves."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "docs/verification/0.1.0.json"


def source_digest():
    paths = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT).decode().split("\0")
    manifest = "".join(f"{hashlib.sha256((ROOT / p).read_bytes()).hexdigest()}  {p}\n"
                       for p in sorted(set(paths)) if p and not p.startswith("docs/verification/"))
    return hashlib.sha256(manifest.encode()).hexdigest()


def sanitized_report(name, expected):
    report = json.loads((ROOT / "artifacts" / name).read_text())
    assert report["tests"] and all(t["status"] == expected for t in report["tests"])
    allowed = {"name", "evidence", "status", "fingerprint", "stepsCompleted", "initialMass", "finalMass", "adapter", "nativeSha256", "managedSha256", "exitCode"}
    return [{k: v for k, v in test.items() if k in allowed and v is not None} for test in report["tests"]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    digest = source_digest()
    if not args.write:
        assert json.loads(DESTINATION.read_text())["sourceTreeSha256"] == digest, "Receipt describes a different source tree"
        print("PASS: receipt source digest matches")
        return
    programs = sorted(set(Path(p).name for p in re.findall(r'execve\("([^\"]+)"', (ROOT / ".local/execve.log").read_text())))
    assert programs and not any("oxygennotincluded" in p.lower() or "unityplayer" in p.lower() for p in programs)
    receipt = {
        "schemaVersion": 1,
        "harnessVersion": "0.1.0",
        "recordedUtc": datetime.now(timezone.utc).isoformat(),
        "sourceTreeSha256": digest,
        "sourceDigestFormat": "SHA256 of sorted '<file SHA256>  <relative path>\\n' for Git-visible source files, excluding docs/verification/",
        "host": "Linux x64, .NET SDK 10.0.112; local installed ONI build 744825",
        "frameworkBehavioralChecks": 10,
        "installedCliNativeRuns": sanitized_report("installed-native.json", "passed"),
        "installedCliManagedRuns": sanitized_report("installed-managed.json", "passed"),
        "intentionalFailingNativeAssertion": sanitized_report("negative-native.json", "failed"),
        "nativeRunExecveProgramBasenames": programs,
        "gameOrUnityPlayerLaunchedInNativeTrace": False,
        "limitations": "Small explicit fixtures and selected .NET-host methods; no ONI gameplay, full lifecycle or Harmony runtime evidence. Raw logs and game files remain local."
    }
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    DESTINATION.write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"Wrote {DESTINATION.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
