"""Loopback-only status and folder selection UI."""

from __future__ import annotations

import hmac
import json
import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from typing import Callable

from .paths import PathSafetyError, SelectedRoot, canonicalize_root


class FolderSelection:
    def __init__(self) -> None:
        self._roots: tuple[SelectedRoot, ...] = ()
        self._lock = Lock()

    def replace(self, roots: list[str]) -> tuple[SelectedRoot, ...]:
        selected = tuple(SelectedRoot(canonicalize_root(root)) for root in roots)
        if not selected:
            raise PathSafetyError("at least one selected root is required")
        with self._lock:
            self._roots = selected
        return selected

    def paths(self) -> tuple[Path, ...]:
        with self._lock:
            return tuple(root.path for root in self._roots)


class LocalStatusServer:
    """A loopback-only server with per-instance CSRF token."""

    def __init__(self, selection: FolderSelection | None = None, status: Callable[[], dict] | None = None) -> None:
        self.selection = selection or FolderSelection()
        self.status = status or (lambda: {"roots": [str(path) for path in self.selection.paths()]})
        self.csrf_token = secrets.token_urlsafe(32)
        self._server: ThreadingHTTPServer | None = None
        self._serving = False

    @property
    def address(self) -> tuple[str, int] | None:
        return self._server.server_address if self._server else None

    def start(self) -> tuple[str, int]:
        if self._server is not None:
            raise RuntimeError("status server already started")
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:
                return

            def _send(self, code: HTTPStatus, payload: dict) -> None:
                data = json.dumps(payload, separators=(",", ":")).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802
                if self.path not in ("/", "/status"):
                    self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                    return
                self._send(HTTPStatus.OK, owner.status())

            def do_POST(self) -> None:  # noqa: N802
                if self.path != "/folders":
                    self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                    return
                token = self.headers.get("X-CSRF-Token", "")
                if not hmac.compare_digest(token, owner.csrf_token):
                    self._send(HTTPStatus.FORBIDDEN, {"error": "csrf"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length <= 0 or length > 64 * 1024:
                        raise ValueError("bounded body required")
                    payload = json.loads(self.rfile.read(length))
                    roots = payload["roots"]
                    if not isinstance(roots, list) or not all(isinstance(root, str) for root in roots):
                        raise ValueError("roots must be a string list")
                    selected = owner.selection.replace(roots)
                except (ValueError, KeyError, json.JSONDecodeError, PathSafetyError):
                    self._send(HTTPStatus.BAD_REQUEST, {"error": "invalid_selection"})
                    return
                self._send(HTTPStatus.OK, {"roots": [str(root.path) for root in selected]})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        return self._server.server_address

    def serve_forever(self) -> None:
        if self._server is None:
            self.start()
        assert self._server is not None
        self._serving = True
        try:
            self._server.serve_forever()
        finally:
            self._serving = False

    def close(self) -> None:
        if self._server is not None:
            if self._serving:
                self._server.shutdown()
            self._server.server_close()
            self._server = None
