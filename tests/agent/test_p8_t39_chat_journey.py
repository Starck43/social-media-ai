"""Permission-filtered task reopening for chat follow-up."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.permissions import PermissionDeniedError, permission_scope
from app.core.tenant_context import tenant_scope
from app.services.task_results import task_configuration
from app.types import ActionType

__source_only__ = True


def _plan() -> dict:
    return {
        "version": 1,
        "goal": "Find recent public updates",
        "platforms": ["vk"],
        "source_scope": "selected",
        "source_ids": [7],
        "collection": {"limit": 20, "queries": ["public update"]},
        "window": {"type": "rolling", "days": 14},
        "scenario_id": 12,
        "trigger": {"type": "once"},
        "api_token": "PRIVATE_TOKEN_SENTINEL",
    }


def _task(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(
        id=4,
        name="Research [request-123]",
        job_type="analyze",
        is_active=False,
        last_status="ok",
        cron_expr="@once",
        payload=payload,
    )


def _read_grants() -> frozenset[tuple[str, ActionType]]:
    return frozenset(
        {
            ("agenttask", ActionType.VIEW),
            ("source", ActionType.VIEW),
            ("agentscenario", ActionType.VIEW),
        }
    )


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def __await__(self):
        async def resolved():
            return self.rows

        return resolved().__await__()


async def test_task_configuration_denial_happens_before_any_query(monkeypatch):
    import app.models

    task_get = AsyncMock()
    monkeypatch.setattr(app.models, "AgentTask", SimpleNamespace(objects=SimpleNamespace(get=task_get)))
    user = SimpleNamespace(id=7, is_active=True)

    with tenant_scope(2), permission_scope(user, member_grants=frozenset()):
        with pytest.raises(PermissionDeniedError):
            await task_configuration(4)

    task_get.assert_not_awaited()


async def test_task_configuration_returns_canonical_plan_without_raw_payload(monkeypatch):
    import app.models
    from app.models.managers.agent_task_manager import AgentTaskManager

    task_get = AsyncMock(return_value=_task({"task_plan": _plan(), "provider_token": "SECRET_SENTINEL"}))
    monkeypatch.setattr(app.models, "AgentTask", SimpleNamespace(objects=SimpleNamespace(get=task_get)))
    monkeypatch.setattr(
        app.models,
        "Source",
        SimpleNamespace(objects=SimpleNamespace(filter=lambda **kwargs: _Rows([SimpleNamespace(id=7)]))),
    )
    monkeypatch.setattr(AgentTaskManager, "get_sources", AsyncMock(return_value=[7]))
    user = SimpleNamespace(id=7, is_active=True)
    grants = _read_grants()

    with tenant_scope(2), permission_scope(user, member_grants=grants):
        result = await task_configuration(4)

    assert result["plan_status"] == "available"
    assert result["plan"]["goal"] == "Find recent public updates"
    assert result["plan"]["source_ids"] == [7]
    assert result["plan"]["window"] == {"type": "rolling", "days": 14}
    assert result["plan"]["scenario_id"] == 12
    assert "PRIVATE_TOKEN_SENTINEL" not in str(result)
    assert "SECRET_SENTINEL" not in str(result)
    assert "api_token" not in str(result)


async def test_task_configuration_hides_stale_selected_scope(monkeypatch):
    import app.models
    from app.models.managers.agent_task_manager import AgentTaskManager

    monkeypatch.setattr(
        app.models,
        "AgentTask",
        SimpleNamespace(objects=SimpleNamespace(get=AsyncMock(return_value=_task({"task_plan": _plan()})))),
    )
    monkeypatch.setattr(AgentTaskManager, "get_sources", AsyncMock(return_value=[8]))
    user = SimpleNamespace(id=7, is_active=True)

    with tenant_scope(2), permission_scope(user, member_grants=_read_grants()):
        result = await task_configuration(4)

    assert result["plan"] is None
    assert result["plan_status"] == "scope_changed"


async def test_task_configuration_hides_source_scope_without_source_view(monkeypatch):
    import app.models
    from app.models.managers.agent_task_manager import AgentTaskManager

    task_get = AsyncMock(return_value=_task({"task_plan": _plan()}))
    get_sources = AsyncMock(return_value=[7])
    monkeypatch.setattr(app.models, "AgentTask", SimpleNamespace(objects=SimpleNamespace(get=task_get)))
    monkeypatch.setattr(AgentTaskManager, "get_sources", get_sources)
    user = SimpleNamespace(id=7, is_active=True)

    with (
        tenant_scope(2),
        permission_scope(
            user, member_grants=frozenset({("agenttask", ActionType.VIEW), ("agentscenario", ActionType.VIEW)})
        ),
    ):
        result = await task_configuration(4)

    assert result["plan"] is None
    assert result["plan_status"] == "source_access_required"
    get_sources.assert_not_awaited()
