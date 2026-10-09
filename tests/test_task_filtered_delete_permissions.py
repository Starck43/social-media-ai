"""Filtered task deletion permission regressions; prepared, not run.

The base delete is mocked: no deletion SQL is issued by these methods. Normal
pytest still loads repository DB conftest; use the configured isolated schema.
Anonymous denial follows the separately reviewed PR22 policy, not this patch.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.permissions import PermissionDeniedError, permission_scope
from app.core.tenant_context import tenant_scope
from app.models.managers.agent_task_manager import AgentTaskManager
from app.models.managers.base_manager import BaseManager
from app.types import ActionType

pytestmark = pytest.mark.tenancy


def actor(allowed):
    return SimpleNamespace(
        is_active=True,
        is_superuser=False,
        role=None,
        _is_superuser_role=lambda: False,
        has_perm_for=lambda model, action: allowed and model == "agenttask" and action == ActionType.DELETE,
    )


@pytest.mark.parametrize("filters", [{"name": "private-task"}, {"id": 91}, {}], ids=["name", "id", "all"])
async def test_filtered_delete_refuses_actor_without_delete_before_base(filters):
    manager = object.__new__(AgentTaskManager)
    delete = AsyncMock(return_value=1)
    with patch.object(BaseManager, "delete", delete):
        with tenant_scope(31), permission_scope(actor(False)):
            with pytest.raises(PermissionDeniedError):
                await manager.delete(**filters)
    delete.assert_not_awaited()


@pytest.mark.parametrize("filters", [{"name": "private-task"}, {"id": 91}, {}], ids=["name", "id", "all"])
async def test_filtered_delete_preserves_allowed_filters_and_count(filters):
    manager = object.__new__(AgentTaskManager)
    delete = AsyncMock(return_value=3)
    with patch.object(BaseManager, "delete", delete):
        with tenant_scope(31), permission_scope(actor(True)):
            result = await manager.delete(**filters)
    assert result == 3
    delete.assert_awaited_once_with(session=None, **filters)


async def test_filtered_delete_preserves_explicit_session():
    manager = object.__new__(AgentTaskManager)
    session = object()
    delete = AsyncMock(return_value=0)
    with patch.object(BaseManager, "delete", delete):
        with tenant_scope(31), permission_scope(actor(True)):
            result = await manager.delete(session=session, name="missing")
    assert result == 0
    delete.assert_awaited_once_with(session=session, name="missing")


async def test_filtered_delete_keeps_explicit_operator_bypass():
    manager = object.__new__(AgentTaskManager)
    delete = AsyncMock(return_value=2)
    with patch.object(BaseManager, "delete", delete):
        with tenant_scope(bypass=True):
            result = await manager.delete(name="operator-task")
    assert result == 2
    delete.assert_awaited_once_with(session=None, name="operator-task")


async def test_filtered_delete_rechecks_right_before_each_base_call():
    manager = object.__new__(AgentTaskManager)
    user = actor(True)
    delete = AsyncMock(return_value=1)
    with patch.object(BaseManager, "delete", delete):
        with tenant_scope(31), permission_scope(user):
            assert await manager.delete(id=91) == 1
            user.has_perm_for = lambda *args: False
            with pytest.raises(PermissionDeniedError):
                await manager.delete(id=91)
    delete.assert_awaited_once_with(session=None, id=91)
