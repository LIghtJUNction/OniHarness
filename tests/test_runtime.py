"""Behavioral checks for the real-game client, without launching a game."""
import http.server
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from runtime import observe
from runtime_mcp import Client
from install_runtime import enable_entry


class RuntimeTests(unittest.TestCase):
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
