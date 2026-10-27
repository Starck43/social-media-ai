"""Confirmed chat mutations use the shared social plan without legacy date requirements."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.agent.toolset import tasks
from app.core.tenant_context import tenant_scope
from app.models import AgentTask, Tenant
from app.models.managers.agent_task_manager import AgentTaskManager
from app.models.managers.job_manager import JobManager

__source_only__ = True


def _plan():
    return {
        "goal": "Research requests",
        "platforms": ["vk"],
        "source_scope": "selected",
        "source_ids": [4],
        "window": {"type": "rolling", "days": 3},
        "trigger": {"type": "schedule", "cron": "0 9 * * *", "timezone": "UTC"},
    }


async def test_chat_plan_conflicts_are_rejected_before_persistence(monkeypatch):
    get = AsyncMock()
    monkeypatch.setattr(AgentTask.objects, "get", get)
    result = await tasks.task_add("Research", "0 8 * * *", "collect", task_plan=_plan())
    assert "conflicts" in result["error"]
    get.assert_not_awaited()


async def test_chat_rolling_plan_is_saved_atomically_then_activated_after_consent(monkeypatch):
    from app.core import database
    from app.services.billing import payer

    class Session:
        @asynccontextmanager
        async def begin(self):
            yield

        async def close(self):
            pass

    session = Session()
    recorded = {}
    row = SimpleNamespace(id=12, name="Research", next_run_at=None)

    async def create(**values):
        recorded.update(values)
        return row

    monkeypatch.setattr(database, "new_session", lambda: session)
    monkeypatch.setattr(tasks, "_get_job_types", lambda: ("collect",))
    monkeypatch.setattr(AgentTask.objects, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(AgentTask.objects, "create", create)
    activate = AsyncMock()
    monkeypatch.setattr(AgentTask.objects, "update_by_id", activate)
    monkeypatch.setattr(Tenant.objects, "get", AsyncMock(return_value=SimpleNamespace(timezone="UTC")))
    monkeypatch.setattr(JobManager, "lock_source_admission", AsyncMock())
    links = AsyncMock()
    monkeypatch.setattr(AgentTaskManager, "set_sources", links)
    consent = AsyncMock()
    monkeypatch.setattr(payer, "consent_current_user_as_task_payer", consent)
    with tenant_scope(2):
        result = await tasks.task_add("Research", "0 9 * * *", "collect", task_plan=_plan())
    assert result["active"] is True
    assert recorded["is_active"] is False
    assert recorded["payload"]["task_plan"]["goal"] == "Research requests"
    assert recorded["payload"]["task_plan"]["window"] == {"type": "rolling", "days": 3}
    assert "cli_dates" not in recorded["payload"]
    links.assert_awaited_once_with(12, [4], session=session)
    consent.assert_awaited_once_with(12)
    activate.assert_awaited_once_with(12, is_active=True)


async def test_chat_rejects_unknown_job_type_before_plan_projection(monkeypatch):
    get = AsyncMock()
    monkeypatch.setattr(tasks, "_get_job_types", lambda: ("collect",))
    monkeypatch.setattr(AgentTask.objects, "get", get)

    result = await tasks.task_add("Research", "0 9 * * *", "unsupported", task_plan=_plan())

    assert "Unknown job_type" in result["error"]
    get.assert_not_awaited()
