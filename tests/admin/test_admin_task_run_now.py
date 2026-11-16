"""The admin "run now" action must credit the signed-in operator, not untrusted
input, into the manual Job admission context.

`_admit_task_run` reads the trusted actor from the ambient `permission_scope`
user and stamps `Job.initiated_by_user_id` from it. The action must run its
dispatch inside `permission_scope(request.state.admin_user)` and fail closed
when the operator identity is missing — never fall back to query/body/task
payload values.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.admin.views import AgentTaskAdmin
from app.models import AgentTask


class _StubRequest:
    def __init__(self, admin_user=None, pks="7"):
        self.state = SimpleNamespace(admin_user=admin_user)
        self.session = {}
        self.query_params = {"pks": pks}

    def url_for(self, name, **kwargs):
        return f"/_stub/{name}"


def _run_now_action():
    """The executor, not the GET confirmation page.

    Since S4 the action URL only renders a confirmation; the state change lives
    in `_run_task_now`, which the CSRF-checked POST route calls. This test is
    about the executor's own contract — the trusted operator enters the
    admission context — so it targets that method directly.
    """
    return AgentTaskAdmin._run_task_now


def _admin_user(active=True, *, superuser=False):
    return SimpleNamespace(id=42, username="operator", is_active=active, is_superuser=superuser)


async def _fake_task_get(**kwargs):
    return SimpleNamespace(id=kwargs.get("id"), name="demo-task", tenant_id=3)


def _patch_task_get(monkeypatch, seen):
    """A console read that fails closed outside the actor's own workspaces."""

    async def scoped_get(**kwargs):
        from app.core.tenant_context import current_tenant_ids, is_bypass

        seen["bypass"] = is_bypass()
        seen["tenants"] = current_tenant_ids()
        return await _fake_task_get(**kwargs)

    monkeypatch.setattr(AgentTask.objects, "get", scoped_get)


def _patch_dispatch(monkeypatch, captured):
    async def fake_run_directly(task):
        from app.core.permissions import get_current_user

        captured["task_id"] = task.id
        captured["actor_id"] = getattr(get_current_user(), "id", None)
        return {"status": "done"}

    monkeypatch.setattr("app.jobs.dispatcher.run_task_directly", fake_run_directly)


@pytest.mark.asyncio
async def test_a_trusted_active_operator_reaches_the_admission_context(monkeypatch):
    """The authenticated operator, not the query params, enters the manual Job
    admission context (`permission_scope` -> `get_current_user`)."""
    captured: dict = {}
    seen: dict = {}
    request = _StubRequest(admin_user=_admin_user())
    _patch_task_get(monkeypatch, seen)
    _patch_dispatch(monkeypatch, captured)

    await _run_now_action()(AgentTaskAdmin(), request, 7)

    assert captured["task_id"] == 7
    assert captured["actor_id"] == 42
    # A staff operator reads the task inside their own scope, never by bypass:
    # a guessed id from a foreign workspace would resolve to nothing.
    assert seen["bypass"] is False


@pytest.mark.asyncio
async def test_the_owner_reads_the_task_across_workspaces(monkeypatch):
    captured: dict = {}
    seen: dict = {}
    _patch_task_get(monkeypatch, seen)
    _patch_dispatch(monkeypatch, captured)

    await _run_now_action()(AgentTaskAdmin(), _StubRequest(admin_user=_admin_user(superuser=True)), 7)

    assert captured["task_id"] == 7
    assert seen["bypass"] is True


@pytest.mark.asyncio
async def test_an_absent_or_inactive_operator_is_denied(monkeypatch):
    """Fail closed: no session identity means no dispatch, whatever pks says."""
    captured: dict = {}
    seen: dict = {}
    _patch_task_get(monkeypatch, seen)
    _patch_dispatch(monkeypatch, captured)

    with pytest.raises(HTTPException) as exc:
        await _run_now_action()(AgentTaskAdmin(), _StubRequest(admin_user=None), 7)
    assert exc.value.status_code == 403
    assert not captured

    with pytest.raises(HTTPException) as exc:
        await _run_now_action()(AgentTaskAdmin(), _StubRequest(admin_user=_admin_user(active=False)), 7)
    assert exc.value.status_code == 403
    assert not captured
