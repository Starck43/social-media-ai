"""Exercise the P11-T29 preview/confirmation UI in a real Safari window.

The live PostgreSQL acceptance test owns the loopback ASGI server and synthetic
users/workspaces. This helper only owns the Safari window it opens.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote

_SCRIPT = r"""on run argv
    set landingURL to item 1 of argv
    set setupURL to item 2 of argv
    set workspaceID to item 3 of argv
    set isProtected to item 4 of argv
    set loginResult to ""
    set windowID to missing value
    tell application "Safari" to set beforeIDs to id of every window
    try
        tell application "Safari" to make new document with properties {URL:landingURL}
        delay 1
        tell application "Safari" to set afterIDs to id of every window
        set addedIDs to {}
        repeat with candidateID in afterIDs
            if beforeIDs does not contain (contents of candidateID) then set end of addedIDs to contents of candidateID
        end repeat
        if (count of addedIDs) is not 1 then error "Could not identify exactly one Safari-owned window"
        set windowID to item 1 of addedIDs
        tell application "Safari" to set index of (window id windowID) to 1
        tell application "Safari" to activate
        tell application "Safari" to set loginResult to do JavaScript "(()=>{const q=new URLSearchParams(location.hash.slice(1));const f=document.querySelector('form');if(!f)return 'LOGIN_FORM_MISSING';f.querySelector('[name=username]').value=q.get('u')||'';f.querySelector('[name=password]').value=q.get('p')||'';f.requestSubmit();return 'LOGIN_SUBMITTED'})()" in current tab of (window id windowID)
        if loginResult is not "LOGIN_SUBMITTED" then error "Synthetic superuser login form failed: " & loginResult
        set currentPath to ""
        repeat 60 times
            delay 0.25
            tell application "Safari" to set currentPath to do JavaScript "location.pathname" in current tab of (window id windowID)
            if currentPath contains "/admin" and currentPath does not contain "/admin/login" then exit repeat
        end repeat
        if currentPath contains "/admin/login" then error "Synthetic superuser login did not reach the console"
        tell application "Safari" to set URL of current tab of (window id windowID) to setupURL
        delay 0.6
        tell application "Safari" to set loginResult to do JavaScript "(()=>{const f=document.querySelector('form');const reason=f?.querySelector('[name=reason_code]');const policy=f?.querySelector('[name=retention_policy]');if(!f||!reason||!policy)return 'PREVIEW_FORM_MISSING';reason.value='owner_request';policy.value='preserve';f.querySelector('button[type=submit]')?.click();return 'PREVIEW_CLICKED'})()" in current tab of (window id windowID)
        if loginResult is not "PREVIEW_CLICKED" then error "Safari could not submit the preview form: " & loginResult
        set resultText to ""
        set pageState to ""
        repeat 100 times
            delay 0.25
            tell application "Safari" to set pageState to do JavaScript "(()=>{if(document.querySelector('[data-offboarding-receipt]'))return 'RECEIPT';if(document.querySelector('[name=review_token]'))return 'REVIEW';if(!document.querySelector('[name=intent][value=preview]')&&document.querySelector('[data-retained-count]'))return 'BLOCKED';return 'WAIT'})()" in current tab of (window id windowID)
            if pageState is not "WAIT" then exit repeat
        end repeat
        if isProtected is "true" then
            if pageState is not "BLOCKED" then error "Safari did not render the retained-audit blocker: " & pageState
            tell application "Safari" to set resultText to do JavaScript "JSON.stringify({setup:true,preview:true,hasReview:!!document.querySelector('[name=review_token]'),previewHasBlocker:!!document.querySelector('[data-retained-count]')&&/срок хранения/i.test(document.body.innerText),confirmation:null,receipt:false,protected:true})" in current tab of (window id windowID)
        else
            if pageState is not "REVIEW" then error "Safari did not render the review confirmation form: " & pageState
            tell application "Safari" to set loginResult to do JavaScript "(()=>{const f=document.querySelector('form');const c=f?.querySelector('[name=confirmed]');const b=f?.querySelector('button[type=submit]');if(!f||!c||!b)return 'CONFIRM_FORM_MISSING';c.checked=true;b.click();return 'CONFIRM_CLICKED'})()" in current tab of (window id windowID)
            if loginResult is not "CONFIRM_CLICKED" then error "Safari could not submit the confirmation form: " & loginResult
            set pageState to ""
            repeat 100 times
                delay 0.25
                tell application "Safari" to set pageState to do JavaScript "document.querySelector('[data-offboarding-receipt]')?'RECEIPT':'WAIT'" in current tab of (window id windowID)
                if pageState is "RECEIPT" then exit repeat
            end repeat
            if pageState is not "RECEIPT" then error "Safari did not render the deletion receipt"
            tell application "Safari" to set resultText to do JavaScript "JSON.stringify({setup:true,preview:true,hasReview:true,previewHasBlocker:false,confirmation:'clicked',receipt:!!document.querySelector('[data-offboarding-receipt]'),protected:false})" in current tab of (window id windowID)
        end if
    on error errorMessage number errorNumber
        if windowID is not missing value then try
            tell application "Safari" to close (window id windowID)
        end try
        error errorMessage number errorNumber
    end try
    if windowID is not missing value then tell application "Safari" to close (window id windowID)
    return resultText
end run"""


def _login_url(base_url: str, username: str, password: str) -> str:
    return f"{base_url}/admin/login#u={quote(username)}&p={quote(password)}"


def run_safari_offboarding(
    base_url: str,
    username: str,
    password: str,
    workspace_id: int,
    *,
    protected: bool = False,
) -> dict:
    """Preview and, for an eligible workspace, confirm the action in Safari."""
    if shutil.which("osascript") is None or not Path("/Applications/Safari.app").exists():
        raise RuntimeError("Safari and AppleScript are required for P11-T29 browser acceptance")
    result = subprocess.run(
        [
            "osascript",
            "-",
            _login_url(base_url, username, password),
            f"{base_url}/admin/tenant/action/offboard?pks={workspace_id}",
            str(workspace_id),
            "true" if protected else "false",
        ],
        input=_SCRIPT,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            "Safari workspace offboarding walkthrough failed: " + (result.stderr or result.stdout)[-1800:]
        )
    try:
        payload = json.loads(result.stdout.strip())
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Safari did not return its offboarding result: " + result.stdout[-1200:]) from exc
    assert payload.get("setup") is True, payload
    assert payload.get("preview") is True, payload
    if protected:
        assert payload.get("previewHasBlocker") is True, payload
        assert payload.get("hasReview") is False, payload
        assert payload.get("confirmation") is None, payload
        assert payload.get("receipt") is False, payload
    else:
        assert payload.get("hasReview") is True, payload
        assert payload.get("confirmation") == "clicked", payload
        assert payload.get("receipt") is True, payload
    print("PASS Safari P11-T29 preview/confirmation" if not protected else "PASS Safari P11-T29 retained-audit blocker")
    return payload


if __name__ == "__main__":
    raise SystemExit("This helper is invoked by tests/admin/test_p11_t29_offboarding_postgres.py")
