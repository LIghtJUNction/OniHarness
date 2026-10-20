"""Loopback MCP transport and pause-verified observations; never launches a game."""

from __future__ import annotations

import inspect
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from functools import wraps
from typing import Any, ParamSpec, TypeVar

from ._version import __version__

P = ParamSpec("P")
T = TypeVar("T")

PROTOCOL = "2025-11-25"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
TOON_HEADER = re.compile(
    r'^(?:(?:"(?:[^"\\]|\\.)*"|[^\W\d][\w.-]*)(?:\[\d+[,\t]?\])?(?:\{[^{}\n]*\})?|\[\d+[,\t]?\](?:\{[^{}\n]*\})?):(?:[ \t\r\n]|$)'
)


class McpError(RuntimeError):
    """Transport, protocol, tool or pause-verification failure."""


def _decode(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    if TOON_HEADER.match(text.lstrip()):
        from toon_format import decode

        try:
            value = decode(text)
        except ValueError as error:
            raise McpError("Malformed TOON tool output") from error
        if isinstance(value, (dict, list)):
            return value
    return {"text": text}


def _payload(result: dict[str, Any]) -> Any:
    text = "\n".join(
        item["text"] for item in result.get("content", []) if item.get("type") == "text"
    )
    if result.get("isError"):
        raise McpError(text or "MCP tool failed")
    if isinstance(result.get("structuredContent"), (dict, list)):
        return result["structuredContent"]
    return _decode(text)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class McpClient:
    """Connect explicitly to a running local OniMcp server (not thread safe).

    Proxies and redirects are disabled so bearer tokens stay on the selected
    loopback endpoint. JSON, SSE and TOON tool results are supported. Low-level
    ``call_tool`` can issue writes; it does not implicitly pause the game.
    """

    def __init__(
        self,
        url: str = "http://127.0.0.1:8788/mcp/",
        *,
        token: str | None = None,
        timeout: float = 30,
    ):
        parsed = urllib.parse.urlsplit(url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in ("localhost", "127.0.0.1", "::1")
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ValueError(
                "Use an HTTP loopback MCP URL without embedded credentials or a fragment"
            )
        self.url = url
        self._token = (
            token if token is not None else os.environ.get("ONIHARNESS_MCP_TOKEN")
        )
        self.timeout = self._timeout(timeout)
        self.session: str | None = None
        self.protocol = PROTOCOL
        self._sequence = 0
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect()
        )
        initialized = self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "oniharness-python", "version": __version__},
            },
        )
        version = initialized.get("protocolVersion")
        if version not in (PROTOCOL, "2025-06-18", "2025-03-26"):
            raise McpError(f"Unsupported MCP protocol version: {version}")
        self.protocol = version
        self.request("notifications/initialized", notification=True)

    @staticmethod
    def _timeout(value: float) -> float:
        if not math.isfinite(value) or value <= 0:
            raise ValueError("timeout must be finite and positive")
        return value

    def request(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        notification: bool = False,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        self._sequence += 1
        request_id = self._sequence
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if not notification:
            message["id"] = request_id
        if params is not None:
            message["params"] = dict(params)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Mcp-Protocol-Version": self.protocol,
        }
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        if self._token:
            headers["Authorization"] = "Bearer " + self._token
        request = urllib.request.Request(
            self.url, json.dumps(message, allow_nan=False).encode(), headers
        )
        try:
            with self._opener.open(
                request,
                timeout=self.timeout if timeout is None else self._timeout(timeout),
            ) as response:
                self.session = response.headers.get("Mcp-Session-Id", self.session)
                if notification:
                    return {}
                if response.headers.get_content_type() == "text/event-stream":
                    value = self._read_sse(response, request_id)
                else:
                    body = response.read(MAX_RESPONSE_BYTES + 1)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise McpError("MCP response exceeded 8 MiB")
                    value = json.loads(body)
        except urllib.error.HTTPError as error:
            error.close()
            raise McpError(f"MCP request {method} failed: HTTP {error.code}") from error
        except (OSError, ValueError) as error:
            raise McpError(f"MCP request {method} failed: {error}") from error
        if (
            not isinstance(value, dict)
            or value.get("id") != request_id
            or value.get("jsonrpc") != "2.0"
        ):
            raise McpError("Invalid MCP response or mismatched request ID")
        if "error" in value:
            raise McpError("MCP error: " + str(value["error"]))
        if not isinstance(value.get("result"), dict):
            raise McpError("MCP response has no result object")
        return value["result"]

    @staticmethod
    def _read_sse(response: Any, request_id: int) -> Any:
        size = 0
        data: list[str] = []
        while True:
            line = response.readline(MAX_RESPONSE_BYTES - size + 1)
            size += len(line)
            if size > MAX_RESPONSE_BYTES:
                raise McpError("MCP response exceeded 8 MiB")
            if not line or line in (b"\n", b"\r\n"):
                if data:
                    value = json.loads("\n".join(data))
                    if isinstance(value, dict) and value.get("id") == request_id:
                        return value
                    data = []
                if not line:
                    raise McpError("SSE ended without a matching MCP response")
            elif line.startswith(b"data:"):
                data.append(line[5:].decode("utf-8").removeprefix(" ").rstrip("\r\n"))

    def list_tools(self) -> list[dict[str, Any]]:
        """Read the server's current tool catalog, including paginated results."""
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            result = self.request("tools/list", {"cursor": cursor} if cursor else {})
            page = result.get("tools")
            if not isinstance(page, list) or any(
                not isinstance(tool, dict) for tool in page
            ):
                raise McpError("Invalid MCP tool catalog")
            tools.extend(page)
            cursor = result.get("nextCursor")
            if cursor is None:
                return tools
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise McpError("Invalid or repeated MCP tool cursor")
            seen.add(cursor)

    def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        """Call an explicit public tool; tool errors raise McpError."""
        args = dict(arguments or {})
        args.setdefault("task", "OniHarness Python API")
        result = self.request(
            "tools/call", {"name": name, "arguments": args}, timeout=timeout
        )
        return _payload(result)


