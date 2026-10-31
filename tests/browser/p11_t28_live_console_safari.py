"""Run a real SQLAdmin login/delete walkthrough in Safari.

The caller supplies a temporary loopback application URL and synthetic fixture
accounts/workspace IDs. Only the Safari window this script opens is closed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

_APPLESCRIPT = r"""on run argv
    set adminURL to item 1 of argv
    set ownerURL to item 2 of argv
    set workspaceURL to item 3 of argv
    set adminWorkspace to item 4 of argv
    set ownerWorkspace to item 5 of argv
    set auditWorkspace to item 6 of argv
    set beforeIDs to {}
    set ownedID to missing value
    set outputText to ""
    tell application "Safari" to set beforeIDs to id of every window
    try
        tell application "Safari" to make new document with properties {URL:adminURL}
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

        tell application "Safari" to set loginResult to do JavaScript "(()=>{const q=new URLSearchParams(location.hash.slice(1));const f=document.querySelector('form');if(!f)return 'LOGIN_FORM_MISSING';f.querySelector('[name=username]').value=q.get('u')||'';f.querySelector('[name=password]').value=q.get('p')||'';f.requestSubmit();return 'LOGIN_SUBMITTED'})()" in current tab of (window id ownedID)
        if loginResult is not "LOGIN_SUBMITTED" then error "Synthetic admin login form failed: " & loginResult
        set loginPath to ""
        repeat 60 times
            delay 0.25
            tell application "Safari" to set loginPath to do JavaScript "location.pathname" in current tab of (window id ownedID)
            if loginPath contains "/admin" and loginPath does not contain "/admin/login" then exit repeat
        end repeat
        if loginPath contains "/admin/login" then error "Synthetic admin login did not reach the console"

        tell application "Safari" to set URL of current tab of (window id ownedID) to workspaceURL
        delay 0.6
        tell application "Safari" to do JavaScript "(async()=>{try{const id=" & adminWorkspace & ";const token=document.querySelector('meta[name=\"csrf-token\"]')?.content||'';const detail=await fetch('/admin/tenant/details/'+id);const response=await fetch('/admin/tenant/delete?pks='+id,{method:'DELETE',headers:{'X-CSRF-Token':token}});window.__p11t28_admin={details:detail.status,status:response.status,text:await response.text()}}catch(e){window.__p11t28_admin={error:String(e)}}})()" in current tab of (window id ownedID)
        set adminResult to ""
        repeat 80 times
            delay 0.25
            tell application "Safari" to set adminResult to do JavaScript "JSON.stringify(window.__p11t28_admin||{})" in current tab of (window id ownedID)
            if adminResult contains "status" or adminResult contains "error" then exit repeat
        end repeat
        if adminResult does not contain "status" then error "Safari admin workspace check did not finish: " & adminResult

        tell application "Safari" to set URL of current tab of (window id ownedID) to (item 7 of argv)
        delay 0.6
        tell application "Safari" to set URL of current tab of (window id ownedID) to ownerURL
        delay 1
        tell application "Safari" to do JavaScript "(()=>{const q=new URLSearchParams(location.hash.slice(1));const f=document.querySelector('form');if(!f)return 'LOGIN_FORM_MISSING';f.querySelector('[name=username]').value=q.get('u')||'';f.querySelector('[name=password]').value=q.get('p')||'';f.requestSubmit();return 'LOGIN_SUBMITTED'})()" in current tab of (window id ownedID)
        set loginPath to ""
        repeat 60 times
            delay 0.25
            tell application "Safari" to set loginPath to do JavaScript "location.pathname" in current tab of (window id ownedID)
            if loginPath contains "/admin" and loginPath does not contain "/admin/login" then exit repeat
        end repeat
        if loginPath contains "/admin/login" then error "Synthetic owner login did not reach the console"

        tell application "Safari" to set URL of current tab of (window id ownedID) to workspaceURL
        delay 0.6
        tell application "Safari" to do JavaScript "(async()=>{try{const token=document.querySelector('meta[name=\"csrf-token\"]')?.content||'';const remove=async id=>{const r=await fetch('/admin/tenant/delete?pks='+id,{method:'DELETE',headers:{'X-CSRF-Token':token}});return {status:r.status,text:await r.text()}};const allowed=await remove(" & ownerWorkspace & ");const protectedRow=await remove(" & auditWorkspace & ");window.__p11t28_owner={allowed,protectedRow}}catch(e){window.__p11t28_owner={error:String(e)}}})()" in current tab of (window id ownedID)
        set outputText to ""
        repeat 80 times
            delay 0.25
            tell application "Safari" to set outputText to do JavaScript "JSON.stringify(window.__p11t28_owner||{})" in current tab of (window id ownedID)
            if outputText contains "allowed" or outputText contains "error" then exit repeat
        end repeat
        if outputText does not contain "allowed" then error "Safari owner workspace check did not finish: " & outputText
        -- Return both phases in one payload. A cross-page channel (localStorage)
        -- is not reliable here: the owner phase navigates through logout and a
        -- fresh login, and the stored admin result was observed arriving empty.
        return adminResult & linefeed & "__P11T28_PHASE_SPLIT__" & linefeed & outputText
    on error errorMessage number errorNumber
        if ownedID is not missing value then try
            tell application "Safari" to close (window id ownedID)
        end try
        error errorMessage number errorNumber
    end try
    if ownedID is not missing value then tell application "Safari" to close (window id ownedID)
    return outputText
