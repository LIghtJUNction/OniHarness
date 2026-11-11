"""Selected Cargo library tests with source-current incremental builds."""
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time


def invoke(command, cwd, timeout):
    started = time.perf_counter()
    env = dict(os.environ)
    env.setdefault("CARGO_BUILD_JOBS", "1")
    process = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, start_new_session=os.name == "posix")
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        stdout, stderr = process.communicate()
    return {"returncode": process.returncode, "stdout": stdout, "stderr": stderr,
            "seconds": time.perf_counter() - started, "timedOut": timed_out}


def selection(manifest, suites, filters, changed_files, all_tests):
    config_path = manifest.parent / "oniharness.json"
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    if not isinstance(config, dict) or not isinstance(config.get("suites", {}), dict):
        raise ValueError("oniharness.json must contain a suites object")
    definitions = config.get("suites", {})
    for name, spec in definitions.items():
        if not isinstance(spec, dict) or any(
            not isinstance(spec.get(field, []), list)
            or any(not isinstance(value, str) or not value for value in spec.get(field, []))
            for field in ("filters", "skip", "paths")):
            raise ValueError(f"Invalid suite definition: {name}")
    selected = list(suites)
    unmatched = []
    for changed in changed_files:
        path = Path(changed)
        if path.is_absolute():
            path = path.resolve().relative_to(manifest.parent)
        name = path.as_posix()
        matches = [suite for suite, spec in definitions.items()
                   if any(fnmatch.fnmatchcase(name, pattern) for pattern in spec.get("paths", []))]
        if not matches:
            unmatched.append(name)
        selected.extend(matches)
    if unmatched:
        raise ValueError("No suite mapping for changed files: " + ", ".join(unmatched))
    selected = list(dict.fromkeys(selected))
    rules = [{"filters": list(filters), "skip": []}] if filters else []
    for suite in selected:
        if suite not in definitions:
            raise ValueError(f"Unknown suite {suite!r}; available: {', '.join(definitions)}")
        rule = {"filters": definitions[suite].get("filters", []),
                "skip": definitions[suite].get("skip", [])}
        if not rule["filters"] or any(not isinstance(value, str) or not value
                for values in rule.values() for value in values):
            raise ValueError(f"Suite {suite!r} requires nonempty string filters")
        rules.append(rule)
    if not all_tests and not rules:
        raise ValueError("Select --suite, --filter or --changed-file; --all explicitly runs the library suite")
    return selected, rules


def run_native(project, *, suites=(), filters=(), changed_files=(), all_tests=False,
               profile="dev", timeout=60):
    started = time.perf_counter()
    manifest = (project / "Cargo.toml" if project.is_dir() else project).resolve()
    result = {"status": "failed", "evidence": "native-library-tests", "manifest": str(manifest),
              "profile": profile, "runtimeVerified": False, "gameLaunched": False}
    try:
        if not manifest.is_file() or manifest.name != "Cargo.toml":
            raise ValueError(f"Cargo.toml not found: {manifest}")
        selected, rules = selection(manifest, suites, filters, changed_files, all_tests)
        result.update(suites=selected, selectors=rules)
        command = ["cargo", "test", "--manifest-path", str(manifest), "--lib", "--no-run", "--message-format=json"]
        if profile == "release":
            command.append("--release")
        build = invoke(command, manifest.parent, 300)
        result["buildSeconds"] = build["seconds"]
        artifacts = []
        diagnostics = []
        for line in build["stdout"].splitlines():
            try:
                artifact = json.loads(line)
            except json.JSONDecodeError:
                continue
            if artifact.get("reason") == "compiler-message":
                message = artifact.get("message", {})
                if message.get("level") == "error":
                    diagnostics.append(message.get("rendered") or message.get("message", ""))
            if (artifact.get("reason") == "compiler-artifact" and artifact.get("executable")
                    and artifact.get("profile", {}).get("test")
                    and Path(artifact.get("manifest_path", "")).resolve() == manifest):
                artifacts.append(artifact)
        result["buildDiagnostics"] = diagnostics
        if build["returncode"] or build["timedOut"]:
            raise ValueError("Native build failed or timed out: " + "\n".join(diagnostics + [build["stderr"]])[-8000:])
        if len(artifacts) != 1:
            raise ValueError(f"Expected one library test executable, found {len(artifacts)}")
        artifact = artifacts[0]
        binary = Path(artifact["executable"]).resolve()
        with binary.open("rb") as stream:
            binary_digest = hashlib.file_digest(stream, "sha256").hexdigest()
        result.update(binary=str(binary), buildFresh=artifact.get("fresh", False),
                      binarySha256=binary_digest)
        listing = invoke([str(binary), "--list", "--format", "terse"], manifest.parent, timeout)
        result["discoverySeconds"] = listing["seconds"]
        if listing["returncode"] or listing["timedOut"]:
            raise ValueError("Native test discovery failed or timed out")
        names = [line.removesuffix(": test") for line in listing["stdout"].splitlines() if line.endswith(": test")]
        missing = [value for rule in rules for value in rule["filters"]
                   if not any(value in name for name in names)]
        if missing:
            raise ValueError("Unmatched test filters: " + ", ".join(missing))
        chosen = [name for name in names if all_tests or any(
            any(value in name for value in rule["filters"])
            and not any(value in name for value in rule["skip"]) for rule in rules)]
        if not chosen:
            raise ValueError("No tests matched the selection")
        result["selectedTests"] = chosen
        # Exact names keep selection independent of overlapping suite prefixes.
        command = [str(binary), "--exact", "--test-threads=1", *chosen]
        execution = invoke(command, manifest.parent, timeout)
        result.update(executionSeconds=execution["seconds"], returncode=execution["returncode"],
                      timedOut=execution["timedOut"], stdout=execution["stdout"], stderr=execution["stderr"])
        summary = re.search(r"test result: (?:ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored", execution["stdout"])
        if summary:
            passed, failed, ignored = map(int, summary.groups())
            result.update(passed=passed, failed=failed, ignored=ignored)
            if execution["returncode"] == 0 and not execution["timedOut"] and passed > 0 and failed == 0 and passed + failed + ignored == len(chosen):
                result["status"] = "passed"
        if result["status"] != "passed":
            result["error"] = "Selected native tests failed, timed out, or executed no tests"
    except (OSError, ValueError) as error:
        result["error"] = str(error)
    result["totalSeconds"] = time.perf_counter() - started
    return result
