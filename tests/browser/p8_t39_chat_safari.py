"""Render the real chat template in Safari at desktop and mobile widths.

No application server, database, providers, network, or owner Safari tabs are
used. The WebDriver session owns the window it opens; automation is never
configured or enabled by this script.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.web.deps import templates
from app.web.nav import NAV_ITEMS

DRIVER = "/usr/bin/safaridriver"

_APPLESCRIPT_DESKTOP = r"""on run argv
    set ownedID to missing value
    tell application "Safari" to set beforeIDs to id of every window
    try
        tell application "Safari" to make new document with properties {URL:(item 1 of argv)}
        delay 1
        tell application "Safari" to set afterIDs to id of every window
        set addedIDs to {}
        repeat with candidateID in afterIDs
            if beforeIDs does not contain (contents of candidateID) then set end of addedIDs to contents of candidateID
        end repeat
        if (count of addedIDs) is not 1 then error "Could not identify exactly one Safari-owned window"
        set ownedID to item 1 of addedIDs
        tell application "Safari" to set bounds of (window id ownedID) to {0, 0, 1600, 1000}
        set resultText to ""
        repeat 10 times
            delay 0.5
            tell application "Safari" to set resultText to do JavaScript "document.readyState + '|' + (document.querySelector('#p8t39-result')?.textContent || '')" in current tab of (window id ownedID)
            if resultText contains "|{" then exit repeat
        end repeat
        if not (resultText contains "|{") then error "Safari localhost page did not expose its DOM result: " & resultText
        log "Safari desktop result: " & resultText
    on error errorMessage number errorNumber
        if ownedID is not missing value then try
            tell application "Safari" to close (window id ownedID)
        end try
        error errorMessage number errorNumber
    end try
    if ownedID is not missing value then tell application "Safari" to close (window id ownedID)
