"""Test-only webhook receiver: keeps every Alertmanager POST and returns them on GET /received."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

received: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        length = int(self.headers.get("Content-Length", 0))
        received.append(json.loads(self.rfile.read(length)))
        self.send_response(200)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        data = json.dumps(received).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: object) -> None:
        """Quiet."""


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
