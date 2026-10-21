"""Notebook behavior over a real local MCP fixture; no game fallback."""

import struct
import tempfile
import threading
import time
import unittest
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from oniharness import Game, GameFrame, McpClient, McpError
from test_mcp import endpoint


def chunk(kind, content):
    return (
        struct.pack(">I", len(content))
        + kind
        + content
        + struct.pack(">I", zlib.crc32(kind + content) & 0xFFFFFFFF)
    )


PNG = (
    b"\x89PNG\r\n\x1a\n"
    + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    + chunk(b"IDAT", zlib.compress(b"\0\xff\0\0"))
    + chunk(b"IEND", b"")
)


class NotebookTests(unittest.TestCase):
    def test_frame_embeds_and_saves_exact_png(self):
        frame = GameFrame(PNG, "2026-10-20T00:00:00+00:00", 42)
        self.assertEqual(frame.size, (1, 1))
        self.assertEqual(frame._repr_png_(), PNG)
        self.assertIn("paused=True", repr(frame))
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                frame.save(Path(directory) / "frame.png").read_bytes(), PNG
            )
        for invalid in (b"", PNG[:-3], PNG[:-5] + b"wrong"):
            with self.assertRaises(ValueError):
                GameFrame(invalid, "now")

    def test_frame_waits_for_async_write_and_never_returns_old_capture(self):
        writes = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "oni-mcp" / "screenshots"
            root.mkdir(parents=True)
            (root / "latest.png").write_bytes(b"stale screenshot")

            def respond(message, handler):
                args = message["params"]["arguments"]
                if args["action"] != "screenshot":
                    return {"structuredContent": {"isPaused": True}}
                target = root / args["filename"]

                def write():
                    target.write_bytes(PNG[:20])
                    time.sleep(0.08)
                    target.write_bytes(PNG)

                thread = threading.Thread(target=write)
                thread.start()
                writes.append(thread)
                return {"structuredContent": {"path": str(target), "cycle": 12}}

            with (
                endpoint(respond) as (url, seen),
                patch("oniharness.mcp.tempfile.gettempdir", return_value=directory),
            ):
                game = Game(McpClient(url))
                first = game.frame(timeout=1)
                second = game.frame(timeout=1)
                self.assertEqual(first.png, PNG)
                self.assertEqual(second.cycle, 12)
                args = [
                    m["params"]["arguments"]
                    for m, _ in seen
                    if m["method"] == "tools/call"
                ]
                captures = [a for a in args if a["action"] == "screenshot"]
                self.assertNotEqual(captures[0]["filename"], captures[1]["filename"])
                self.assertEqual(args[-1]["action"], "time")
                self.assertFalse(
                    any(a["action"] in {"resume", "set_speed"} for a in args)
                )
            for thread in writes:
                thread.join()

    def test_missing_or_unexpected_capture_is_explicit_failure(self):
        with endpoint(lambda *_: {"structuredContent": {"isPaused": True}}) as (url, _):
            game = Game(McpClient(url))
            with self.assertRaisesRegex(McpError, "expected local PNG"):
                game.frame(timeout=0.1)
            with self.assertRaises(ValueError):
                game.frame(timeout=0)

        def missing(message, handler):
            args = message["params"]["arguments"]
            value = {"isPaused": True}
            if args["action"] == "screenshot":
                value = {
                    "path": str(
                        Path(tempfile.gettempdir())
                        / "oni-mcp"
                        / "screenshots"
                        / args["filename"]
                    )
                }
            return {"structuredContent": value}

        with (
            endpoint(missing) as (url, _),
            self.assertRaisesRegex(McpError, "graphics-enabled ONI"),
        ):
            Game(McpClient(url)).frame(timeout=0.05)

    def test_read_keeps_decoded_data_and_prevents_write_overrides(self):
        def respond(message, handler):
            args = message["params"]["arguments"]
            if args.get("command") == "read":
                return {"structuredContent": {"dupes": [{"stress": 12}]}}
            return {"structuredContent": {"isPaused": True}}

        with endpoint(respond) as (url, seen):
            game = Game(McpClient(url))
            self.assertEqual(
                game.read("/active/dupes/index.oni")["dupes"][0]["stress"], 12
            )
            self.assertEqual(seen[-1][0]["params"]["arguments"]["command"], "read")
            for path, options in [
                ("/etc/passwd", {}),
                ("/active/index.md", {"command": "write"}),
            ]:
                with self.assertRaises(ValueError):
                    game.read(path, **options)

    def test_watch_bounds_are_validated_before_contacting_game(self):
        game = Game.__new__(Game)
        for frames, interval in [
            (0, 1),
            (121, 1),
            (True, 1),
            (1, 0),
            (1, float("nan")),
        ]:
            with self.assertRaises(ValueError):
                game.watch(frames=frames, interval=interval)

    def test_watch_updates_one_display_and_pauses_even_when_interrupted(self):
        game = Game.__new__(Game)
        frame = GameFrame(PNG, "2026-10-20T00:00:00+00:00")
        updates = []
        displays = []
        handle = SimpleNamespace(update=updates.append)

        def display(value, *, display_id):
            self.assertTrue(display_id)
            displays.append(value)
            return handle

        with (
            patch(
                "oniharness.mcp.import_module",
                return_value=SimpleNamespace(display=display),
            ),
            patch("oniharness.mcp.time.sleep"),
            patch.object(game, "frame", return_value=frame) as capture,
            patch.object(game, "pause") as pause,
        ):
            self.assertIs(game.watch(frames=3, interval=0.05), frame)
            self.assertEqual(capture.call_count, 3)
            self.assertEqual(displays, [frame])
            self.assertEqual(updates, [frame, frame])
            pause.assert_called_once_with()
            pause.reset_mock()
            capture.side_effect = KeyboardInterrupt
            with self.assertRaises(KeyboardInterrupt):
                game.watch(frames=3)
            pause.assert_called_once_with()
