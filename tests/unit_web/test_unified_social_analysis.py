from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.models import AgentScenario
from app.web import tasks as tasks_web
from app.web.tasks import (
    _normalize_web_job_type,
    _payload_from_form,
    _run_outcome,
    _unified_analysis_scenario_block,
    _web_job_type_choices,
)


def test_web_hides_collection_type_and_normalizes_legacy_submission():
    choices = _web_job_type_choices()

    assert "collect" not in {value for value, _label in choices}
    assert ("analyze", "📊 Анализ соцсетей") in choices
    assert _normalize_web_job_type("collect") == "analyze"
    assert _normalize_web_job_type("digest") == "digest"


def test_web_collect_form_becomes_unified_analysis_and_keeps_dates():
    payload = _payload_from_form("collect", "", "", "2025-01-01", "2025-02-01")

    assert payload["collect_before_analyze"] is True
    assert payload["cli_dates"]["start_date"] == "2025-01-01"
    assert payload["cli_dates"]["end_date"] == "2025-02-01"


def test_unified_collection_failures_make_analyze_outcome_partial():
    assert _run_outcome("analyze", {"analyzed": 4, "collection_errors": 1}) == "partial"
    assert _run_outcome("analyze", {"analyzed": 4, "collection_rate_limited": 1}) == "partial"


@pytest.mark.asyncio
async def test_unified_web_analysis_requires_a_scenario_or_active_default(monkeypatch):
    manager = AgentScenario.objects

    async def no_scenario(**_kwargs):
        return None

    async def no_default(**_kwargs):
        return None

    monkeypatch.setattr(manager, "get", no_scenario)
    monkeypatch.setattr(manager, "get_default_scenario", no_default)

    assert await _unified_analysis_scenario_block(9, None) == "создайте активный сценарий и выберите его для анализа"

    async def active_default(**_kwargs):
        return object()

    monkeypatch.setattr(manager, "get_default_scenario", active_default)
    assert await _unified_analysis_scenario_block(9, None) is None


@pytest.mark.asyncio
async def test_unified_task_can_be_paused_without_scenario_but_cannot_run_now(monkeypatch):
    task = SimpleNamespace(
        id=12,
        name="Social",
        is_active=True,
        job_type="analyze",
        payload={"collect_before_analyze": True},
        agent_scenario_id=None,
    )
    request = SimpleNamespace(state=SimpleNamespace(web_user=SimpleNamespace(id=3)))
    monkeypatch.setattr(tasks_web, "ensure_csrf", Mock(return_value=True))
    monkeypatch.setattr(tasks_web, "action_tenant_id", Mock(return_value=9))
    monkeypatch.setattr(tasks_web, "guard_web", Mock(return_value=None))
    monkeypatch.setattr(tasks_web, "add_flash", Mock())
    monkeypatch.setattr(tasks_web.AgentTask.objects, "get", AsyncMock(return_value=task))
    update = AsyncMock()
    monkeypatch.setattr(tasks_web.AgentTask.objects, "update_by_id", update)
    monkeypatch.setattr(
        tasks_web,
        "_unified_analysis_scenario_block",
        AsyncMock(return_value="создайте активный сценарий"),
    )
    queue = AsyncMock()
    monkeypatch.setattr(tasks_web, "queue_task_now", queue)

    paused = await tasks_web.task_toggle(request, 12, token="csrf", tenant_id=9)
    assert paused.status_code == 302
    update.assert_awaited_once_with(12, is_active=False)

    run_now = await tasks_web.task_run_now(request, 12, token="csrf", tenant_id=9)
    assert run_now.status_code == 302
    queue.assert_not_awaited()
