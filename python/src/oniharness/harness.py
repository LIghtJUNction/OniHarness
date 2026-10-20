"""Drive the official CLI; preserve worker isolation, fingerprints and failures."""

from __future__ import annotations

import json
import math
import os
import shutil
import signal
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class HarnessError(RuntimeError):
    """Missing backend, invalid report or process timeout."""


class CommandError(HarnessError):
    """CLI/configuration failure; output and exit status remain available."""

    def __init__(self, result: subprocess.CompletedProcess[str]):
        self.returncode = result.returncode
        self.stdout = result.stdout
        self.stderr = result.stderr
        super().__init__(
            f"OniHarness exited with {result.returncode}: "
            + (result.stderr.strip() or result.stdout.strip())
        )


@dataclass(frozen=True)
class TestReport:
    """Original CLI report plus process evidence. Empty runs never pass."""

    data: dict[str, Any]
    returncode: int
    stdout: str
    stderr: str

    @property
    def tests(self) -> list[dict[str, Any]]:
        return self.data["tests"]

    @property
    def passed(self) -> bool:
        return (
            self.returncode == 0
            and bool(self.tests)
            and all(test.get("status") == "passed" for test in self.tests)
        )

    def raise_for_status(self) -> None:
        if not self.passed:
            raise TestFailure(self)


class TestFailure(HarnessError):
    """A failed, unsupported, crashed, timed-out or empty test run."""

    def __init__(self, report: TestReport):
        self.report = report
        failures = [
            f"{t.get('name')}: {t.get('status')} ({t.get('error') or ''})"
            for t in report.tests
            if t.get("status") != "passed"
        ]
        super().__init__("; ".join(failures) or "No passing test run")


