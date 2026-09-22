"""Fixture A: an already-running HTTP agent with its own credential and a JSON task endpoint.

Started by tests (in a thread, or as a subprocess with the project as its
working directory so it counts as "already running from that folder").
It describes itself with OpenAPI. It holds its own upstream credential
(FIXTURE_UPSTREAM_KEY) - Bevro never needs to know it.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OPENAPI = {
    "openapi": "3.1.0",
    "info": {"title": "Fixture Desk", "description": "Answers questions about widgets and drafts replies.", "version": "1.0"},
    "paths": {
        "/task": {"post": {"summary": "Run a task", "operationId": "run_task", "tags": ["research", "drafting"], "requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": {"prompt": {"type": "string"}}, "required": ["prompt"]}}}}, "responses": {"200": {"content": {"application/json": {"schema": {"type": "object", "properties": {"answer": {"type": "string"}, "link": {"type": "string"}}}}}}}}},
        "/health": {"get": {"summary": "Health"}},
    },
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet
        pass

    def _json(self, code: int, body) -> None:
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/openapi.json":
            return self._json(200, OPENAPI)
        if self.path == "/health":
            return self._json(200, {"status": "ok"})
        if self.path == "/":
            return self._json(200, {"name": "Fixture Desk"})
        return self._json(404, {"detail": "not found"})

    def do_POST(self):
        if self.path != "/task":
            return self._json(404, {"detail": "not found"})
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        upstream = os.environ.get("FIXTURE_UPSTREAM_KEY", "")
        if not upstream:
            return self._json(500, {"detail": "the agent's own credential is missing"})
        return self._json(200, {"answer": f"Widget answer for: {body.get('prompt')}", "link": "https://desk.example/answers/1"})


def serve(port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
