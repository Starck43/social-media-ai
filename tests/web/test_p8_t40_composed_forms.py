"""Focused web contracts for composed task editing and safe stage results."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.services import task_results
from app.web import tasks

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "app/web/templates"
__source_only__ = True


def _task(payload):
    return SimpleNamespace(
        id=2,
        name="research",
        job_type="analyze",
        cron_expr="0 9 * * *",
        payload=payload,
        agent_scenario_id=4,
        trigger_config={},
        trigger_type=None,
        action_type=None,
        rate_limit_per_hour=None,
        cooldown_seconds=None,
        dry_run=False,
    )


def _form_plan(*, digest_window, include_collection=True):
    return tasks._task_plan_from_form(
        "Research local posts",
        ["vk"],
        "all",
        [],
        4,
        "analyze",
        "0 9 * * *",
        {"collection": {"queries": ["repair"]}},
        "rolling",
        3,
        "Europe/Kirov",
        compose_digest=True,
        digest_window_type=digest_window["type"],
        digest_rolling_days=digest_window.get("days", 7),
        digest_start_date=digest_window.get("start", ""),
        digest_end_date=digest_window.get("end", ""),
        digest_group_by="topic_chains",
        digest_time_breakdown=True,
        include_collection=include_collection,
    )


def test_composed_editor_keeps_collection_and_fixed_report_windows_independent():
    plan = _form_plan(
        digest_window={"type": "fixed", "start": "2026-01-03", "end": "2026-01-08"},
    )
    assert plan["version"] == 2
    assert plan["window"] == {"type": "rolling", "days": 3}
    assert plan["pipeline"]["stages"] == ["collect", "analyze", "digest"]
    assert plan["digest"] == {
        "window": {"type": "fixed", "start": "2026-01-03", "end": "2026-01-08"},
        "group_by": "topic_chains",
        "time_breakdown": True,
    }
    assert plan["trigger"]["timezone"] == "Europe/Kirov"


def test_editor_preserves_analyze_digest_pipeline_and_rolling_report_on_edit(monkeypatch):
    original = _form_plan(
        digest_window={"type": "rolling", "days": 11},
        include_collection=False,
    )
    monkeypatch.setattr(
        tasks,
        "_collection_edit",
        lambda _payload: {"start_date": "", "end_date": ""},
    )
    edit = tasks._edit_payload(
        _task({"task_plan": original}),
        set(),
        [],
    )
    assert edit["compose_digest"] is True
    assert edit["include_collection"] is False
    assert edit["digest_window_type"] == "rolling"
    assert edit["digest_rolling_days"] == 11
    assert edit["digest_group_by"] == "topic_chains"
    assert edit["digest_time_breakdown"] is True

    roundtrip = tasks._task_plan_from_form(
        edit["task_goal"],
        edit["platforms"],
        edit["source_scope"],
        edit["source_ids"],
        edit["scenario_id"],
        "analyze",
        edit["cron_expr"],
        {"collection": {"queries": ["repair"]}, "task_plan": original},
        edit["window_type"],
        edit["rolling_days"],
        edit["trigger_timezone"],
        compose_digest=edit["compose_digest"],
        digest_window_type=edit["digest_window_type"],
        digest_rolling_days=edit["digest_rolling_days"],
        digest_group_by=edit["digest_group_by"],
        digest_time_breakdown=edit["digest_time_breakdown"],
        include_collection=edit["include_collection"],
    )
    assert roundtrip["pipeline"] == {"stages": ["analyze", "digest"]}
    assert roundtrip["digest"] == original["digest"]


def test_composed_editor_rejects_out_of_bounds_rolling_report_window():
    with pytest.raises(ValueError):
        _form_plan(digest_window={"type": "rolling", "days": 0})


def test_plan_summary_names_the_separate_report_window_and_never_promises_delivery():
    environment = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(("html", "htm", "xml")),
    )
    html = environment.get_template("web/_task_plan_summary.html").render(
        task=SimpleNamespace(name="Research", job_type="analyze", cron_expr="0 9 * * *"),
        task_definition={
            "plan_status": "available",
            "plan": {
                "goal": "Research",
                "platforms": ["vk"],
                "source_scope": "all",
                "source_ids": [],
                "collection": {"queries": ["repair"], "limit": 20},
                "window": {"type": "rolling", "days": 3},
                "trigger": {"type": "schedule", "timezone": "Europe/Kirov"},
                "pipeline": {"stages": ["collect", "analyze", "digest"]},
                "digest": {
                    "window": {"type": "fixed", "start": "2026-01-03", "end": "2026-01-08"},
                    "group_by": "topic_chains",
                    "time_breakdown": True,
                },
            },
        },
        sources=[],
        scenario=SimpleNamespace(name="Research scenario"),
        collection={},
        cron_to_human=lambda _cron: "каждый день в 9:00",
    )
    assert "Последние 3 дн." in html
    assert "Окно дайджеста" in html
    assert "2026-01-03 — 2026-01-08" in html
    assert "collect → analyze → digest" in html
    assert "формируется без отправки" in html


def test_pipeline_result_projection_preserves_partial_counts_and_redacts_raw_errors():
    raw = {
        "pipeline": {
            "version": 1,
            "stages": {
                "collect": {
                    "state": "completed",
                    "result": {"status": "partial", "counts": {"collected": 7}, "error": "provider secret"},
                },
                "analyze": {"state": "completed", "result": {"status": "completed", "counts": {"analyzed": 5}}},
                "digest": {
                    "state": "completed",
                    "result": {
                        "status": "skipped",
                        "generated": False,
                        "preview_text": "must stay hidden",
                        "report_start": "2026-01-03",
                        "report_end": "2026-01-08",
                    },
                },
            },
        }
    }
    projected = task_results._pipeline_outcome(raw, can_view_preview=False)
    assert projected["stages"]["collect"] == {"status": "partial", "counts": {"collected": 7}}
    assert projected["stages"]["analyze"]["counts"] == {"analyzed": 5}
    digest = projected["stages"]["digest"]
    assert digest["status"] == "skipped"
    assert digest["generated"] is not True
    assert "preview_text" not in digest
    assert "provider secret" not in repr(projected)


async def test_pipeline_reader_resolves_only_scoped_delivery_ledger_for_preview_right(monkeypatch):
    calls = []
    module = ModuleType("app.services.task_digest_delivery")

    async def read_delivery(reference):
        calls.append(reference)
        return {
            "status": "partial",
            "sent_parts": 1,
            "total_parts": 2,
            "target": "must-not-escape",
        }

    module.read_task_digest_delivery = read_delivery
    monkeypatch.setitem(sys.modules, "app.services.task_digest_delivery", module)
    raw = {
        "pipeline": {
            "stages": {
                "digest": {
                    "state": "completed",
                    "result": {"generated": True, "text": "report body", "preview_text": "report body"},
                }
            }
        },
        "task_digest_delivery": {"run_id": 23, "generation": "generation-1", "target": "untrusted"},
    }

    hidden = await task_results.task_pipeline_outcome(raw, can_view_preview=False)
    assert calls == []
    assert "preview_text" not in hidden["stages"]["digest"]
    visible = await task_results.task_pipeline_outcome(raw, can_view_preview=True)
    assert calls == [{"run_id": 23, "generation": "generation-1"}]
    assert visible["stages"]["digest"]["preview_text"] == "report body"
    assert visible["stages"]["digest"]["delivery"] == {
        "status": "partial",
        "sent_parts": 1,
        "total_parts": 2,
    }
    assert "target" not in repr(visible)


async def test_web_digest_send_requires_explicit_confirmation_and_create_permission(monkeypatch):
    calls = []
    module = ModuleType("app.services.task_digest_delivery")

    async def send_task_digest(*, task_id, job_id):
        calls.append((task_id, job_id))
        return {"status": "sent"}

    module.send_task_digest = send_task_digest
    monkeypatch.setitem(sys.modules, "app.services.task_digest_delivery", module)
    monkeypatch.setattr(tasks, "ensure_csrf", lambda *_args: True)
    monkeypatch.setattr(tasks, "add_flash", lambda *_args: None)
    permission_checks = []

    def allow(request, model, action, *, back, **_kwargs):
        permission_checks.append((model, action, back))
        return None

    monkeypatch.setattr(tasks, "guard_web", allow)
    request = SimpleNamespace(state=SimpleNamespace(tenant_id=77))

    refused = await tasks.task_send_generated_digest(request, task_id=31, job_id=42, confirmed="", token="csrf")
    assert refused.status_code == 302
    assert calls == []

    response = await tasks.task_send_generated_digest(request, task_id=31, job_id=42, confirmed="true", token="csrf")
    assert response.status_code == 302
    assert calls == [(31, 42)]
    assert permission_checks == [
        ("digestrun", "create", "/app/tasks/31"),
        ("digestrun", "create", "/app/tasks/31"),
    ]

    from fastapi.responses import RedirectResponse

    monkeypatch.setattr(
        tasks,
        "guard_web",
        lambda *_args, **_kwargs: RedirectResponse("/app/tasks/31", status_code=302),
    )
    denied = await tasks.task_send_generated_digest(request, task_id=31, job_id=43, confirmed="true", token="csrf")
    assert denied.status_code == 302
    assert calls == [(31, 42)]
