"""Exercise the public API over real local HTTP, JSON, SSE and TOON."""

import http.server
import inspect
import json
import os
import threading
import time
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from oniharness import Game, McpClient, McpError, mcp


@contextmanager
def endpoint(responder, *, initialize=None, initialized=None):
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_DELETE(self):
            seen.append(({"method": "DELETE"}, dict(self.headers)))
            result = responder({"method": "DELETE"}, self)
            if result is not None:
                self.send_response(204)
                self.end_headers()

        def do_POST(self):
            message = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((message, dict(self.headers)))
            if message["method"] == "initialize":
                result = (initialize(message, self) if initialize
                          else {"protocolVersion": "2025-06-18"})
                if result is None:
                    return
            elif message["method"].startswith("notifications/"):
                if initialized:
                    initialized(message, self)
                else:
                    self.send_response(202)
                    self.end_headers()
                return
            else:
                result = responder(message, self)
                if result is None:
                    return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Mcp-Session-Id",
                             getattr(self.server, "mcp_session", "fixture-session"))
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
    def test_failed_initialization_releases_allocated_session(self):
        def initialize(message, handler):
            return {"protocolVersion": "2025-06-18", "capabilities": []}

        with endpoint(lambda *_: {}, initialize=initialize) as (url, seen):
            with self.assertRaisesRegex(McpError, "capabilities"):
                McpClient(url)
            self.assertEqual([message["method"] for message, _ in seen],
                             ["initialize", "DELETE"])
            self.assertEqual(seen[-1][1]["Mcp-Session-Id"], "fixture-session")

    def test_failed_initialized_notification_releases_session_and_keeps_error(self):
        def initialized(message, handler):
            handler.send_response(503)
            handler.end_headers()

        def cleanup(message, handler):
            handler.send_response(401)
            handler.end_headers()

        with endpoint(cleanup, initialized=initialized) as (url, seen):
            with self.assertRaisesRegex(McpError, "HTTP 503") as raised:
                McpClient(url)
            self.assertEqual(raised.exception.method, "notifications/initialized")
            self.assertEqual(raised.exception.http_status, 503)
            self.assertEqual([message["method"] for message, _ in seen],
                             ["initialize", "notifications/initialized", "DELETE"])
            if hasattr(raised.exception, "add_note"):
                self.assertIn("HTTP 401", raised.exception.__notes__[0])

    def test_failed_reconnect_releases_old_and_new_sessions(self):
        generation = 0

        def initialize(message, handler):
            nonlocal generation
            generation += 1
            handler.server.mcp_session = f"session-{generation}"
            return {"protocolVersion": "2025-06-18"}

        def initialized(message, handler):
            handler.send_response(503 if generation == 2 else 202)
            handler.end_headers()

        with endpoint(lambda *_: {}, initialize=initialize,
                      initialized=initialized) as (url, seen):
            client = McpClient(url)
            with self.assertRaisesRegex(McpError, "HTTP 503"):
                client.reconnect()
            deleted = [headers["Mcp-Session-Id"] for message, headers in seen
                       if message["method"] == "DELETE"]
            self.assertEqual(deleted, ["session-1", "session-2"])
            self.assertIsNone(client.session)
            with self.assertRaisesRegex(McpError, "closed"):
                client.list_tools()
            client.reconnect()
            self.assertEqual(client.session, "session-3")
            self.assertTrue(all("Mcp-Session-Id" not in headers
                                for message, headers in seen
                                if message["method"] == "initialize"))
            self.assertFalse(any(message["method"] == "tools/call"
                                 for message, _ in seen))
            client.close()

    def test_optional_delete_405_allows_local_close_and_reconnect(self):
        def respond(message, handler):
            handler.send_response(405)
            handler.end_headers()

        with endpoint(respond) as (url, seen):
            client = McpClient(url)
            client.close()
            self.assertIsNone(client.session)
            client.reconnect()
            self.assertEqual(sum(message["method"] == "initialize"
                                 for message, _ in seen), 2)
            client.close()

    def test_context_manager_releases_session_and_close_is_idempotent(self):
        with endpoint(lambda *_: {"tools": []}) as (url, seen):
            with McpClient(url, token="test-only") as client:
                self.assertEqual(client.list_tools(), [])
            deletion = seen[-1]
            self.assertEqual(deletion[0]["method"], "DELETE")
            self.assertEqual(deletion[1]["Mcp-Session-Id"], "fixture-session")
            self.assertEqual(deletion[1]["Authorization"], "Bearer test-only")
            self.assertEqual(deletion[1]["Mcp-Protocol-Version"], "2025-06-18")
            count = len(seen)
            client.close()
            self.assertEqual(len(seen), count)
            self.assertIsNone(client.session)
            with self.assertRaisesRegex(McpError, "closed"):
                client.list_tools()

    def test_session_loss_can_reconnect_without_replaying_tool_writes(self):
        stale = True

        def respond(message, handler):
            if stale:
                handler.send_response(404)
                handler.send_header("Content-Type", "application/json")
                handler.end_headers()
                handler.wfile.write(json.dumps({
                    "jsonrpc": "2.0", "id": None,
                    "error": {"code": -32600, "message": "Session not found or terminated"}
                }).encode())
                return None
            return {"tools": []}

        with endpoint(respond) as (url, seen):
            client = McpClient(url)
            with self.assertRaisesRegex(McpError, "reconnect") as raised:
                client.call_tool("orders_control", {"action": "dig"})
            self.assertEqual(raised.exception.http_status, 404)
            self.assertEqual(raised.exception.code, -32600)
            self.assertEqual(raised.exception.method, "tools/call")
            client.reconnect()
            stale = False
            self.assertEqual(client.list_tools(), [])
            self.assertEqual(sum(m["method"] == "tools/call" for m, _ in seen), 1)
            initializations = [(m, h) for m, h in seen if m["method"] == "initialize"]
            self.assertEqual(len(initializations), 2)
            self.assertTrue(all("Mcp-Session-Id" not in h for _, h in initializations))

    def test_http_errors_preserve_retry_details_and_bound_error_bodies(self):
        def respond(message, handler):
            handler.send_response(503)
            handler.send_header("Content-Type", "application/json")
            handler.end_headers()
            handler.wfile.write(json.dumps({
                "jsonrpc": "2.0", "id": message["id"],
                "error": {
                    "code": -32000,
                    "message": "Main thread is busy",
                    "data": {"reasonCode": "main_thread_busy", "retryable": True}
                }
            }).encode())

        with endpoint(respond) as (url, _):
            client = McpClient(url)
            with self.assertRaisesRegex(McpError, "Main thread is busy") as raised:
                client.list_tools()
            failure = raised.exception
            self.assertEqual(failure.http_status, 503)
            self.assertEqual(failure.method, "tools/list")
            self.assertEqual(failure.code, -32000)
            self.assertEqual(failure.data, {"reasonCode": "main_thread_busy", "retryable": True})
            with (
                patch.object(mcp, "MAX_ERROR_BYTES", 8),
                self.assertRaisesRegex(McpError, "HTTP 503") as bounded,
            ):
                client.list_tools()
            self.assertIsNone(bounded.exception.data)

    def test_json_rpc_errors_keep_method_code_and_data(self):
        def respond(message, handler):
            handler.send_response(200)
            handler.send_header("Content-Type", "application/json")
            handler.end_headers()
            handler.wfile.write(json.dumps({
                "jsonrpc": "2.0", "id": message["id"],
                "error": {"code": -32602, "message": "arguments must be an object",
                          "data": {"field": "arguments"}}
            }).encode())

        with endpoint(respond) as (url, _):
            client = McpClient(url)
            with self.assertRaisesRegex(McpError, "arguments must be an object") as raised:
                client.request("tools/call")
            self.assertEqual(raised.exception.method, "tools/call")
            self.assertEqual(raised.exception.code, -32602)
            self.assertEqual(raised.exception.data, {"field": "arguments"})

    def test_cleanup_failure_does_not_hide_a_body_failure(self):
        def respond(message, handler):
            handler.send_response(503)
            handler.end_headers()

        with endpoint(respond) as (url, _):
            with (
                self.assertRaisesRegex(ValueError, "body failed") as raised,
                McpClient(url),
            ):
                raise ValueError("body failed")
            if hasattr(raised.exception, "add_note"):
                self.assertTrue(any("cleanup also failed" in note
                                    for note in raised.exception.__notes__))

    def test_token_defaults_to_environment_and_game_can_connect_itself(self):
        with (
            endpoint(lambda *_: {"tools": []}) as (url, seen),
            patch.dict(os.environ, {"ONIHARNESS_MCP_TOKEN": "environment-test-only"}),
        ):
            client = McpClient(url)
            client.list_tools()
            self.assertEqual(
                seen[-1][1]["Authorization"], "Bearer environment-test-only"
            )
            with patch("oniharness.mcp.McpClient", return_value=client) as connect:
                game = Game()
            self.assertIs(game.client, client)
            connect.assert_called_once_with()

    def test_paused_decorator_preserves_return_and_verifies_before_and_after(self):
        with endpoint(lambda *_: {"structuredContent": {"isPaused": True}}) as (
            url,
            seen,
        ):
            game = Game(McpClient(url))

            @game.paused
            def operation(value: int = 3) -> int:
                """Operation documentation."""
                self.assertEqual(seen[-1][0]["params"]["arguments"]["action"], "time")
                return value * 2

            self.assertEqual(operation(4), 8)
            self.assertEqual(operation.__name__, "operation")
            self.assertEqual(operation.__doc__, "Operation documentation.")
            self.assertEqual(
                str(inspect.signature(operation)), "(value: int = 3) -> int"
            )
            actions = [
                message["params"]["arguments"]["action"]
                for message, _ in seen
                if message["method"] == "tools/call"
            ]
            self.assertEqual(actions, ["pause", "time", "pause", "time"])

    def test_paused_decorator_attempts_final_pause_on_exception(self):
        with endpoint(lambda *_: {"structuredContent": {"isPaused": True}}) as (
            url,
            seen,
        ):
            game = Game(McpClient(url))

            @game.paused
            def broken():
                raise ValueError("body failed")

            with self.assertRaisesRegex(ValueError, "body failed"):
                broken()
            actions = [
                message["params"]["arguments"]["action"]
                for message, _ in seen
                if message["method"] == "tools/call"
            ]
            self.assertEqual(actions, ["pause", "time", "pause", "time"])

    def test_failed_pause_blocks_body_and_failed_final_pause_is_not_success(self):
        statuses = iter([False, True, False])
        called = []

        def respond(message, handler):
            args = message["params"]["arguments"]
            return {
                "structuredContent": {
                    "isPaused": next(statuses) if args["action"] == "time" else True
                }
            }

        with endpoint(respond) as (url, _):
            game = Game(McpClient(url))

            @game.paused
            def operation():
                called.append(True)

            with self.assertRaisesRegex(McpError, "Pause was not verified"):
                operation()
            self.assertEqual(called, [])
            with self.assertRaisesRegex(McpError, "Pause was not verified"):
                operation()
            self.assertEqual(called, [True])

    def test_paused_decorator_rejects_delayed_work(self):
        with endpoint(lambda *_: {"structuredContent": {"isPaused": True}}) as (url, _):
            game = Game(McpClient(url))

            async def asynchronous():
                return 1

            def generator():
                yield 1

            for function in (asynchronous, generator):
                with self.assertRaisesRegex(TypeError, "synchronous"):
                    game.paused(function)

            @game.paused
            def delayed():
                return asynchronous()

            with self.assertRaisesRegex(TypeError, "deferred"):
                delayed()

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
