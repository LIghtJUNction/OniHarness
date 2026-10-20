"""Exercise the public API over real local HTTP, JSON, SSE and TOON."""

import http.server
import json
import os
import threading
import time
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from oniharness import Game, McpClient, McpError, mcp


@contextmanager
def endpoint(responder):
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            message = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((message, dict(self.headers)))
            if message["method"] == "initialize":
                result = {"protocolVersion": "2025-06-18"}
            elif message["method"].startswith("notifications/"):
                self.send_response(202)
                self.end_headers()
                return
            else:
                result = responder(message, self)
                if result is None:
                    return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Mcp-Session-Id", "fixture-session")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {"jsonrpc": "2.0", "id": message["id"], "result": result}
                ).encode()
            )

    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/mcp/", seen
        finally:
            server.shutdown()
            thread.join()


class McpTests(unittest.TestCase):
    def test_handshake_session_token_proxy_bypass_and_structured_result(self):
        def respond(message, handler):
            return {
                "structuredContent": {"isPaused": True},
                "content": [{"type": "text", "text": "isPaused: false"}],
            }

        with (
            endpoint(respond) as (url, seen),
            patch.dict(
                os.environ, {"http_proxy": "http://127.0.0.1:1", "no_proxy": ""}
            ),
        ):
            client = McpClient(url, token="test-only")
            self.assertEqual(
                client.call_tool("game_control", {"task": "own intent"}),
                {"isPaused": True},
            )
            self.assertEqual(seen[-1][1]["Mcp-Session-Id"], "fixture-session")
            self.assertEqual(seen[-1][1]["Mcp-Protocol-Version"], "2025-06-18")
            self.assertEqual(seen[-1][1]["Authorization"], "Bearer test-only")
            self.assertEqual(seen[-1][0]["params"]["arguments"]["task"], "own intent")

    def test_json_toon_and_plain_text_are_preserved(self):
        texts = iter(
            [
                '{"isPaused":true}',
                "choices[2]{id,enabled}:\n  survival,true\n  sandbox,false",
                "# Map\n人 O 建",
            ]
        )
        with endpoint(
            lambda *_: {"content": [{"type": "text", "text": next(texts)}]}
        ) as (url, _):
            client = McpClient(url)
            self.assertEqual(client.call_tool("read"), {"isPaused": True})
            self.assertEqual(
                client.call_tool("read")["choices"][1],
                {"id": "sandbox", "enabled": False},
            )
            self.assertEqual(client.call_tool("read"), {"text": "# Map\n人 O 建"})

    def test_malformed_toon_and_tool_errors_raise(self):
        results = iter(
            [
                {"content": [{"type": "text", "text": "choices[2]: survival"}]},
                {
                    "isError": True,
                    "structuredContent": {"success": True},
                    "content": [{"type": "text", "text": "refused"}],
                },
            ]
        )
        with endpoint(lambda *_: next(results)) as (url, _):
            client = McpClient(url)
            with self.assertRaisesRegex(McpError, "Malformed TOON"):
                client.call_tool("read")
            with self.assertRaisesRegex(McpError, "refused"):
                client.call_tool("write")

    def test_sse_multiline_notifications_and_keepalive(self):
        released = threading.Event()

        def respond(message, handler):
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.end_headers()
            body = json.dumps(
                {"jsonrpc": "2.0", "id": message["id"], "result": {"tools": []}},
                indent=2,
            )
            handler.wfile.write(
                b': keepalive\n\ndata: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n'
            )
            handler.wfile.write(
                (
                    "\n".join("data: " + line for line in body.splitlines()) + "\n\n"
                ).encode()
            )
            handler.wfile.flush()
            released.wait(3)

        with endpoint(respond) as (url, _):
            client = McpClient(url)
            started = time.monotonic()
            try:
                self.assertEqual(client.list_tools(), [])
                self.assertLess(time.monotonic() - started, 2)
            finally:
                released.set()

    def test_response_id_rpc_errors_and_response_size_are_not_success(self):
        def respond(message, handler):
            handler.send_response(200)
            handler.send_header("Content-Type", "application/json")
            handler.end_headers()
            handler.wfile.write(json.dumps(message["params"]).encode())

        with endpoint(respond) as (url, _):
            client = McpClient(url)
            for body, expected in [
                ({"jsonrpc": "2.0", "id": 999, "result": {}}, "mismatched"),
                ({"jsonrpc": "2.0", "id": 4, "error": {"code": -1}}, "MCP error"),
            ]:
                with (
                    self.subTest(body=body),
                    self.assertRaisesRegex(McpError, expected),
                ):
                    client.request("custom", body)
            with (
                patch.object(mcp, "MAX_RESPONSE_BYTES", 64),
                self.assertRaisesRegex(McpError, "exceeded"),
            ):
                client.request("custom", {"data": "x" * 100})

    def test_redirect_never_sends_credentials_to_another_endpoint(self):
        redirected = []
        with endpoint(lambda *args: redirected.append(args) or {}) as (destination, _):

            def redirect(message, handler):
                handler.send_response(307)
                handler.send_header("Location", destination)
                handler.end_headers()

            with endpoint(redirect) as (url, _):
                client = McpClient(url, token="test-only")
                with self.assertRaises(McpError):
                    client.list_tools()
        self.assertEqual(redirected, [])

    def test_nonlocal_credential_and_fragment_urls_rejected(self):
        for url in [
            "http://example.com/",
            "https://127.0.0.1/",
            "http://secret@localhost/",
            "http://localhost/#fragment",
        ]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                McpClient(url)

    def test_paginated_catalog_and_repeated_cursor(self):
        pages = iter(
            [
                {"tools": [{"name": "first"}], "nextCursor": "next"},
                {"tools": [{"name": "last"}]},
            ]
        )
        with endpoint(lambda *_: next(pages)) as (url, seen):
            self.assertEqual(
                [tool["name"] for tool in McpClient(url).list_tools()],
                ["first", "last"],
            )
            self.assertEqual(seen[-1][0]["params"], {"cursor": "next"})
        with (
            endpoint(lambda *_: {"tools": [], "nextCursor": "same"}) as (url, _),
            self.assertRaisesRegex(McpError, "repeated"),
        ):
            McpClient(url).list_tools()

    def test_observation_verifies_pause_before_reading_and_keeps_exact_map(self):
        paused = False
        original_map = "# Native map\nY=4: ? O 建 人\n? = unrevealed\n"

        def respond(message, handler):
            args = message["params"]["arguments"]
            name = message["params"]["name"]
            value = (
                {"isPaused": paused}
                if name == "game_control"
                else {
                    "text": original_map if args.get("command") == "zoom" else "colony"
                }
            )
            return {"structuredContent": value}

        with endpoint(respond) as (url, seen):
            game = Game(McpClient(url))
            with self.assertRaisesRegex(McpError, "Pause was not verified"):
                game.observe(bounds=(0, 0, 3, 3))
            self.assertFalse(
                any(
                    item[0].get("params", {}).get("name") == "world_editor"
                    for item in seen
                )
            )
            paused = True
            observation = game.observe(bounds=(0, 0, 3, 3))
            self.assertTrue(observation["paused"])
            self.assertEqual(observation["map"]["text"], original_map)
            self.assertFalse(observation["mapRequest"]["focusCamera"])
            self.assertFalse(
                any(
                    item[0].get("params", {}).get("arguments", {}).get("action")
                    == "resume"
                    for item in seen
                )
            )
            for bounds in [
                (0, 0, 100, 100),
                (1, 1, 0, 0),
                (-1, 0, 0, 0),
                (0, 0, 1.5, 2),
            ]:
                before = len(seen)
                with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                    game.observe(bounds=bounds)
                self.assertEqual(len(seen), before)


if __name__ == "__main__":
    unittest.main()
