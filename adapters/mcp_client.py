"""A small MCP client: enough to discover and call tools.

Two transports, one interface:

  HttpSession(url)            Streamable HTTP: JSON-RPC POSTed to one endpoint,
                              answers as JSON or as a short SSE stream.
  StdioSession(argv, cwd)     A server started as a subprocess, newline-delimited
                              JSON-RPC on stdin/stdout.

Both expose initialize(), list_tools(), call_tool(). No other MCP feature is
used. The server is never asked to change anything during discovery: only
initialize and tools/list are read-only by definition; tools/call happens
when a task is assigned.
"""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import threading
from typing import Any

import httpx

PROTOCOL_VERSION = "2025-03-26"
CLIENT_INFO = {"name": "bevro", "version": "0.4"}
DEFAULT_TIMEOUT = 15.0


class McpError(Exception):
    pass


def _rpc(method: str, params: dict[str, Any] | None, req_id: int | None) -> dict[str, Any]:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    if req_id is not None:
        msg["id"] = req_id
    return msg


def _check(reply: dict[str, Any]) -> dict[str, Any]:
    if "error" in reply and reply["error"]:
        err = reply["error"]
        raise McpError(str(err.get("message") if isinstance(err, dict) else err))
    result = reply.get("result")
    return result if isinstance(result, dict) else {}


class _Session:
    """Shared behaviour; subclasses implement _request/_notify."""

    server_info: dict[str, Any]
    instructions: str | None

    def __init__(self) -> None:
        self._next_id = 0
        self.server_info = {}
        self.instructions = None

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    def _request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:  # pragma: no cover
        raise NotImplementedError

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:  # pragma: no cover
        raise NotImplementedError

    def initialize(self) -> dict[str, Any]:
        result = self._request(
            "initialize",
            {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": CLIENT_INFO},
        )
        self.server_info = dict(result.get("serverInfo") or {})
        self.instructions = result.get("instructions") if isinstance(result.get("instructions"), str) else None
        self._notify("notifications/initialized")
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(20):  # bounded: a server cannot page forever
            params = {"cursor": cursor} if cursor else {}
            result = self._request("tools/list", params)
            tools += [t for t in result.get("tools") or [] if isinstance(t, dict) and t.get("name")]
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._request("tools/call", {"name": name, "arguments": arguments})

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _parse_sse(text: str) -> list[dict[str, Any]]:
    """Messages from a text/event-stream body: each event's data lines, JSON-decoded."""
    messages: list[dict[str, Any]] = []
    data: list[str] = []
    for line in text.splitlines() + [""]:
        if line.startswith("data:"):
            data.append(line[5:].strip())
        elif line == "" and data:
            try:
                parsed = json.loads("\n".join(data))
                if isinstance(parsed, dict):
                    messages.append(parsed)
            except ValueError:
                pass
            data = []
    return messages


class HttpSession(_Session):
    def __init__(self, url: str, headers: dict[str, str] | None = None, timeout: float = DEFAULT_TIMEOUT, transport: httpx.BaseTransport | None = None) -> None:
        super().__init__()
        self.url = url
        self.session_id: str | None = None
        self._headers = dict(headers or {})
        self._client = httpx.Client(timeout=timeout, transport=transport, follow_redirects=False)

    def _post(self, body: dict[str, Any]) -> httpx.Response:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **self._headers}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        headers["MCP-Protocol-Version"] = PROTOCOL_VERSION
        try:
            response = self._client.post(self.url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise McpError(f"MCP server unreachable: {type(exc).__name__}") from exc
        sid = response.headers.get("mcp-session-id")
        if sid:
            self.session_id = sid
        return response

    def _request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        req_id = self._id()
        response = self._post(_rpc(method, params, req_id))
        if response.status_code >= 400:
            raise McpError(f"MCP server answered HTTP {response.status_code}")
        ctype = response.headers.get("content-type", "")
        replies: list[dict[str, Any]]
        if "text/event-stream" in ctype:
            replies = _parse_sse(response.text)
        else:
            try:
                data = response.json()
            except ValueError as exc:
                raise McpError("MCP server sent something Bevro couldn't read") from exc
            replies = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
        for reply in replies:
            if reply.get("id") == req_id:
                return _check(reply)
        raise McpError("MCP server did not answer the request")

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        try:
            self._post(_rpc(method, params, None))
        except McpError:
            pass  # a lost notification is not fatal

    def close(self) -> None:
        self._client.close()


class StdioSession(_Session):
    def __init__(self, argv: list[str], cwd: str | None = None, env: dict[str, str] | None = None, timeout: float = DEFAULT_TIMEOUT) -> None:
        super().__init__()
        if not argv:
            raise McpError("no command to start the MCP server")
        self.timeout = timeout
        try:
            self._proc = subprocess.Popen(
                argv,
                cwd=cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                env=env if env is not None else os.environ.copy(),
                start_new_session=True,
            )
        except OSError as exc:
            raise McpError(f"could not start the MCP server: {exc}") from exc
        self._lines: queue.Queue[str | None] = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            self._lines.put(line)
        self._lines.put(None)

    def _send(self, msg: dict[str, Any]) -> None:
        assert self._proc.stdin is not None
        try:
            self._proc.stdin.write(json.dumps(msg) + "\n")
            self._proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise McpError("MCP server stopped") from exc

    def _request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        req_id = self._id()
        self._send(_rpc(method, params, req_id))
        while True:
            try:
                line = self._lines.get(timeout=self.timeout)
            except queue.Empty as exc:
                raise McpError("MCP server did not answer in time") from exc
            if line is None:
                raise McpError("MCP server exited")
            try:
                reply = json.loads(line)
            except ValueError:
                continue  # servers sometimes log to stdout; ignore non-JSON
            if isinstance(reply, dict) and reply.get("id") == req_id:
                return _check(reply)

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._send(_rpc(method, params, None))

    def close(self) -> None:
        proc = self._proc
        if proc.poll() is not None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            proc.wait(timeout=5)


def text_of(result: dict[str, Any]) -> str:
    """The text content of a tools/call result, joined."""
    parts: list[str] = []
    for block in result.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n\n".join(parts).strip()
