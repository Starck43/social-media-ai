"""Safely exercise compact model probes in Safari without an app or provider call."""

from __future__ import annotations

import json
import mimetypes
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[2]
EXPECTED = re.compile(r"PASS (?P<count>\d+) checks")

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
        delay 0.4
        repeat with targetWidth in {1440, 390}
            set widthValue to contents of targetWidth
            tell application "Safari" to set bounds of (window id ownedID) to {0, 0, widthValue, 920}
            delay 0.5
            tell application "Safari" to do JavaScript "window.location.reload()" in current tab of (window id ownedID)
            set resultText to ""
            repeat 80 times
                delay 0.25
                tell application "Safari" to set resultText to do JavaScript "JSON.stringify(window.__p8t45 || {})" in current tab of (window id ownedID)
                if resultText contains "PASS " and resultText contains " checks" then exit repeat
                if resultText contains "FAIL " then error "P8T45 Safari interaction failure: " & resultText
            end repeat
            if not (resultText contains "PASS " and resultText contains " checks") then error "P8T45 Safari check did not finish: " & resultText
            set outputText to outputText & "P8T45_VIEWPORT:" & resultText & linefeed
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

_JS = r"""try {
const checks=[]; const check=(ok,name)=>{if(!ok)throw Error(name);checks.push(name)};
const rows=[...document.querySelectorAll('.llm-model-entry')];
check(rows.length===2,'two catalog models rendered');
check([...document.styleSheets].some(sheet=>sheet.href?.includes('/static/css/settings-llm.css')),'settings model stylesheet loaded');
const first=rows[0], local=first.querySelector('form input[name=mode][value=local]')?.form;
check(!!local,'local probe form exists');
check(local.querySelector('input[name=_csrf]')?.value==='fixture-csrf','local probe carries CSRF');
check(local.querySelector('input[name=model_id]')?.value==='31','local probe binds selected model');
check(first.textContent.includes('4 успешно · 2 с ошибкой'),'compact success and failure counters visible');
check(first.textContent.includes('Ограничение квоты из fixture'),'current provider warning visible');
const realDetails=first.querySelector('details.llm-real-probe');
check(!!realDetails,'real probe is tucked behind compact disclosure');
const real=realDetails.querySelector('form');
check(real.querySelector('input[name=consent][value=yes]') && !real.querySelector('input[name=consent]').checked,'real probe requires deliberate consent');
check(real.querySelector('input[name=tenant_id]')?.value==='7','real probe binds active workspace');
const submitted=[];
for(const form of document.querySelectorAll('form[action="/app/settings/models/probe"]'))
 form.addEventListener('submit',e=>{e.preventDefault();submitted.push(new FormData(form).get('mode'))});
check(submitted.length===0,'page load sends no test requests');
local.querySelector('button').click();
check(submitted.length===1 && submitted[0]==='local','local check runs only after click');
realDetails.querySelector('summary').click();
check(realDetails.open,'real-test disclosure opens on request');
real.querySelector('input[name=consent]').checked=true;
real.querySelector('button').click();
check(submitted.length===2 && submitted[1]==='real','consented real check submits only after click');
check(new FormData(real).get('consent')==='yes','real probe carries explicit consent');
check(document.documentElement.scrollWidth<=innerWidth+1,'settings layout has no horizontal overflow');
window.__p8t45={result:'PASS '+checks.length+' checks',checks,width:innerWidth};
} catch(error) { const offenders=[...document.querySelectorAll('body *')].map(el=>({el,box:el.getBoundingClientRect()})).filter(x=>x.box.right>innerWidth+1).slice(0,8).map(x=>({tag:x.el.tagName,cls:String(x.el.className||''),parent:x.el.parentElement?.className,right:Math.round(x.box.right),width:Math.round(x.box.width),wrap:getComputedStyle(x.el).overflowWrap,text:x.el.textContent.slice(0,80)})); window.__p8t45={result:'FAIL '+error.message+' '+JSON.stringify(offenders),checks:[],width:innerWidth}; }"""


class _FixtureHandler(SimpleHTTPRequestHandler):
    fixture_html = ""

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/settings.html":
            body = self.fixture_html.encode()
            mime = "text/html; charset=utf-8"
        elif parsed.path.startswith("/static/"):
            relative = unquote(parsed.path.removeprefix("/static/"))
            path = (ROOT / "app/static" / relative).resolve()
            if not path.is_relative_to(ROOT / "app/static") or not path.is_file():
                self.send_error(404)
                return
            body = path.read_bytes()
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        pass


def _fixture_html() -> str:
    import runpy

    fixtures = runpy.run_path(str(ROOT / "tests/shared/settings_llm_fixtures.py"))
    context = fixtures["catalog_context"]("catalog")
    models = context["llm_catalog"]["providers"][0]["models"]
    models[0].update(use_count=6, fail_count=2, success_count=4, has_recent_error=True)
    context["model_probe_result"] = {
        "owner_user_id": 2,
        "model_id": 31,
        "mode": "real",
        "ok": False,
        "text": "Реальный запрос не выполнен",
        "limit_hint": {"code": "quota", "message": "Ограничение квоты из fixture"},
    }
    html = fixtures["BASE"]["environment"]().get_template("web/settings.html").render(**context)
    html = re.sub(r'<script[^>]+src="https://[^"]+"[^>]*></script>', "", html)
    html = html.replace("http://fixture.test/static/", "/static/")
    html = html.replace("</body>", "<script>" + _JS + "</script></body>")
    return html


def main() -> int:
    if shutil.which("osascript") is None or (
        shutil.which("Safari") is None and not Path("/Applications/Safari.app").exists()
    ):
        raise RuntimeError("Safari and AppleScript are required")
    with tempfile.TemporaryDirectory(prefix="p8-t45-safari-"):
        handler = type("FixtureHandler", (_FixtureHandler,), {"fixture_html": _fixture_html()})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/settings.html"
            completed = subprocess.run(
                ["osascript", "-", url], input=_APPLESCRIPT, text=True, capture_output=True, timeout=55, check=False
            )
            output = completed.stdout + completed.stderr
            if completed.returncode:
                raise RuntimeError("Safari settings test failed: " + output[-2200:])
            results = [
                json.loads(line.split("P8T45_VIEWPORT:", 1)[1])
                for line in completed.stdout.splitlines()
                if line.startswith("P8T45_VIEWPORT:")
            ]
            if len(results) != 2:
                raise RuntimeError("Safari did not return both viewport results: " + output[-1500:])
            for result in results:
                match = EXPECTED.fullmatch(result.get("result", ""))
                if not match or int(match.group("count")) != 16:
                    raise RuntimeError(f"Safari checks did not pass: {result!r}")
            print(f"PASS Safari desktop: {results[0]['result']} at {results[0]['width']}px")
            print(f"PASS Safari mobile: {results[1]['result']} at {results[1]['width']}px")
            return 0
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"FAIL {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1)
