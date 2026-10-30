"""Behavioral checks for the real-game client, without launching a game."""
import http.server
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import itertools
import subprocess
from unittest.mock import patch
import contextlib
import io
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from runtime import observe, main, read_status, validate_headless, wait_ready
from runtime_mcp import Client, decode_text, payload
from install_runtime import enable_entry, require_idle_mod_loader, restore_mod_loader_state
from measure_steps import require_log_markers
import measure_steps


class RuntimeTests(unittest.TestCase):
    def test_interrupted_mod_loader_is_rejected_and_owned_baseline_restores_only_its_flag(self):
        before = {"mod_load_in_progress": False}
        current = {"mod_load_in_progress": True, "mods": [{"newMetadata": 7}], "version": 1}
        with self.assertRaisesRegex(RuntimeError, "mod loading was interrupted"):
            require_idle_mod_loader(current)
        restore_mod_loader_state(current, before)
        require_idle_mod_loader(current)
        self.assertEqual(current, {"mod_load_in_progress": False, "mods": [{"newMetadata": 7}], "version": 1})
        restore_mod_loader_state(current, {"mod_load_in_progress": True})
        self.assertTrue(current["mod_load_in_progress"])
        restore_mod_loader_state(current, {})
        self.assertTrue(current["mod_load_in_progress"])

    def test_runtime_install_rejects_interrupted_loader_before_build_or_writes(self):
        from install_runtime import main as install
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = root / "mods.json"
            original = json.dumps({"mods": [], "mod_load_in_progress": True})
            manifest.write_text(original)
            with patch("install_runtime.game_pids", return_value=[]), \
                    patch("install_runtime.subprocess.run") as build, \
                    patch.object(sys, "argv", ["install_runtime.py", "--game-path", "/fake/game", "--mods-dir", temp]):
                with self.assertRaisesRegex(RuntimeError, "mod loading was interrupted"):
                    install()
                build.assert_not_called()
            self.assertEqual(manifest.read_text(), original)
            self.assertFalse((root / "Local").exists())

    def test_structured_content_takes_precedence_and_errors_remain_errors(self):
        result = {"structuredContent": {"isPaused": True}, "content": [{"type": "text", "text": "isPaused: false"}]}
        self.assertEqual(payload(result), {"isPaused": True})
        with self.assertRaisesRegex(RuntimeError, "isPaused: false"):
            payload({**result, "isError": True})

    def test_toon_status_and_tabular_choices_decode_without_changing_json(self):
        text = "stage: mode\nisPaused: null\nchoices[2]{id,enabled}:\n  survival,true\n  sandbox,false"
        self.assertEqual(decode_text(text), {"stage": "mode", "isPaused": None,
            "choices": [{"id": "survival", "enabled": True}, {"id": "sandbox", "enabled": False}]})
        self.assertEqual(decode_text('{"isPaused":true,"simulationSeconds":2.5}'),
                         {"isPaused": True, "simulationSeconds": 2.5})

    def test_toon_batch_child_uses_the_same_decoder(self):
        client = object.__new__(Client)
        client.call = lambda *args: {"executed": 1, "failed": 0,
                                   "results": [{"text": "isPaused: true\nsimulationSeconds: 2.5"}]}
        self.assertEqual(client.read("game_control", {}), {"isPaused": True, "simulationSeconds": 2.5})

    def test_plain_markdown_is_preserved_and_malformed_toon_is_rejected(self):
        text = "# Native colony\n\n- 氧气: 1 kg\n"
        self.assertEqual(decode_text(text), {"text": text})
        with self.assertRaisesRegex(RuntimeError, "Malformed TOON"):
            decode_text("choices[2]: survival")

    def test_missing_toon_dependency_is_explicit_without_breaking_json_or_markdown(self):
        with patch.dict(sys.modules, {"toon_format": None}):
            self.assertEqual(decode_text('{"loaded":true}'), {"loaded": True})
            self.assertEqual(decode_text("# Map"), {"text": "# Map"})
            with self.assertRaisesRegex(RuntimeError, "requirements-runtime.txt"):
                decode_text("stage: main_menu")

    def test_measurement_rejects_an_unloaded_mod_before_starting_steps(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            (output / "player.log").write_text("Loaded companion only")
            with self.assertRaisesRegex(RuntimeError, "marker is absent"):
                require_log_markers(output, ["[CycleTrim][PerfProbe]"])
            (output / "player.log").write_text("[CycleTrim][PerfProbe] resolved target")
            require_log_markers(output, ["[CycleTrim][PerfProbe]"])

    def test_mod_probe_option_is_forwarded_without_overriding_headless_controls(self):
        with patch("runtime.game_pids", return_value=[]), patch("runtime.shutil.which", return_value="/fake/steam"), \
                patch.object(sys, "argv", ["runtime.py", "launch", "--dry-run", "--game-arg=--cycletrim-perf-probe"]), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            main()
        command = json.loads(out.getvalue())["command"]
        self.assertIn("--cycletrim-perf-probe", command)
        self.assertIn("-nographics", command)
        for argument in ("-applaunch", "--oniharness-control=/elsewhere"):
            with patch("runtime.game_pids", return_value=[]), patch("runtime.shutil.which", return_value="/fake/steam"), \
                    patch.object(sys, "argv", ["runtime.py", "launch", "--dry-run", "--game-arg=" + argument]):
                with self.assertRaisesRegex(ValueError, "reserved"):
                    main()

    def test_launch_defaults_to_no_window_and_no_graphics_without_fallback(self):
        with patch("runtime.game_pids", return_value=[]), patch("runtime.shutil.which", return_value="/fake/steam"), \
                patch.object(sys, "argv", ["runtime.py", "launch", "--dry-run"]), contextlib.redirect_stdout(io.StringIO()) as out, \
                patch("runtime.subprocess.Popen") as start:
            main()
        command = json.loads(out.getvalue())["command"]
        self.assertEqual(command[:3], ["/fake/steam", "-applaunch", "457140"])
        for argument in ("-batchmode", "-nographics", "--oniharness-headless"):
            self.assertIn(argument, command)
        start.assert_not_called()
        with patch("runtime.game_pids", return_value=[42]), patch.object(sys, "argv", ["runtime.py", "launch", "--dry-run"]):
            with self.assertRaisesRegex(RuntimeError, "already running"):
                main()

    def test_headless_rejects_graphical_or_incomplete_or_crashed_telemetry(self):
        status = {"batchMode": True, "graphicsDevice": "Null", "headless": True,
                  "headlessRequested": True, "cameraRenderCallbacks": 0, "gameHasCrash": False, "error": None}
        validate_headless(status)
        for key, value in [("graphicsDevice", "OpenGLCore"), ("batchMode", False),
                           ("headlessRequested", False), ("cameraRenderCallbacks", 1),
                           ("gameHasCrash", True), ("error", "native failure"), ("gameError", "native dialog")]:
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, "Headless verification failed"):
                validate_headless({**status, key: value})
        with self.assertRaisesRegex(RuntimeError, "Headless verification failed"):
            validate_headless({"graphicsDevice": "Null"})

    def test_live_status_requires_freshness_and_startup_timeout_never_relaunches(self):
        with tempfile.TemporaryDirectory() as temp, patch("runtime.game_pids", return_value=[42]):
            path = Path(temp) / "status.json"
            path.write_text(json.dumps({"pid": 42, "capturedAt": (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()}))
            with self.assertRaisesRegex(RuntimeError, "stale"):
                read_status(temp)
            with patch("runtime.time.monotonic", side_effect=[0, 0, 2]), patch("runtime.time.sleep"), patch("runtime.subprocess.Popen") as start:
                with self.assertRaisesRegex(RuntimeError, "No fallback or restart"):
                    wait_ready(temp, 1)
                start.assert_not_called()

    def test_enable_discovered_local_entry_with_null_dlc_list(self):
        entry = {"enabled": False, "enabledForDlc": None, "staticID": "existing", "label": {"version": 12}}
        enable_entry(entry, "EXPANSION1_ID")
        enable_entry(entry, "EXPANSION1_ID")
        self.assertTrue(entry["enabled"])
        self.assertEqual(entry["enabledForDlc"], ["EXPANSION1_ID"])
        self.assertEqual(entry["label"], {"version": 12})

    def test_rejects_nonlocal_and_credential_urls_before_connecting(self):
        for url in ["http://example.com/mcp/", "https://127.0.0.1/", "http://secret@localhost/"]:
            with self.assertRaises(ValueError):
                Client(url)

    def test_json_sse_session_and_tool_errors(self):
        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                message = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append((message, dict(self.headers)))
                self.send_response(200)
                self.send_header("Mcp-Session-Id", "test-session")
                self.end_headers()
                if message["method"].startswith("notifications/"):
                    return
                result = {"protocolVersion": "2025-11-25"}
                if message["method"] == "tools/call":
                    result = {"content": [{"type": "text", "text": "game refused action"}], "isError": True}
                body = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result})
                self.wfile.write((body if message["method"] == "initialize" else "data: " + body + "\n\n").encode())

        with http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                client = Client(f"http://127.0.0.1:{server.server_port}/mcp/", token="test-only")
                with self.assertRaisesRegex(RuntimeError, "game refused action"):
                    client.call("game_control", {"domain": "speed", "action": "pause"})
                self.assertEqual(seen[-1][1]["Mcp-Session-Id"], "test-session")
                self.assertEqual(seen[-1][1]["Authorization"], "Bearer test-only")
            finally:
                server.shutdown()
                thread.join()

    def test_failed_child_batch_is_not_a_successful_observation(self):
        client = object.__new__(Client)
        client.call = lambda *args: {"executed": 1, "failed": 1, "results": [{"text": "partial"}]}
        with self.assertRaisesRegex(RuntimeError, "Observation batch failed"):
            client.read("read_control", {})

    def test_observation_requires_verified_pause_and_keeps_exact_map(self):
        original_map = {"text": "# Native map\nY=4: ? O 建 人\n? = unrevealed\n"}

        class Game:
            paused = False

            def call(self, name, args):
                if name == "game_control":
                    return {"isPaused": self.paused}
                return original_map if args.get("command") == "zoom" else {"text": "Native colony state"}

        game = Game()
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(RuntimeError, "Pause was not verified"):
                observe(game, temp, bounds=(0, 0, 3, 3))
            self.assertFalse((Path(temp) / "latest.json").exists())
            game.paused = True
            returned = observe(game, temp, "test observation", bounds=(0, 0, 3, 3))
            recorded = json.loads((Path(temp) / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(returned["map"], original_map)
            self.assertEqual(recorded, returned)


class MeasurementTests(unittest.TestCase):
    @staticmethod
    def status(**changes):
        return {"pid": 42, "gameLoaded": True, "paused": True, "frameCount": 100,
                "unity": "test-only", "headless": True, **changes}

    @staticmethod
    def process(**changes):
        return {"startTicks": 500, "cpuSeconds": 2.0, "majorFaults": 0,
                "rssKiB": 100, "swapKiB": 0, **changes}

    @staticmethod
    def step(advanced=1.0, **changes):
        return subprocess.CompletedProcess([], 0, json.dumps({"simulationSecondsAdvanced": advanced,
            "timings": {"runningWallSeconds": 1.0}, **changes}), "")

    @contextlib.contextmanager
    def measurement(self, output, *, samples=1, warmup=0, statuses=None, processes=None, results=None, clock=None):
        attempts = samples + warmup
        with patch.object(sys, "argv", ["measure_steps.py", "--output", str(output), "--label", "candidate",
                "--samples", str(samples), "--warmup", str(warmup), "--seconds", "1"]), \
                patch("measure_steps.read_status", side_effect=statuses or
                      [self.status()] + [value for _ in range(attempts) for value in
                      (self.status(), self.status(frameCount=110))]), \
                patch("measure_steps.process_sample", side_effect=processes or
                      [value for _ in range(attempts) for value in
                      (self.process(), self.process(cpuSeconds=3.0))]), \
                patch("measure_steps.time.monotonic", side_effect=clock or itertools.count(step=2)), \
                patch("measure_steps.subprocess.run", side_effect=results or [self.step()] * attempts) as run, \
                patch("measure_steps.os.getloadavg", return_value=(0.0, 0.0, 0.0), create=True), \
                contextlib.redirect_stdout(io.StringIO()):
            yield run

    @staticmethod
    def report(output):
        # A report must be standard JSON even when rejecting non-finite input.
        def reject_constant(value):
            raise AssertionError("Non-finite number in evidence: " + value)
        return json.loads((output / "step-benchmark-candidate.json").read_text(), parse_constant=reject_constant)

    def test_existing_evidence_is_preserved_before_any_step(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            destination = output / "step-benchmark-candidate.json"
            original = b'{"complete": false, "error": "previous failed run"}\n'
            destination.write_bytes(original)
            with self.measurement(output) as run, self.assertRaises(FileExistsError):
                measure_steps.main()
            run.assert_not_called()
            self.assertEqual(destination.read_bytes(), original)

    def test_running_attempt_reserves_label_and_records_identity_before_step(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)

            def step(*args, **kwargs):
                report = self.report(output)
                self.assertFalse(report["complete"])
                self.assertEqual(report["processIdentity"], {"pid": 42, "startTicks": 500})
                self.assertEqual(report["attempts"][0]["stage"], "step")
                with self.assertRaises(FileExistsError):
                    measure_steps.main()
                self.assertEqual(self.report(output), report)
                return self.step()

            with self.measurement(output, results=step) as run:
                measure_steps.main()
            self.assertEqual(run.call_count, 1)
            report = self.report(output)
            self.assertTrue(report["complete"])
            self.assertEqual(report["samples"][0]["gameCpuSecondsPerNativeSecond"], 1.0)
            self.assertEqual(report["samples"][0]["updateFrames"], 10)

    def test_preflight_failure_is_retained_without_controlling_game(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            with self.measurement(output, statuses=[self.status(paused=False)]) as run, \
                    self.assertRaisesRegex(RuntimeError, "paused loaded"):
                measure_steps.main()
            run.assert_not_called()
            report = self.report(output)
            self.assertFalse(report["complete"])
            self.assertEqual(report["attempts"], [])
            self.assertIn("paused loaded", report["error"])

    def test_warmups_are_retained_but_excluded_from_measured_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            with self.measurement(output, warmup=1, results=[self.step(.5), self.step(2.0)]):
                measure_steps.main()
            report = self.report(output)
            self.assertTrue(report["complete"])
            self.assertEqual([attempt["warmup"] for attempt in report["attempts"]], [True, False])
            self.assertEqual([attempt["measurement"]["nativeSeconds"] for attempt in report["attempts"]], [.5, 2.0])
            self.assertEqual(len(report["samples"]), 1)
            self.assertEqual(report["summary"]["gameCpuSecondsPerNativeSecond"]["median"], .5)

    def test_failed_step_keeps_previous_samples_and_stderr(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            failed = subprocess.CompletedProcess([], 3, "partial output", "game step refused")
            with self.measurement(output, samples=2, results=[self.step(), failed]), \
                    self.assertRaisesRegex(RuntimeError, "game step refused"):
                measure_steps.main()
            report = self.report(output)
            self.assertFalse(report["complete"])
            self.assertEqual(len(report["samples"]), 1)
            self.assertEqual(len(report["attempts"]), 2)
            self.assertFalse(report["attempts"][0]["failed"])
            failure = report["attempts"][1]
            self.assertTrue(failure["failed"])
            self.assertEqual(failure["stage"], "step")
            self.assertEqual(failure["stepExitCode"], 3)
            self.assertEqual(failure["stdout"], "partial output")
            self.assertEqual(failure["stderr"], "game step refused")

    def test_failed_warmup_is_retained_and_stops_measurement(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            failed = subprocess.CompletedProcess([], 2, "", "warmup failed")
            with self.measurement(output, warmup=1, results=[failed]) as run, self.assertRaises(RuntimeError):
                measure_steps.main()
            self.assertEqual(run.call_count, 1)
            report = self.report(output)
            self.assertEqual(report["samples"], [])
            self.assertTrue(report["attempts"][0]["warmup"])
            self.assertTrue(report["attempts"][0]["failed"])

    def test_timeout_preserves_bounded_partial_output(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            timeout = subprocess.TimeoutExpired([], 21, output=b"x" * 5000, stderr=b"deadline reached\xff")
            with self.measurement(output, results=[timeout]), self.assertRaises(subprocess.TimeoutExpired):
                measure_steps.main()
            report = self.report(output)
            self.assertFalse(report["complete"])
            failure = report["attempts"][0]
            self.assertEqual(failure["errorType"], "TimeoutExpired")
            self.assertEqual(failure["stdout"], "x" * 4000)
            self.assertEqual(failure["stderr"], "deadline reached\ufffd")

    def test_same_pid_with_new_start_time_during_step_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            with self.measurement(output, processes=[self.process(), self.process(cpuSeconds=3, startTicks=501)]), \
                    self.assertRaisesRegex(RuntimeError, "identity during"):
                measure_steps.main()
            report = self.report(output)
            self.assertFalse(report["complete"])
            self.assertEqual(report["samples"], [])
            self.assertEqual(report["attempts"][0]["stage"], "process-after")

    def test_same_pid_with_new_start_time_between_steps_never_runs_again(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            processes = [self.process(), self.process(cpuSeconds=3), self.process(startTicks=501)]
            with self.measurement(output, samples=2, processes=processes) as run, \
                    self.assertRaisesRegex(RuntimeError, "identity before"):
                measure_steps.main()
            self.assertEqual(run.call_count, 1)
            report = self.report(output)
            self.assertEqual(len(report["samples"]), 1)
            self.assertEqual(report["attempts"][1]["stage"], "process-before")

    def test_invalid_native_progress_never_becomes_a_speedup(self):
        for advanced in (0, -1, True, float("nan"), float("inf"), -float("inf"), "1", None):
            with self.subTest(advanced=advanced), tempfile.TemporaryDirectory() as temp:
                output = Path(temp)
                with self.measurement(output, results=[self.step(advanced)]), \
                        self.assertRaisesRegex(RuntimeError, "simulationSecondsAdvanced"):
                    measure_steps.main()
                report = self.report(output)
                self.assertFalse(report["complete"])
                self.assertEqual(report["samples"], [])
                self.assertNotIn("summary", report)

    def test_invalid_or_decreasing_process_metrics_are_rejected(self):
        for name, value in (("cpuSeconds", 1), ("cpuSeconds", float("inf")),
                            ("cpuSeconds", float("nan")), ("cpuSeconds", True),
                            ("majorFaults", -1), ("rssKiB", -1), ("swapKiB", float("inf"))):
            with self.subTest(metric=name, value=value), tempfile.TemporaryDirectory() as temp:
                output = Path(temp)
                after = self.process(cpuSeconds=3)
                after[name] = value
                with self.measurement(output, processes=[self.process(), after]), self.assertRaises(RuntimeError):
                    measure_steps.main()
                report = self.report(output)
                self.assertFalse(report["complete"])
                self.assertEqual(report["samples"], [])

    def test_frame_counter_decrease_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            statuses = [self.status(), self.status(), self.status(frameCount=99)]
            with self.measurement(output, statuses=statuses), self.assertRaisesRegex(RuntimeError, "updateFrames delta"):
                measure_steps.main()
            self.assertEqual(self.report(output)["samples"], [])

    def test_nonfinite_or_negative_step_timings_are_rejected(self):
        for value in (float("inf"), float("nan"), True, -1):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temp:
                output = Path(temp)
                with self.measurement(output, results=[self.step(timings={"runningWallSeconds": value})]), \
                        self.assertRaisesRegex(RuntimeError, "stepTimings.runningWallSeconds"):
                    measure_steps.main()
                self.assertFalse(self.report(output)["complete"])

    def test_invalid_wall_duration_or_negative_control_overhead_is_rejected(self):
        for value in (float("nan"), float("inf"), -1, .5):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temp:
                output = Path(temp)
                with self.measurement(output, clock=[0, value]), self.assertRaises(RuntimeError):
                    measure_steps.main()
                self.assertFalse(self.report(output)["complete"])

    def test_final_pause_and_process_are_required_before_acceptance(self):
        for final in (self.status(paused=False), self.status(pid=43), self.status(gameLoaded=False)):
            with self.subTest(final=final), tempfile.TemporaryDirectory() as temp:
                output = Path(temp)
                with self.measurement(output, statuses=[self.status(), self.status(), final]), \
                        self.assertRaisesRegex(RuntimeError, "native work and pause"):
                    measure_steps.main()
                report = self.report(output)
                self.assertFalse(report["complete"])
                self.assertEqual(report["samples"], [])
                self.assertEqual(report["attempts"][0]["stage"], "final-status")

    @staticmethod
    def stat(start=500, state="S"):
        fields = ["0"] * 25
        fields[0], fields[9], fields[11], fields[12], fields[19] = state, "2", "200", "100", str(start)
        return "42 (game (worker)) " + " ".join(fields)

    def test_proc_sample_records_identity_and_handles_parentheses_in_comm(self):
        with patch.object(Path, "read_text", side_effect=[self.stat(), "VmRSS: 100 kB\nVmSwap: 20 kB", self.stat()]), \
                patch("measure_steps.os.sysconf", return_value=100, create=True):
            sample = measure_steps.process_sample(42)
        self.assertEqual(sample, {"startTicks": 500, "cpuSeconds": 3.0, "majorFaults": 2,
                                  "rssKiB": 100, "swapKiB": 20})

    def test_proc_sample_rejects_exited_or_reused_identity(self):
        for reads in ([self.stat(state="Z")],
                      [self.stat(), "VmRSS: 100 kB", self.stat(state="Z")],
                      [self.stat(), "VmRSS: 100 kB", self.stat(start=501)]):
            with self.subTest(reads=reads), patch.object(Path, "read_text", side_effect=reads), \
                    patch("measure_steps.os.sysconf", return_value=100, create=True), \
                    self.assertRaisesRegex(RuntimeError, "process exited"):
                measure_steps.process_sample(42)


if __name__ == "__main__":
    unittest.main()
