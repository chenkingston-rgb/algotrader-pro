"""Authenticated localhost-only status server; access through an SSH tunnel."""
from __future__ import annotations

import base64
import hmac
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "monitor" / "index.html"
STATUS = Path(os.getenv("STATUS_PATH", str(ROOT / "state" / "dashboard.json")))
HOST = "127.0.0.1"
PORT = int(os.getenv("DASHBOARD_PORT", "8787"))
USERNAME = os.getenv("DASHBOARD_USERNAME", "")
PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")


def _authorized(header: str | None) -> bool:
    if not USERNAME or len(PASSWORD) < 16 or not header or not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
    except Exception:
        return False
    return hmac.compare_digest(decoded, f"{USERNAME}:{PASSWORD}")


class Handler(BaseHTTPRequestHandler):
    server_version = "Trend3Status/1"

    def _headers(self, status: int, content_type: str, length: int = 0) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 (stdlib handler contract)
        if not _authorized(self.headers.get("Authorization")):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Trend-3 status"')
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        path = self.path.split("?", 1)[0]
        source = INDEX if path in {"/", "/index.html"} else STATUS if path == "/dashboard.json" else None
        if source is None or not source.is_file():
            self._headers(404, "text/plain; charset=utf-8", 0)
            return
        if source.stat().st_size > 2_000_000:
            self._headers(503, "text/plain; charset=utf-8", 0)
            return
        body = source.read_bytes()
        kind = "text/html; charset=utf-8" if source == INDEX else "application/json; charset=utf-8"
        self._headers(200, kind, len(body))
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        # Never log the Authorization header.  BaseHTTPRequestHandler's normal
        # request log contains only method/path/status.
        super().log_message(fmt, *args)


def main() -> None:
    if not USERNAME or len(PASSWORD) < 16:
        raise RuntimeError("DASHBOARD_USERNAME and a 16+ character DASHBOARD_PASSWORD are required")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
