"""Run real chat workspace interactions in an ID-tracked Safari window.

The fixture uses mocked fetch responses and a temporary loopback HTTP server.
It starts no application runtime or database and changes no Safari preferences.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_BUILDER = ROOT / "tests/browser/p8_t42_chat_workspace.cjs"
EXPECTED = "PASS 24 checks"

_APPLESCRIPT = r"""on run argv
    set targetURL to item 1 of argv
    set ownedID to missing value
    set outputText to ""
    tell application "Safari" to set beforeIDs to id of every window
    try
        tell application "Safari" to make new document with properties {URL:targetURL}
        delay 1
        tell application "Safari" to set afterIDs to id of every window
        set addedIDs to {}
        repeat with candidateID in afterIDs
            if beforeIDs does not contain (contents of candidateID) then set end of addedIDs to contents of candidateID
        end repeat
        if (count of addedIDs) is not 1 then error "Could not identify exactly one Safari-owned window"
        set ownedID to item 1 of addedIDs
        tell application "Safari" to set index of (window id ownedID) to 1
        tell application "Safari" to activate
        delay 0.5

        set requestedWidths to {1600, 500}
        repeat with requestedWidth in requestedWidths
            set targetWidth to contents of requestedWidth
            tell application "Safari" to set bounds of (window id ownedID) to {0, 0, targetWidth, 1000}
            delay 0.3
            tell application "Safari" to do JavaScript "window.location.reload()" in current tab of (window id ownedID)
            set resultText to ""
            repeat 80 times
                delay 0.25
                tell application "Safari" to set resultText to do JavaScript "JSON.stringify({state: document.readyState, result: document.querySelector('#p8t42-results')?.textContent || '', width: window.innerWidth})" in current tab of (window id ownedID)
                if resultText contains "FAIL " then error "T42 browser interaction failed: " & resultText
                if resultText contains "PASS 24 checks" then exit repeat
            end repeat
            if not (resultText contains "PASS 24 checks") then error "T42 checks did not finish: " & resultText
            set outputText to outputText & "P8T42_VIEWPORT:" & resultText & linefeed
        end repeat
    on error errorMessage number errorNumber
        if ownedID is not missing value then try
            tell application "Safari" to close (window id ownedID)
        end try
        error errorMessage number errorNumber
    end try
    if ownedID is not missing value then tell application "Safari" to close (window id ownedID)
    return outputText
end run"""


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *_args) -> None:
        pass


def _build_fixture(destination: Path) -> None:
    command = ["node", str(FIXTURE_BUILDER), "--fixture-only", str(destination)]
    result = subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, "PYTHON": sys.executable},
        text=True,
        capture_output=True,
        timeout=45,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("T42 browser fixture build failed: " + (result.stderr or result.stdout)[-1600:])
    if not destination.is_file():
        raise RuntimeError("T42 browser fixture builder returned without the fixture")


def _run_applescript(fixture_url: str) -> list[dict]:
    if shutil.which("osascript") is None:
        raise RuntimeError("AppleScript runner `osascript` is unavailable")
    completed = subprocess.run(
        ["osascript", "-", fixture_url],
        input=_APPLESCRIPT,
        text=True,
        capture_output=True,
        timeout=55,
        check=False,
    )
    output = completed.stdout + completed.stderr
    if completed.returncode:
        raise RuntimeError("Safari AppleScript failed: " + output[-2400:].strip())

    results = []
    for line in completed.stdout.splitlines():
        marker = "P8T42_VIEWPORT:"
        if line.startswith(marker):
            try:
                results.append(json.loads(line[len(marker) :]))
            except json.JSONDecodeError as error:
                raise RuntimeError("Safari returned invalid viewport evidence: " + line[:1200]) from error
    if len(results) != 2:
        raise RuntimeError("Safari did not return both viewport results: " + output[-1800:].strip())
    for result, layout in zip(results, ("desktop", "narrow"), strict=True):
        width = int(result.get("width", 0))
        if result.get("result") != EXPECTED:
            raise RuntimeError(f"Safari {layout} interaction checks failed: {result!r}")
        if layout == "desktop" and width < 1200:
            raise RuntimeError(f"Safari desktop viewport measured only {width}px")
        if layout == "narrow" and width >= 640:
            raise RuntimeError(f"Safari narrow viewport measured {width}px; expected below 640px")
    return results


def main() -> int:
    if shutil.which("Safari") is None and not Path("/Applications/Safari.app").exists():
        raise RuntimeError("Safari is unavailable")

    with tempfile.TemporaryDirectory(prefix="p8-t42-safari-") as temporary:
        scratch = Path(temporary)
        fixture = scratch / "chat.html"
        _build_fixture(fixture)

        handler = lambda *args, **kwargs: _QuietHandler(*args, directory=str(scratch), **kwargs)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        fixture_url = f"http://127.0.0.1:{server.server_port}/chat.html"
        try:
            desktop, narrow = _run_applescript(fixture_url)
            print(f"PASS Safari desktop: 24 checks at measured viewport {desktop['width']}px")
            print(f"PASS Safari narrow: 24 checks at measured viewport {narrow['width']}px")
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
            help="Run the fixture in an ID-tracked Safari window at wide and narrow widths (default).",
        )
        parser.parse_args()
        raise SystemExit(main())
    except Exception as error:
        print(f"FAIL {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1)
