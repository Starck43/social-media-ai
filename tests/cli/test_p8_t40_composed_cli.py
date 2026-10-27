"""CLI plan, add, shared update and confirmed digest-send contracts."""

from __future__ import annotations

import asyncio
import json
import sys
from types import ModuleType, SimpleNamespace

from typer.testing import CliRunner

from cli import main as cli_main
from cli.commands import tasks as task_commands
from app.core.tenant_context import current_tenant_id
from app.services.task_creation import prepare_task_plan_create
from app.services.task_plans import prepare_task_plan_update, task_plan_block, parse_task_plan

__source_only__ = True


def _plan():
    return {
        "version": 2,
        "goal": "Research repair posts",
        "platforms": ["vk"],
        "source_scope": "all",
        "source_ids": [],
        "collection": {"queries": ["repair"], "limit": 30},
        "window": {"type": "rolling", "days": 2},
        "scenario_id": 19,
        "trigger": {"type": "schedule", "cron": "0 8 * * 1", "timezone": "Europe/Kirov"},
        "pipeline": {"stages": ["collect", "analyze", "digest"]},
        "digest": {
            "window": {"type": "fixed", "start": "2026-01-04", "end": "2026-01-12"},
            "group_by": "entities",
            "time_breakdown": False,
        },
    }


def _allow_operator_cli(monkeypatch):
    monkeypatch.setattr(task_commands, "_require_operator_context", lambda: None)
    monkeypatch.setattr(task_commands, "_run_task_platform", lambda coro: asyncio.run(coro))


def test_cli_plan_previews_composed_stages_and_report_dates_without_running(monkeypatch):
    _allow_operator_cli(monkeypatch)
    result = CliRunner().invoke(
        cli_main.app,
        ["task", "plan", json.dumps(_plan()), "--job-type", "analyze"],
    )
    assert result.exit_code == 0, result.output
    assert '"stages": [' in result.output
    assert '"digest"' in result.output
    assert '"start": "2026-01-04"' in result.output
    assert '"end": "2026-01-12"' in result.output
    assert "Preview only" in result.output


def test_cli_add_transports_full_v2_plan_to_creation_and_preserves_projection(monkeypatch):
    captured = {}
    _allow_operator_cli(monkeypatch)

    async def capture_add(**values):
        captured.update(values)
        return SimpleNamespace(id=10, tenant_id=21, name=values["name"], job_type=values["job_type"], is_active=False)

    monkeypatch.setattr(task_commands, "_add_task", capture_add)
    result = CliRunner().invoke(
        cli_main.app,
        [
            "task",
            "add",
            "research",
            "0 8 * * 1",
            "analyze",
            "--tenant",
            "owner",
            "--task-plan",
            json.dumps(_plan()),
        ],
    )
    assert result.exit_code == 0, result.output
    saved = captured["parsed_payload"]["task_plan"]
    assert saved["pipeline"] == _plan()["pipeline"]
    assert saved["digest"] == _plan()["digest"]
    assert captured["parsed_payload"]["collect_before_analyze"] is True


def test_cli_update_shared_plan_policy_preserves_and_replaces_v2_fields():
    raw = _plan()
    created = prepare_task_plan_create({}, raw, job_type="analyze")
    canonical = task_plan_block(parse_task_plan(raw))
    ordinary = prepare_task_plan_update(created.payload, {"payload": created.payload}, job_type="analyze")
    assert ordinary.changes["payload"]["task_plan"] == canonical

    replacement = _plan()
    replacement["digest"]["window"] = {"type": "rolling", "days": 15}
    replaced = prepare_task_plan_update(created.payload, {"task_plan": replacement}, job_type="analyze")
    assert replaced.changes["payload"]["task_plan"]["pipeline"] == replacement["pipeline"]
    assert replaced.changes["payload"]["task_plan"]["digest"]["window"] == replacement["digest"]["window"]


def test_cli_digest_send_requires_confirm_and_scopes_confirmed_send_to_explicit_tenant(monkeypatch):
    calls = []
    _allow_operator_cli(monkeypatch)

    async def resolve_tenant(_tenant):
        return 73

    monkeypatch.setattr(task_commands, "_require_explicit_tenant", resolve_tenant)
    module = ModuleType("app.services.task_digest_delivery")

    async def send_task_digest(task_id, job_id):
        calls.append((task_id, job_id, current_tenant_id()))
        return {"status": "sent", "sent_parts": 1, "total_parts": 1}

    module.send_task_digest = send_task_digest
    monkeypatch.setitem(sys.modules, "app.services.task_digest_delivery", module)
    runner = CliRunner()
    base_args = ["task", "digest-send", "31", "--job-id", "42", "--tenant", "owner"]
    unconfirmed = runner.invoke(cli_main.app, base_args)
    assert unconfirmed.exit_code == task_commands.EXIT_USAGE
    assert calls == []

    confirmed = runner.invoke(cli_main.app, [*base_args, "--confirm"])
    assert confirmed.exit_code == 0, confirmed.output
    assert calls == [(31, 42, 73)]
