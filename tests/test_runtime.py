"""Behavioral checks for the real-game client, without launching a game."""
import http.server
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import contextlib
import io
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from runtime import observe, main, read_status, validate_headless, wait_ready
from runtime_mcp import Client
from install_runtime import enable_entry
from measure_steps import require_log_markers


class RuntimeTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