end run"""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(base: str, method: str, path: str, body: dict | None = None, timeout: float = 10) -> dict:
    data = None if body is None else json.dumps(body).encode()
    request = Request(
        base + path,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method=method,
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def _render_chat(destination: Path) -> None:
    context = {
        "user": SimpleNamespace(username="owner", role=None, has_admin_access=lambda: False),
        "tenant": SimpleNamespace(id=1, name="Fixture workspace"),
        "workspaces": [],
        "memberships": [],
        "unread_notifications": 0,
        "connection_alerts": [],
        "perms": None,
        "member_level": None,
        "nav": NAV_ITEMS,
        "mobile_nav": NAV_ITEMS,
        "csrf": "fixture",
        "flashes": [],
        "path": "/app/chat",
        "section": "chat",
        "plan_labels": {},
        "_tz": "UTC",
        "messages": [],
        "is_owner": True,
        "request": SimpleNamespace(query_params={"embedded": "1"}),
        "url_for": lambda name, **kwargs: "/static/" + kwargs["path"],
        "static_v": lambda path: "/static/" + path,
    }
    html = templates.env.get_template("web/chat.html").render(**context)
    html = re.sub(r"<script\b[\s\S]*?</script>", "", html, flags=re.I)
    html = re.sub(r'<link\b[^>]*rel="stylesheet"[^>]*>', "", html, flags=re.I)
    css = "\n".join(
        (ROOT / "app/static/css" / name).read_text() for name in ("theme.css", "ui.css", "shell.css", "chat.css")
    )
    js = (ROOT / "app/static/js/chat.js").read_text()
    checks = r"""
      const labels = [];
      const check = (condition, label) => {
        if (!condition) throw new Error(label);
        labels.push(label);
      };
      const app = chatApp();
      const root = document.querySelector('.chat-starters');
      for (const starter of app.starters) {
        const button = document.createElement('button');
        button.type = 'button'; button.className = 'chat-starter';
        button.innerHTML = '<span class="chat-starter-title"></span><span class="chat-starter-hint"></span>';
        button.querySelector('.chat-starter-title').textContent = starter.title;
        button.querySelector('.chat-starter-hint').textContent = starter.hint;
        root.append(button);
      }
      check(document.querySelector('.ui-shell'), 'existing shell renders');
      check(document.querySelector('#page-heading')?.textContent.includes('Чат с агентом'), 'chat stays primary');
      check(root.querySelectorAll('.chat-starter').length === 6, 'six starters render');
      for (const route of ['/app/', '/app/sources', '/app/scenarios', '/app/tasks', '/app/digests']) {
        check([...document.querySelectorAll('a')].some(a => a.getAttribute('href') === route), 'direct route ' + route);
      }
      const guidance = document.querySelector('.chat-window').textContent;
      for (const phrase of ['цель', 'данные и источники', 'способ обработки', 'нужный результат', 'период или расписание', 'ограничения']) {
        check(guidance.includes(phrase), 'six-group guidance: ' + phrase);
      }
      const columns = getComputedStyle(root).gridTemplateColumns.trim().split(/\s+/).length;
      check(columns === (innerWidth < 640 ? 1 : 2), 'responsive starter grid');
      document.body.insertAdjacentHTML('beforeend', '<pre id="p8t39-result"></pre>');
      document.querySelector('#p8t39-result').textContent = JSON.stringify({count: labels.length, labels, width: innerWidth});
    """
    html = html.replace("</head>", f"<style>{css}</style></head>")
    html = html.replace("</body>", f"<script>{js}</script><script>{checks}</script></body>")
    destination.write_text(html)


def main() -> int:
    if not Path(DRIVER).exists():
        raise RuntimeError("Safari WebDriver is unavailable at /usr/bin/safaridriver")
    with tempfile.TemporaryDirectory(prefix="p8-t39-safari-") as scratch:
        fixture = Path(scratch) / "chat.html"
        _render_chat(fixture)
        port = _free_port()
        process = subprocess.Popen(
            [DRIVER, "-p", str(port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        base = f"http://127.0.0.1:{port}"
        session_id = None
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    output = process.stdout.read()[-1600:] if process.stdout else ""
                    raise RuntimeError("Safari WebDriver stopped before listening: " + output)
                try:
                    _request(base, "GET", "/status", timeout=0.5)
                    break
                except (OSError, URLError):
                    time.sleep(0.2)
            else:
                raise RuntimeError("Safari WebDriver did not listen within 8 seconds")

            created = _request(
                base,
                "POST",
                "/session",
                {"capabilities": {"alwaysMatch": {"browserName": "safari"}}},
                timeout=25,
            )
            value = created.get("value", {})
            session_id = created.get("sessionId") or value.get("sessionId")
            if not session_id:
                raise RuntimeError("Safari WebDriver returned no session ID: " + json.dumps(created)[:1200])
            print("Safari WebDriver session opened without changing automation preferences.")

            for width, layout in ((1280, "desktop"), (390, "mobile")):
                base_session = f"/session/{session_id}"
                _request(base, "POST", base_session + "/window/rect", {"x": 0, "y": 0, "width": width, "height": 900})
                _request(base, "POST", base_session + "/url", {"url": fixture.as_uri()})
                time.sleep(0.25)
                executed = _request(
                    base,
                    "POST",
                    base_session + "/execute/sync",
                    {"script": "return document.querySelector('#p8t39-result')?.textContent || '';", "args": []},
                )
                result = json.loads(executed.get("value") or "{}")
                if result.get("count") != 15:
                    raise RuntimeError(f"Safari {layout} checks failed: {result!r}")
                actual_width = int(result["width"])
                if (layout == "mobile" and actual_width >= 640) or (layout == "desktop" and actual_width < 640):
                    raise RuntimeError(f"Safari {layout} viewport did not match requested width: {actual_width}px")
                print(f"PASS Safari {layout}: {result['count']} checks at viewport {result['width']}px")
            return 0
        finally:
            if session_id is None and process.poll() is None:
                try:
                    sessions = _request(base, "GET", "/sessions", timeout=1).get("value", [])
                    for session in sessions:
                        candidate = session.get("id") or session.get("sessionId")
                        if candidate:
                            _request(base, "DELETE", f"/session/{candidate}", timeout=3)
                except Exception:
                    pass
            if session_id:
                try:
                    _request(base, "DELETE", f"/session/{session_id}", timeout=5)
                    print("Safari WebDriver-owned session closed.")
                except Exception as error:  # cleanup is best effort; never close unrelated windows
                    print(f"Safari session close failed: {type(error).__name__}")
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            if process.stdout:
                diagnostic = process.stdout.read()[-1600:].strip()
                if diagnostic:
                    print("Safari WebDriver diagnostics:")
                    print(diagnostic)


def _run_applescript_desktop() -> int:
    with tempfile.TemporaryDirectory(prefix="p8-t39-safari-http-") as scratch:
        fixture = Path(scratch) / "chat.html"
        _render_chat(fixture)
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=scratch)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/chat.html"
        try:
            result = subprocess.run(
                ["osascript", "-", url],
                input=_APPLESCRIPT_DESKTOP,
                text=True,
                capture_output=True,
                timeout=20,
                check=False,
            )
            output = result.stdout + result.stderr
            print(output[-3000:].strip())
            if result.returncode:
                raise RuntimeError(f"Safari AppleScript desktop check failed with exit {result.returncode}")
            match = re.search(r"Safari desktop result: complete\|(\{[^\n]*\})", output)
            if not match:
                raise RuntimeError("Safari AppleScript did not report a completed desktop DOM result")
            browser_result = json.loads(match.group(1))
            if browser_result.get("count") != 15 or int(browser_result.get("width", 0)) < 640:
                raise RuntimeError(f"Safari desktop checks failed: {browser_result!r}")
            print(f"PASS Safari desktop: {browser_result['count']} checks at viewport {browser_result['width']}px")
            return 0
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument(
            "--applescript-desktop",
            action="store_true",
            help="Use a temporary loopback fixture and an ID-tracked Safari window for desktop checks.",
        )
        arguments = parser.parse_args()
        raise SystemExit(_run_applescript_desktop() if arguments.applescript_desktop else main())
    except TimeoutError:
        print("BLOCKED: Safari WebDriver session creation timed out; browser checks did not run.")
        raise SystemExit(2)