def _positive_timeout(value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        raise ValueError("process_timeout must be finite and positive")
    return value


def _run(command: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
    options: dict[str, Any] = {}
    if os.name == "posix":
        options["start_new_session"] = True
    elif os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        process = subprocess.Popen(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            **options,
        )
    except FileNotFoundError as error:
        raise HarnessError(
            f"Executable not found: {command[0]}. Install the OniHarness CLI "
            "or use Harness.from_checkout(path, build=True) with .NET SDK 10."
        ) from error
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except BaseException as error:
        # The CLI owns native workers. Terminate its descendants as well as the parent.
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                check=False,
            )
        process.kill()
        process.communicate()
        if isinstance(error, subprocess.TimeoutExpired):
            raise HarnessError(
                f"OniHarness process exceeded {timeout} seconds; process tree terminated"
            ) from error
        raise
    return subprocess.CompletedProcess(
        list(command), process.returncode, stdout, stderr
    )


class Harness:
    """Call an installed CLI without ever launching ONI, Unity or Steam.

    ``command`` is an argument sequence, never a shell string. The default looks
    for ``oniharness`` on PATH. Native adapters still require exact installed
    game fingerprints; this library does not bypass the CLI's compatibility gate.
    ``process_timeout`` bounds the entire run, independently of worker timeouts.
    """

    def __init__(
        self,
        command: Sequence[str | os.PathLike[str]] | None = None,
        *,
        game_path: str | os.PathLike[str] | None = None,
        process_timeout: float = 300,
    ):
        if isinstance(command, (str, bytes)):
            raise TypeError("command must be an argument sequence, e.g. ['oniharness']")
        if command is None:
            executable = shutil.which("oniharness")
            if executable is None:
                raise HarnessError(
                    "OniHarness CLI not found on PATH. Install the CLI or use "
                    "Harness.from_checkout(path, build=True) with .NET SDK 10."
                )
            command = [executable]
        if not command:
            raise ValueError("command cannot be empty")
        self.command = tuple(os.fspath(part) for part in command)
        self.game_path = (
            Path(game_path).expanduser().resolve() if game_path is not None else None
        )
        self.process_timeout = _positive_timeout(process_timeout)

    @classmethod
    def from_checkout(
        cls,
        path: str | os.PathLike[str],
        *,
        build: bool = False,
        game_path: str | os.PathLike[str] | None = None,
        process_timeout: float = 300,
    ) -> Harness:
        """Use a trusted source checkout. Building is explicit and requires SDK 10."""
        root = Path(path).expanduser().resolve()
        project = root / "src/OniHarness.Cli/OniHarness.Cli.csproj"
        if not project.is_file():
            raise HarnessError(f"Not an OniHarness checkout: {root}")
        if build:
            result = _run(
                ["dotnet", "build", str(project), "-c", "Release", "--nologo"],
                _positive_timeout(process_timeout),
            )
            if result.returncode:
                raise CommandError(result)
        backend = project.parent / "bin/Release/net10.0/OniHarness.Cli.dll"
        if not backend.is_file():
            raise HarnessError(
                "CLI has not been built; use build=True or build the checkout first"
            )
        return cls(
            ["dotnet", backend], game_path=game_path, process_timeout=process_timeout
        )

    def _invoke(self, arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return _run([*self.command, *arguments], self.process_timeout)

    def _game_arguments(self) -> list[str]:
        return (
            ["--game-path", str(self.game_path)] if self.game_path is not None else []
        )

    def doctor(self) -> dict[str, Any]:
        """Inspect installed hashes. Unsupported installations raise CommandError."""
        result = self._invoke(["doctor", *self._game_arguments()])
        if result.returncode:
            raise CommandError(result)
        return self._json_object(result.stdout)

    def contract(
        self,
        assembly: str | os.PathLike[str],
        *,
        type_name: str,
        method: str,
        parameters: int = 0,
    ) -> dict[str, Any]:
        """Read metadata only. A missing method returns found=False, not success."""
        result = self._invoke(
            [
                "contract",
                str(Path(assembly).resolve()),
                "--type",
                type_name,
                "--method",
                method,
                "--parameters",
                str(parameters),
            ]
        )
        if result.returncode not in (0, 1):
            raise CommandError(result)
        return self._json_object(result.stdout)

    @staticmethod
    def _json_object(text: str) -> dict[str, Any]:
        try:
            value = json.loads(text)
        except json.JSONDecodeError as error:
            raise HarnessError("CLI did not produce valid JSON") from error
        if not isinstance(value, dict):
            raise HarnessError("CLI did not produce a JSON object")
        return value

    def run_scenario(self, scenario: Mapping[str, Any], **options: Any) -> TestReport:
        """Run an in-memory scenario through a temporary JSON file and fresh workers."""
        with tempfile.TemporaryDirectory(prefix="oniharness-python-") as directory:
            path = Path(directory) / "scenario.json"
            path.write_text(
                json.dumps(dict(scenario), allow_nan=False), encoding="utf-8"
            )
            return self.test_scenarios(path, **options)

    def test_scenarios(
        self,
        path: str | os.PathLike[str],
        *,
        repeat: int = 1,
        timeout: int = 20,
        check: bool = True,
        report_path: str | os.PathLike[str] | None = None,
    ) -> TestReport:
        """Test one JSON file or directory. Failures raise TestFailure by default."""
        return self._test(
            [str(Path(path).expanduser().resolve())],
            repeat,
            timeout,
            check,
            report_path,
        )

    def test_assembly(
        self,
        path: str | os.PathLike[str],
        *,
        filter: str | None = None,
        repeat: int = 1,
        timeout: int = 20,
        check: bool = True,
        report_path: str | os.PathLike[str] | None = None,
    ) -> TestReport:
        """Discover and execute trusted C# tests in the CLI's isolated workers."""
        arguments = ["--assembly", str(Path(path).expanduser().resolve())]
        if filter is not None:
            arguments += ["--filter", filter]
        return self._test(arguments, repeat, timeout, check, report_path)

    def _test(
        self,
        arguments: list[str],
        repeat: int,
        timeout: int,
        check: bool,
        report_path: str | os.PathLike[str] | None,
    ) -> TestReport:
        if type(repeat) is not int or not 1 <= repeat <= 100:
            raise ValueError("repeat must be an integer in 1..100")
        if type(timeout) is not int or not 1 <= timeout <= 600:
            raise ValueError("timeout must be an integer in 1..600 worker seconds")
        with tempfile.TemporaryDirectory(prefix="oniharness-report-") as directory:
            path = Path(directory) / "report.json"
            result = self._invoke(
                [
                    "test",
                    *arguments,
                    *self._game_arguments(),
                    "--repeat",
                    str(repeat),
                    "--timeout",
                    str(timeout),
                    "--report",
                    str(path),
                ]
            )
            if result.returncode not in (0, 1) or not path.is_file():
                raise CommandError(result)
            data = self._json_object(path.read_text(encoding="utf-8"))
        if (
            data.get("schemaVersion") != 1
            or not isinstance(data.get("tests"), list)
            or any(not isinstance(test, dict) for test in data["tests"])
        ):
            raise HarnessError("Unsupported or malformed CLI test report")
        report = TestReport(data, result.returncode, result.stdout, result.stderr)
        # Retain failed reports before raising, without overwriting an old result on setup failure.
        if report_path is not None:
            output = Path(report_path).expanduser().resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        if check:
            report.raise_for_status()
        return report
