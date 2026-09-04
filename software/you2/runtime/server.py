"""The face: a local HTTP server that streams what the brain is doing.

stdlib only. `http.server` plus server-sent events is genuinely enough for one
display on a loopback interface, and every dependency avoided here is RSS not
spent on a 2 GB board that is also running an audio stack.

Binds to 127.0.0.1 by default. The whole premise of the device is that nothing
leaves the room, and a memory server listening on 0.0.0.0 would be a poor way
to honour that — `--host` exists for developing against it from a laptop, and
says so.
"""

from __future__ import annotations

import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .events import BUS, Bus

UI_ROOT = Path(__file__).resolve().parents[2] / "ui"


class Handler(BaseHTTPRequestHandler):
    server_version = "you2"
    brain: Any = None
    bus: Bus = BUS

    # ------------------------------------------------------------- plumbing

    def log_message(self, fmt: str, *args: Any) -> None:
        return          # the journal is the log; this would just be noise

    def _send(self, code: int, body: bytes, ctype: str,
              extra: dict[str, str] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, payload: Any, code: int = 200) -> None:
        self._send(code, json.dumps(payload, default=str).encode(),
                   "application/json")

    # ----------------------------------------------------------------- GET

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        route = url.path.rstrip("/") or "/"

        if route == "/events":
            return self._events()
        if route == "/api/state":
            return self._json({"state": str(self.bus.state),
                               "subscribers": self.bus.subscribers})
        if route == "/api/constellation":
            from . import viz
            if self.brain is None:
                return self._json({"nodes": [], "edges": []})
            return self._json(viz.constellation(self.brain.store))
        if route == "/api/stats":
            return self._json(self.brain.stats() if self.brain else {})
        if route == "/api/why":
            q = parse_qs(url.query).get("q", [""])[0]
            if not self.brain or not q:
                return self._json({"explain": ""})
            return self._json({"explain": self.brain.why(q)})
        if route == "/api/journal":
            if not self.brain:
                return self._json([])
            kind = parse_qs(url.query).get("kind", [None])[0]
            return self._json(self.brain.store.journal(kind, limit=40))
        return self._static(route)

    # ---------------------------------------------------------------- POST

    def do_POST(self) -> None:  # noqa: N802
        route = urlparse(self.path).path.rstrip("/") or "/"
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "bad json"}, 400)

        if self.brain is None:
            return self._json({"error": "no brain"}, 503)

        if route == "/api/say":
            text = str(payload.get("text", "")).strip()
            if not text:
                return self._json({"error": "empty"}, 400)
            reply = self.brain.say(text)
            return self._json({
                "text": reply.text, "route": reply.route,
                "ms": reply.timing.total_ms,
                "tokens": reply.workspace.tokens if reply.workspace else 0,
                "salience": reply.salience.as_dict() if reply.salience else None,
            })
        if route == "/api/night":
            report = self.brain.night()
            return self._json({"journal": report.journal(),
                               **report.as_dict()})
        if route == "/api/rate":
            from ..discovery import verdict
            verdict(self.brain.store, int(payload["insight"]),
                    bool(payload.get("useful")))
            return self._json({"ok": True})
        return self._json({"error": "not found"}, 404)

    # ------------------------------------------------------------------ SSE

    def _events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            for event in self.bus.stream():
                self.wfile.write(event.sse())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass        # the display went away; nothing to do about it

    # --------------------------------------------------------------- static

    def _static(self, route: str) -> None:
        name = "index.html" if route == "/" else route.lstrip("/")
        target = (UI_ROOT / name).resolve()
        # Path traversal: the UI directory is the whole world.
        if not str(target).startswith(str(UI_ROOT.resolve())) or not target.is_file():
            return self._send(404, b"not found", "text/plain")
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), ctype)


def serve(brain: Any = None, host: str = "127.0.0.1", port: int = 8765, *,
          bus: Bus = BUS, background: bool = False) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"brain": brain, "bus": bus})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    if background:
        threading.Thread(target=httpd.serve_forever, daemon=True,
                         name="you2-server").start()
    return httpd
