"""Regression: a staff task action cannot fetch/dispatch a foreign workspace task."""

import secrets
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.admin.views import AgentTaskAdmin
from app.core.permissions import get_current_user, operator_permission_scope, permission_scope
from app.core.tenant_context import current_tenant_ids, is_bypass, tenant_scope
from app.models import AgentTask, Tenant
from app.types import ActionType

pytestmark = pytest.mark.tenancy


@contextmanager
def _trusted_setup():
    """Use trusted bootstrap scopes only to arrange isolated database records."""
    with tenant_scope(bypass=True), operator_permission_scope():
        yield


def _name(prefix):
    return f"{prefix}-{secrets.token_hex(5)}"


async def _workspace():
    with _trusted_setup():
        slug = _name("p9-task-action")
        return await Tenant.objects.create(name=slug, slug=slug)


async def _task(tenant, label):
    with _trusted_setup():
        return await AgentTask.objects.create(
            tenant_id=tenant.id,
            name=_name(label),
            cron_expr="0 9 * * *",
            job_type="collect",
        )


class _Actor:
    id = 987321
    is_active = True
    is_superuser = False

    def has_admin_access(self):
        return True

    def model_permissions(self, model_name):
        return {ActionType.UPDATE} if model_name.lower() == "agenttask" else set()

    def has_perm_for(self, model_name, action):
        return action in self.model_permissions(model_name)


class _Request:
    def __init__(self, task_id, actor):
        self.state = SimpleNamespace(admin_user=actor)
        self.session = {}
        self.query_params = {"pks": str(task_id)}

    def url_for(self, name, **kwargs):
        return f"/_stub/{name}"


def _unwrapped_action():
    action = AgentTaskAdmin.run_now_action
    while hasattr(action, "__wrapped__"):
        action = action.__wrapped__
    return action


@pytest.mark.asyncio
async def test_staff_run_now_reads_only_active_workspace_task(monkeypatch):
    workspace_a, workspace_b = await _workspace(), await _workspace()
    task_a = await _task(workspace_a, "own-task")
    task_b = await _task(workspace_b, "foreign-task")
    actor = _Actor()
    member_grants = frozenset({("agenttask", ActionType.UPDATE)})
    dispatched = []

    async def fake_run_task_directly(task):
        dispatched.append(
            {
                "task_id": task.id,
                "actor_id": getattr(get_current_user(), "id", None),
                "tenant_ids": current_tenant_ids(),
                "bypass": is_bypass(),
            }
        )
        return {"status": "done"}

    monkeypatch.setattr("app.jobs.dispatcher.run_task_directly", fake_run_task_directly)
    action = _unwrapped_action()

    with tenant_scope(workspace_a.id), permission_scope(actor, member_grants=member_grants):
        # The real AgentTask manager lookup must hide a guessed foreign ID.
        await action(AgentTaskAdmin(), _Request(task_b.id, actor))
        assert dispatched == []

        # A task in the active workspace still dispatches under the same tenant
        # and the authenticated actor identity.
        await action(AgentTaskAdmin(), _Request(task_a.id, actor))

    assert dispatched == [
        {
            "task_id": task_a.id,
            "actor_id": actor.id,
            "tenant_ids": (workspace_a.id,),
            "bypass": False,
        }
    ]
