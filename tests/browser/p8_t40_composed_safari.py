"""Render P8-T40 task and digest surfaces in an isolated Safari window.

The fixture uses templates and CSS from the supplied source worktree, mock
objects only, and a loopback HTTP server. It never starts the application,
connects to a database, calls providers, or submits the send form.
"""

from __future__ import annotations

import functools
import http.server
import json
import os
import re
import socket
import subprocess
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, Undefined, select_autoescape

ROOT = Path(__file__).resolve().parents[2]
SOURCE_REPO = Path(os.environ.get("P8_T40_SOURCE_REPO", "/private/tmp/social-p8-t40"))
EXPECTED_SOURCE = "22a51888"

_APPLESCRIPT = r"""on run argv
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
            tell application "Safari" to set resultText to do JavaScript "document.readyState + '|' + (document.querySelector('#p8t40-result')?.textContent || '')" in current tab of (window id ownedID)
            if resultText contains "|{" then exit repeat
        end repeat
        if not (resultText contains "|{") then error "Safari fixture did not expose its DOM result: " & resultText
        log "Safari P8-T40 result: " & resultText
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


def _render_pages(destination: Path, source_root: Path) -> None:
    env = Environment(
        loader=FileSystemLoader(source_root / "app/web/templates"),
        autoescape=select_autoescape(["html"]),
        undefined=Undefined,
    )

    def blank(*_args, **_kwargs):
        return ""

    for name in (
        "static_v", "bound_popover_select", "control", "switch_toggle", "check_card",
        "task_collection_fields", "task_retention_fields", "task_targets", "task_trigger_actions",
        "task_fields", "tenant_field", "cron_to_human", "icon", "empty_state", "empty_state_cta",
        "url_for",
    ):
        env.globals[name] = blank
    env.globals["perms_can"] = lambda *_args: True
    env.globals["JOB_TYPE_TITLES"] = {"analyze": "Анализ соцсетей"}
    env.filters["ui_label"] = blank
    env.filters["human_dt"] = blank

    perms = SimpleNamespace(can=lambda *_args: True, can_manage_workspace=lambda *_args: True)
    user = SimpleNamespace(username="fixture", role=None, has_admin_access=lambda: False)
    tenant = SimpleNamespace(id=1, name="Fixture workspace")
    common = {
        "request": SimpleNamespace(query_params={}, state=SimpleNamespace()),
        "section": "tasks", "csrf": "fixture-token", "user": user, "tenant": tenant,
        "nav": [], "mobile_nav": [], "unread_notifications": 0, "connection_alerts": [],
        "flashes": [], "plan_labels": {}, "_tz": "Europe/Kirov", "perms": perms,
        "is_superuser": False, "filter_tenant_id": None,
    }

    # New-task form: assert the real create template exposes every composed
    # control. The visible current values are represented by the edit form.
    create_html = env.get_template("web/tasks.html").render(
        **common, tasks=[], sources=[], source_groups=[], collection_defaults={}, target_fields=[],
        scenarios=[], scenario_analysis={}, prefill_source_ids=[], prefill_scenario_id=None,
        prefill_job_type="analyze", prefill_retention=False, open_add_modal=True,
        effective_active=set(), unconfirmed_tasks=set(), running_jobs={},
        job_types=[("analyze", "Анализ соцсетей"), ("collect", "Сбор данных")],
        trigger_types=[], action_types=[], cron_to_human=blank, job_id=None, tenants=[],
    )

    plan = {
        "goal": "Проверить отчёт <безопасно> & аккуратно",
        "platforms": ["vk"], "source_scope": "all", "source_ids": [],
        "collection": {"limit": 10}, "window": {"type": "rolling", "days": 7},
        "trigger": {"type": "schedule", "timezone": "Europe/Kirov"},
        "pipeline": {"stages": ["analyze", "digest"]},
        "digest": {
            "window": {"type": "fixed", "start": "2026-10-01", "end": "2026-10-28"},
            "group_by": "themes", "time_breakdown": True,
        },
    }
    task = SimpleNamespace(
        id=740, name="Fixture & composed task", job_type="analyze", cron_expr="0 9 * * *",
        next_run_at=None, agent_scenario_id=2, scenario=None, tenant=tenant, sources=[],
        source_ids=[], payload={"task_plan": plan}, is_active=True,
    )
    edit = {
        "id": task.id, "name": task.name, "job_type": "analyze", "cron_expr": task.cron_expr,
        "trigger_timezone": "Europe/Kirov", "task_goal": plan["goal"], "source_scope": "all",
        "platforms": ["vk"], "window_type": "rolling", "rolling_days": 7,
        "scenario_id": 2, "compose_digest": True, "include_collection": False,
        "digest_window_type": "fixed", "digest_rolling_days": 7,
        "digest_start_date": "2026-10-01", "digest_end_date": "2026-10-28",
        "digest_group_by": "themes", "digest_time_breakdown": True,
        "source_ids": [], "is_active": True,
    }
    edit_html = env.get_template("web/task_edit.html").render(
        **common, task=task, edit=edit, sources=[], source_groups=[], task_sources=[], target_fields=[],
        scenarios=[], scenario_analysis={}, job_types=[("analyze", "Анализ соцсетей")],
        trigger_types=[], action_types=[], can_dry_run=False,
    )

    stage = {
        "status": "complete", "counts": {"analyzed": 2}, "generated": True,
        "report_start": "2026-10-01", "report_end": "2026-10-28",
        "preview_text": "Report <script> & escaped safely",
    }
    pipeline = {"stages": {"analyze": {"status": "complete", "counts": {"analyzed": 2}}, "digest": stage}}
    job = SimpleNamespace(id=990, status="done", result={"items": 2}, created_at=None, started_at=None, finished_at=None)
    detail_html = env.get_template("web/task_detail.html").render(
        **common, task=task, task_definition=SimpleNamespace(plan=plan, plan_status="available"),
        sources=[], scenario=None, recent_jobs=[job], job_outcomes={}, pipeline_outcomes={job.id: pipeline},
        latest_run=SimpleNamespace(
            job=None, summary={}, sources=[], can_view_sources=True, run_scope=None,
            legacy_status_label=None, run_progress=None,
        ), effective_active={task.id}, running_job_id=None, running_job_status=None,
        task_sources=[], job_type_labels={}, job_type_titles={},
    )

    pages = []
    for label, html in (("create", create_html), ("edit", edit_html), ("detail", detail_html)):
        main = re.search(r'<main id="page-content"[^>]*>([\s\S]*?)</main>', html)
        if not main:
            raise RuntimeError(f"Rendered {label} template has no page content")
        body = re.sub(r"<script\b[\s\S]*?</script>", "", main.group(1), flags=re.I)
        pages.append(f'<section id="{label}">{body}</section>')

    checks = r"""
      const labels = [];
      const check = (condition, label) => { if (!condition) throw new Error(label); labels.push(label); };
      const names = id => [...document.querySelectorAll(`#${id} [name]`)].map(el => el.name);
      for (const name of ['compose_digest','include_collection','digest_window_type','digest_rolling_days','digest_start_date','digest_end_date','digest_group_by','digest_time_breakdown'])
        check(names('create').includes(name), 'create form field ' + name);
      const edit = document.querySelector('#edit [data-task-editor-form]');
      check(Boolean(edit), 'real edit form renders');
      check(edit.querySelector('[name="compose_digest"]').checked, 'edit enables composed digest');
      check(!edit.querySelector('[name="include_collection"]').checked, 'edit preserves analyze plus digest stages');
      check(edit.querySelector('[name="digest_window_type"]').value === 'fixed', 'edit preserves fixed report period');
      check(edit.querySelector('[name="digest_start_date"]').value === '2026-10-01', 'edit preserves report start date');
      check(edit.querySelector('[name="digest_end_date"]').value === '2026-10-28', 'edit preserves report end date');
      check(edit.querySelector('[name="digest_group_by"]').value === 'themes', 'edit preserves grouping');
      check(edit.querySelector('[name="digest_time_breakdown"]').checked, 'edit preserves daily breakdown');
      const summary = document.querySelector('#detail [data-task-plan-summary]');
      check(summary?.textContent.includes('analyze → digest'), 'summary displays v2 stage sequence');
      check(summary?.textContent.includes('2026-10-01 — 2026-10-28'), 'summary shows report window separately');
      check(summary?.textContent.includes('Europe/Kirov'), 'summary shows schedule timezone');
      const preview = document.querySelector('#detail details pre');
      check(preview?.textContent === 'Report <script> & escaped safely', 'preview text is HTML escaped');
      check(!preview.querySelector('script'), 'preview cannot create markup');
      const send = document.querySelector('#detail form[action="/app/tasks/740/digest/990/send"]');
      check(Boolean(send), 'separate send action renders');
      check(send?.dataset.confirm?.includes('Отправить сохранённый дайджест') && send?.dataset.confirmTitle?.includes('Подтвердить'), 'send requires explicit confirmation text');
      check(send?.querySelector('[name="confirmed"]')?.value === 'true', 'send action carries confirmation field');
      document.querySelector('#p8t40-result').textContent = JSON.stringify({count: labels.length, labels, width: innerWidth, height: innerHeight});
    """
    document = "<!doctype html><html><head><meta charset='utf-8'><title>P8-T40 Safari fixture</title></head><body>"
    document += "<h1>P8-T40 composed task fixture</h1>" + "".join(pages)
    document += '<pre id="p8t40-result"></pre><script>' + checks + "</script></body></html>"
    destination.write_text(document)


def main() -> int:
    if not SOURCE_REPO.is_dir():
        raise RuntimeError(f"source repository does not exist: {SOURCE_REPO}")
    revision = subprocess.check_output(
        ["git", "-C", str(SOURCE_REPO), "rev-parse", "--short=8", EXPECTED_SOURCE], text=True
    ).strip()
    if revision != EXPECTED_SOURCE:
        raise RuntimeError(f"expected source {EXPECTED_SOURCE}, resolved {revision}")
    print(f"Rendering candidate {revision}; mock data only.")
    with tempfile.TemporaryDirectory(prefix="p8-t40-safari-") as scratch:
        template_root = Path(scratch) / "source"
        template_root.mkdir()
        archive = subprocess.run(
            ["git", "-C", str(SOURCE_REPO), "archive", "--format=tar", EXPECTED_SOURCE, "app/web/templates"],
            check=True, capture_output=True,
        ).stdout
        import tarfile

        with tarfile.open(fileobj=__import__("io").BytesIO(archive), mode="r:") as bundle:
            bundle.extractall(template_root, filter="data")
        fixture = Path(scratch) / "fixture.html"
        _render_pages(fixture, template_root)
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=scratch)
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/fixture.html"
        try:
            result = subprocess.run(
                ["osascript", "-", url], input=_APPLESCRIPT, text=True,
                capture_output=True, timeout=25, check=False,
            )
            output = result.stdout + result.stderr
            print(output[-3000:].strip())
            if result.returncode:
                raise RuntimeError(f"Safari fixture check failed with exit {result.returncode}")
            match = re.search(r"Safari P8-T40 result: complete\|(\{[^\n]*\})", output)
            if not match:
                raise RuntimeError("Safari did not report a completed DOM result")
            browser = json.loads(match.group(1))
            if browser.get("count") != 24 or int(browser.get("width", 0)) < 640:
                raise RuntimeError(f"Safari checks failed: {browser!r}")
            print(f"PASS Safari desktop: {browser['count']} checks at {browser['width']}x{browser['height']} viewport")
            return 0
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    raise SystemExit(main())
