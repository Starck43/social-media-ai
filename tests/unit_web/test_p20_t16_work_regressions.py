"""Regression checks for the Today service adapters used by the Work surfaces."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core import tenant_context
from app.models.managers.agent_task_manager import AgentTaskManager
from app.services.planner.tasks import planner_service
from app.services.workspace.today import TodayRepository

__source_only__ = True


@pytest.mark.asyncio
async def test_today_planner_adapts_scoped_service_projection(monkeypatch):
    monkeypatch.setattr(tenant_context, "current_tenant_id", lambda: 42)
    monkeypatch.setattr(tenant_context, "is_bypass", lambda: False)
    task = SimpleNamespace(id=3, model_dump=lambda: {"id": 3, "title": "Synthetic", "status": "todo"})
    fetch = AsyncMock(side_effect=[{"items": [task], "next_before_id": None}, {"items": [], "next_before_id": None}])
    monkeypatch.setattr(planner_service, "list", fetch)
    result = await TodayRepository().planner_tasks(42, limit=5)
    assert fetch.await_count == 2
    assert [call.kwargs for call in fetch.await_args_list] == [
        {"limit": 15, "status": "todo"},
        {"limit": 15, "status": "in_progress"},
    ]
    assert [(row.tenant_id, row.id, row.title) for row in result] == [(42, 3, "Synthetic")]


@pytest.mark.asyncio
async def test_today_adapters_fail_closed_for_wrong_tenant(monkeypatch):
    monkeypatch.setattr(tenant_context, "current_tenant_id", lambda: 43)
    monkeypatch.setattr(tenant_context, "is_bypass", lambda: False)
    fetch = AsyncMock()
    monkeypatch.setattr(planner_service, "list", fetch)
    schedules = AsyncMock()
    monkeypatch.setattr(AgentTaskManager, "upcoming_effective", schedules)
    with pytest.raises(PermissionError):
        await TodayRepository().planner_tasks(42)
    with pytest.raises(PermissionError):
        await TodayRepository().upcoming_agent_tasks(42)
    fetch.assert_not_awaited()
    schedules.assert_not_awaited()


@pytest.mark.asyncio
async def test_today_schedule_passes_source_permission_and_aware_time(monkeypatch):
    monkeypatch.setattr(tenant_context, "current_tenant_id", lambda: 42)
    monkeypatch.setattr(tenant_context, "is_bypass", lambda: False)
    schedules = AsyncMock(return_value=[])
    monkeypatch.setattr(AgentTaskManager, "upcoming_effective", schedules)
    assert await TodayRepository().upcoming_agent_tasks(42, show_sources=False, limit=3) == []
    args = schedules.await_args.kwargs
    assert args["include_source_based"] is False
    assert args["limit"] == 3
    assert args["now"].tzinfo is not None
