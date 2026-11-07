"""Loopback development page for selecting roots and controlling the local index."""

from __future__ import annotations

import hmac
import html
import json
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from typing import Callable
from urllib.parse import urlsplit

from .paths import PathSafetyError, SelectedRoot


class FolderSelection:
    """Thread-safe view of the pinned roots currently owned by the runtime."""

    def __init__(self) -> None:
        self._roots: tuple[SelectedRoot, ...] = ()
        self._lock = Lock()

    def replace(self, roots: list[str | Path] | tuple[SelectedRoot, ...]) -> tuple[SelectedRoot, ...]:
        selected = tuple(root if isinstance(root, SelectedRoot) else SelectedRoot(Path(root)) for root in roots)
        if len({str(root.path) for root in selected}) != len(selected):
            raise PathSafetyError("duplicate selected folder")
        with self._lock:
            self._roots = selected
        return selected

    def paths(self) -> tuple[Path, ...]:
        with self._lock:
            return tuple(root.path for root in self._roots)

    def roots(self) -> tuple[SelectedRoot, ...]:
        with self._lock:
            return self._roots


class LocalStatusServer:
    """CSRF-protected, host-checked loopback page; it never handles file bytes."""

    def __init__(
        self,
        selection: FolderSelection | None = None,
        status: Callable[[], dict] | None = None,
        *,
        select_roots: Callable[[list[str]], object] | None = None,
        delete_index: Callable[[], object] | None = None,
        resume_indexing: Callable[[], object] | None = None,
    ) -> None:
        self.selection = selection or FolderSelection()
        self.status = status or (lambda: {"roots": [str(path) for path in self.selection.paths()]})
        self.select_roots = select_roots
        self.delete_index = delete_index
        self.resume_indexing = resume_indexing
        self.csrf_token = secrets.token_urlsafe(32)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._serving = False
        self._serving_event = threading.Event()

    @property
    def address(self) -> tuple[str, int] | None:
        if self._server is None:
            return None
        host, port = self._server.server_address[:2]
        return str(host), int(port)

    @staticmethod
    def _page(token: str, status: dict) -> tuple[bytes, str]:
        roots = status.get("roots", [])
        if not isinstance(roots, list) or not all(isinstance(root, str) for root in roots):
            roots = []
        selected = html.escape("\n".join(roots), quote=True)
        csrf = html.escape(token, quote=True)
        paused = bool(status.get("indexing_paused", True))
        state = "Paused" if paused else ("Indexing" if roots else "Waiting for folder selection")
        nonce = secrets.token_urlsafe(18)
        page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Local connector</title><style nonce="{nonce}">body{{font:16px system-ui;max-width:46rem;margin:3rem auto;padding:0 1rem;color:#20242a}}textarea{{width:100%;min-height:6rem}}button{{margin:.35rem;padding:.55rem 1rem}}#message{{min-height:1.5rem}}</style></head>
<body><h1>Local connector</h1><p>Development scaffold. Selected text files stay on this device; no cloud connection is active.</p>
<label for="roots">Folder paths on this device (one per line)</label><textarea id="roots" spellcheck="false">{selected}</textarea>
<p><button id="save">Save selected folders</button> <button id="delete">Delete local index…</button> <button id="resume">Resume indexing</button></p>
<p>Index status: <strong id="state">{html.escape(state)}</strong></p><p id="problem" role="status" aria-live="polite">{html.escape('; '.join(status.get('errors', [])))}</p><p id="message" role="status" aria-live="polite"></p>
<script nonce="{nonce}">const csrf="{csrf}";
async function send(path,body){{const r=await fetch(path,{{method:'POST',headers:{{'Content-Type':'application/json','X-CSRF-Token':csrf}},body:JSON.stringify(body)}});const d=await r.json();if(!r.ok)throw Error(d.error||'Request failed');return d}}
document.querySelector('#save').onclick=async()=>{{try{{const roots=document.querySelector('#roots').value.split(/\\r?\\n/).map(x=>x.trim()).filter(Boolean);await send('/folders',{{roots}});const d=await refresh(true);document.querySelector('#message').textContent=d.indexing_paused?'Folders saved. Indexing remains paused; choose Resume indexing when ready.':'Folders saved. Indexing follows the selected folders.'}}catch(e){{document.querySelector('#message').textContent=e.message}}}};
document.querySelector('#delete').onclick=async()=>{{if(!confirm('Delete the local index? Source files will remain untouched. Indexing will pause until you resume it.'))return;try{{await send('/delete',{{confirmed:true}});document.querySelector('#message').textContent='Local index deleted. Indexing is paused; choose Resume indexing when ready.';await refresh()}}catch(e){{document.querySelector('#message').textContent=e.message}}}};
document.querySelector('#resume').onclick=async()=>{{try{{await send('/resume',{{}});const d=await refresh();document.querySelector('#message').textContent=d.roots.length?'Indexing resumed.':'No folders selected. Choose folders to start indexing.'}}catch(e){{document.querySelector('#message').textContent=e.message}}}};
async function refresh(updateRoots=false){{const r=await fetch('/status',{{cache:'no-store'}});const d=await r.json();document.querySelector('#state').textContent=d.indexing_paused?'Paused':((d.roots||[]).length?'Indexing':'Waiting for folder selection');document.querySelector('#problem').textContent=(d.errors||[]).join('; ');if(updateRoots)document.querySelector('#roots').value=(d.roots||[]).join('\\n');return d}}refresh();setInterval(refresh,5000);
</script></body></html>"""
        return page.encode("utf-8"), nonce

    def start(self) -> tuple[str, int]:
        if self._server is not None:
            raise RuntimeError("status server already started")
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:
                return

            def _send(
                self,
                code: HTTPStatus,
                payload: dict | bytes,
                *,
                content_type: str = "application/json",
                nonce: str = "",
            ) -> None:
                data = payload if isinstance(payload, bytes) else json.dumps(payload, separators=(",", ":")).encode()
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header(
                    "Content-Security-Policy",
                    (
                        f"default-src 'none'; style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'; "
                        "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
                    ),
                )
                self.end_headers()
                self.wfile.write(data)

            def _host_is_local(self) -> bool:
                address = owner.address
                if address is None:
                    return False
                try:
                    parsed = urlsplit("//" + self.headers.get("Host", ""))
                    return parsed.hostname == "127.0.0.1" and parsed.port == address[1]
                except ValueError:
                    return False

            def _origin_is_local(self) -> bool:
                origin = self.headers.get("Origin")
                if origin is None:
                    return True
                address = owner.address
                try:
                    parsed = urlsplit(origin)
                    return (
                        address is not None
                        and parsed.scheme == "http"
                        and parsed.hostname == "127.0.0.1"
                        and parsed.port == address[1]
                        and parsed.path == ""
                    )
                except ValueError:
                    return False

            def _read_json(self) -> dict:
                if self.headers.get_content_type() != "application/json":
                    raise ValueError("application/json required")
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 64 * 1024:
                    raise ValueError("bounded JSON body required")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("JSON object required")
                return payload

            def _authorized_post(self) -> bool:
                if not self._host_is_local() or not self._origin_is_local():
                    self._send(HTTPStatus.FORBIDDEN, {"error": "local_origin_required"})
                    return False
                token = self.headers.get("X-CSRF-Token", "")
                if not hmac.compare_digest(token, owner.csrf_token):
                    self._send(HTTPStatus.FORBIDDEN, {"error": "csrf"})
                    return False
                return True

            def do_GET(self) -> None:  # noqa: N802
                if not self._host_is_local():
                    self._send(HTTPStatus.FORBIDDEN, {"error": "local_host_required"})
                    return
                if self.path == "/":
                    page, nonce = owner._page(owner.csrf_token, owner.status())
                    self._send(HTTPStatus.OK, page, content_type="text/html; charset=utf-8", nonce=nonce)
                elif self.path == "/status":
                    self._send(HTTPStatus.OK, owner.status())
                else:
                    self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})

            def do_POST(self) -> None:  # noqa: N802
                if self.path not in ("/folders", "/delete", "/resume"):
                    self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                    return
                if not self._authorized_post():
                    return
                try:
                    payload = self._read_json()
                    if self.path == "/folders":
                        roots = payload.get("roots")
                        if not isinstance(roots, list) or not all(isinstance(root, str) for root in roots):
                            raise ValueError("roots must be a string list")
                        if owner.select_roots is None:
                            selected = owner.selection.replace(roots)
                        else:
                            selected = owner.select_roots(roots)
                        self._send(HTTPStatus.OK, {"roots": [str(root.path) for root in selected]})
                    elif self.path == "/delete":
                        if payload != {"confirmed": True}:
                            raise ValueError("explicit confirmation required")
                        if owner.delete_index is None:
                            raise ValueError("index controls unavailable")
                        owner.delete_index()
                        self._send(HTTPStatus.OK, {"ok": True})
                    else:
                        if payload:
                            raise ValueError("empty JSON object required")
                        if owner.resume_indexing is None:
                            raise ValueError("index controls unavailable")
                        owner.resume_indexing()
                        self._send(HTTPStatus.OK, {"ok": True})
                except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError, PathSafetyError) as exc:
                    self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)[:160]})

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            allow_reuse_address = False

        self._server = Server(("127.0.0.1", 0), Handler)
        self._serving_event.clear()
        self._thread = threading.Thread(target=self.serve_forever, name="local-connector-ui", daemon=True)
        self._thread.start()
        if not self._serving_event.wait(timeout=5):
            self.close()
            raise RuntimeError("loopback UI server did not start")
        return self.address or ("127.0.0.1", 0)

    def serve_forever(self) -> None:
        if self._server is None:
            raise RuntimeError("call start() to launch the local UI")
        self._serving = True
        self._serving_event.set()
        try:
            self._server.serve_forever()
        finally:
            self._serving = False

    def close(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            if self._serving:
                server.shutdown()
            server.server_close()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)
