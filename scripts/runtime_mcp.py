"""Small, loopback-only OniMcp client for real-game tests and shared observations."""
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request
import uuid

PROTOCOL = "2025-11-25"

TOON_HEADER = re.compile(r'^(?:(?:"(?:[^"\\]|\\.)*"|[^\W\d][\w.-]*)(?:\[\d+[,\t]?\])?(?:\{[^{}\n]*\})?|\[\d+[,\t]?\](?:\{[^{}\n]*\})?):(?:[ \t]|$)')


def decode_text(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    if not text.strip():
        return {"text": text}
    structured = TOON_HEADER.match(text.lstrip()) is not None
    if not structured:
        return {"text": text}
    try:
        from toon_format import decode
    except ImportError as error:
        if structured:
            raise RuntimeError("Tool returned TOON; install scripts/requirements-runtime.txt in the runtime Python environment") from error
        return {"text": text}
    try:
        value = decode(text)
    except ValueError as error:
        if structured:
            raise RuntimeError("Malformed TOON tool output") from error
        return {"text": text}
    return value if isinstance(value, (dict, list)) else {"text": text}


def payload(result):
    text = "\n".join(c["text"] for c in result.get("content", []) if c.get("type") == "text")
    if result.get("isError"):
        raise RuntimeError(text or "MCP tool failed")
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    return decode_text(text)


class Client:
    def __init__(self, url="http://127.0.0.1:8788/mcp/", token=None):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1", "::1") or parsed.username or parsed.password:
            raise ValueError("Use an HTTP loopback MCP URL without embedded credentials")
        self.url, self.token, self.session, self.sequence = url, token, None, 0
        # No proxies or redirects: credentials must stay on the selected loopback endpoint.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                   "clientInfo": {"name": "OniHarness", "version": "0.1"}})
        self.request("notifications/initialized", notification=True)

    def request(self, method, params=None, notification=False, timeout=30):
        self.sequence += 1
        message = {"jsonrpc": "2.0", "method": method}
        if not notification:
            message["id"] = self.sequence
        if params is not None:
            message["params"] = params
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "Mcp-Protocol-Version": PROTOCOL}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        request = urllib.request.Request(self.url, json.dumps(message).encode(), headers)
        with self.opener.open(request, timeout=timeout) as response:
            self.session = response.headers.get("Mcp-Session-Id", self.session)
            body = response.read(8 * 1024 * 1024 + 1).decode("utf-8")
        if notification:
            return None
        if len(body.encode()) > 8 * 1024 * 1024:
            raise RuntimeError("MCP response exceeded 8 MiB")
        if body.lstrip().startswith("{"):
            response = json.loads(body)
        else:
            responses = [json.loads(line[5:].strip()) for line in body.splitlines() if line.startswith("data:")]
            response = next((r for r in responses if r.get("id") == self.sequence), {})
        if response.get("id") != self.sequence or "result" not in response:
            raise RuntimeError("Invalid or failed MCP response: " + str(response.get("error", "missing result")))
        return response["result"]

    def call(self, name, arguments, timeout=30):
        arguments = dict(arguments)
        arguments.setdefault("task", "OniHarness real-game test")
        return payload(self.request("tools/call", {"name": name, "arguments": arguments}, timeout=timeout))

    def read(self, name, arguments):
        """Call an aggregate through public batching when the installed catalog exposes it."""
        batch = self.call("server_control", {"domain": "batch", "action": "call_many",
                          "calls": [{"name": name, "arguments": arguments}], "responseMode": "full",
                          "requireAllValid": True, "stopOnError": True,
                          "defaults": {"task": "OniHarness paused observation"}})
        results = batch.get("results", [])
        if batch.get("failed", 0) or len(results) != 1 or batch.get("executed") != 1:
            raise RuntimeError("Observation batch failed: " + json.dumps(batch, ensure_ascii=False))
        child = results[0]
        if child.get("isError") or child.get("success") is False:
            raise RuntimeError("Observation failed: " + str(child))
        text = child.get("text", "")
        return decode_text(text)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