end run"""


def _login_url(base_url: str, username: str, password: str) -> str:
    from urllib.parse import quote

    return f"{base_url}/admin/login#u={quote(username)}&p={quote(password)}"


def run_safari_console(
    base_url: str, admin: str, owner: str, password: str, admin_tenant: int, owner_tenant: int, audit_tenant: int
) -> dict:
    from pathlib import Path

    if shutil.which("osascript") is None or (
        shutil.which("Safari") is None and not Path("/Applications/Safari.app").exists()
    ):
        raise RuntimeError("Safari and AppleScript are required for live console acceptance")
    args = [
        _login_url(base_url, admin, password),
        _login_url(base_url, owner, password),
        f"{base_url}/admin/tenant/list",
        str(admin_tenant),
        str(owner_tenant),
        str(audit_tenant),
        f"{base_url}/admin/logout",
    ]
    result = subprocess.run(
        ["osascript", "-", *args],
        input=_APPLESCRIPT,
        text=True,
        capture_output=True,
        timeout=75,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("Safari live SQLAdmin walkthrough failed: " + (result.stderr or result.stdout)[-1800:])
    admin_raw, _, owner_raw = result.stdout.strip().partition("\n__P11T28_PHASE_SPLIT__\n")
    if not owner_raw:
        raise RuntimeError("Safari did not return both SQLAdmin phases: " + result.stdout[-1200:])
    try:
        admin_result = json.loads(admin_raw.strip())
        owner_result = json.loads(owner_raw.strip())
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Safari did not return its SQLAdmin result objects: " + result.stdout[-1200:]) from exc
    payload = {**owner_result, "admin": admin_result}
    assert admin_result.get("details") == 200, payload
    assert admin_result.get("status") == 403, payload
    assert owner_result.get("allowed", {}).get("status") == 200, payload
    assert "error=" not in owner_result.get("allowed", {}).get("text", ""), payload
    assert owner_result.get("protectedRow", {}).get("status", 500) < 500, payload
    assert "error=" in owner_result.get("protectedRow", {}).get("text", ""), payload
    print(
        "PASS Safari real console: fully granted admin 403; owner deletes empty workspace; retained audit protects workspace"
    )
    return payload


if __name__ == "__main__":
    # The acceptance pytest imports this helper with a fixture-owned live server.
    raise SystemExit("This helper is invoked from the owner-local acceptance test")