class Game:
    """Pause and observe an already running game. No automatic launch or resume."""

    def __init__(self, client: McpClient | None = None):
        self.client = client if client is not None else McpClient()

    def paused(self, function: Callable[P, T]) -> Callable[P, T]:
        """Decorate a synchronous operation: verify pause before and in finally.

        The function's return value, name and signature are preserved. A failed
        initial pause prevents execution; exceptions still trigger a final pause.
        This does not authorize writes or add a bounded resume/step mechanism.
        """
        if (
            inspect.iscoroutinefunction(function)
            or inspect.isgeneratorfunction(function)
            or inspect.isasyncgenfunction(function)
        ):
            raise TypeError(
                "@game.paused requires a synchronous, non-generator function"
            )

        @wraps(function)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
            self.pause()
            try:
                result = function(*args, **kwargs)
                if (
                    inspect.isawaitable(result)
                    or inspect.isgenerator(result)
                    or inspect.isasyncgen(result)
                ):
                    if inspect.iscoroutine(result) or inspect.isgenerator(result):
                        result.close()
                    raise TypeError(
                        "@game.paused cannot return deferred async/generator work"
                    )
                return result
            finally:
                self.pause()

        return wrapped

    def pause(self) -> dict[str, Any]:
        self.client.call_tool("game_control", {"domain": "speed", "action": "pause"})
        status = self.client.call_tool(
            "game_control", {"domain": "speed", "action": "time"}
        )
        if not isinstance(status, dict) or status.get("isPaused") is not True:
            raise McpError("Pause was not verified; observation aborted")
        return status

    def observe(
        self,
        *,
        bounds: tuple[int, int, int, int] | None = None,
        intent: str = "Read-only observation",
    ) -> dict[str, Any]:
        """Return the exact map and colony/dupe text, after verifying pause.

        Coordinates are inclusive, with a maximum of 1600 cells. Without bounds,
        read a small rectangle around the current camera. No camera movement.
        """
        if bounds is not None:
            self._validate_bounds(bounds)
        status = self.pause()
        snapshot = self.client.call_tool(
            "world_editor",
            {"command": "read", "path": "/active/index.md", "includeState": True},
        )
        if bounds is None:
            camera = self.client.call_tool(
                "navigation_control", {"action": "get_view"}
            )["position"]
            x, y = math.floor(camera["x"]), math.floor(camera["y"])
            bounds = (max(0, x - 16), max(0, y - 12), max(0, x + 15), max(0, y + 11))
            self._validate_bounds(bounds)
        x1, y1, x2, y2 = bounds
        arguments = {
            "command": "zoom",
            "path": "/active/map/viewport.md",
            "format": "edit",
            "compact": False,
            "syncView": False,
            "focusCamera": False,
            "x1": x1,
            "y1": y1,
            "x2": x2,
            "y2": y2,
            "maxCells": 1600,
        }
        text_map = self.client.call_tool("world_editor", arguments)
        if not isinstance(text_map, dict) or not text_map.get("text"):
            raise McpError("No virtual map text returned")
        dupes = self.client.call_tool(
            "world_editor", {"command": "read", "path": "/active/dupes/index.md"}
        )
        return {
            "schema": 1,
            "capturedAt": datetime.now(timezone.utc).isoformat(),
            "paused": True,
            "intent": intent,
            "time": status,
            "snapshot": snapshot,
            "map": text_map,
            "mapRequest": arguments,
            "dupes": dupes,
        }

    @staticmethod
    def _validate_bounds(bounds: tuple[int, int, int, int]) -> None:
        if len(bounds) != 4 or any(type(value) is not int for value in bounds):
            raise ValueError("bounds must contain four integer cell coordinates")
        x1, y1, x2, y2 = bounds
        if (
            x1 < 0
            or y1 < 0
            or x2 < x1
            or y2 < y1
            or (x2 - x1 + 1) * (y2 - y1 + 1) > 1600
        ):
            raise ValueError(
                "Map bounds must be ordered, nonnegative and at most 1600 cells"
            )
