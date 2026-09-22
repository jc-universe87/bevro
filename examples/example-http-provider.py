"""A minimal HTTP provider for trying the Connect flow.

    python3 examples/example-http-provider.py            # listens on :8765

Then in Bevro: Connect → name "Echo", method API (or Local / self-hosted),
address http://host.docker.internal:8765 (Linux: http://172.17.0.1:8765),
and ask it something from Agents.
"""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        self._send(200, {"status": "ok"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        request = payload.get("request", "")
        self._send(
            200,
            {
                "state": "completed",
                "summary": f"Done. Echoed {len(request.split())} words.",
                "artifacts": [
                    {"type": "note", "title": "Echo", "mime_type": "text/plain", "payload": {"text": request}},
                    {"type": "hologram", "title": "Something Bevro has not seen before", "external_url": "https://example.com/hologram/1"},
                ],
            },
        )

    def log_message(self, *args) -> None:  # keep the terminal quiet
        pass


if __name__ == "__main__":
    print("example provider listening on http://0.0.0.0:8765")
    HTTPServer(("0.0.0.0", 8765), Handler).serve_forever()
