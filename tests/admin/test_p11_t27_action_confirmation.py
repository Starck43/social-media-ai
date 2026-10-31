"""P11-T27 S4: a mutating or paid console action never runs on GET.

The defect: `run-now`, `toggle-active`, `send-to-messenger` and `test-connection`
were `@action` handlers, i.e. bookmarkable, CSRF-free GET URLs that changed state
or spent provider tokens. `send-to-messenger` even took `recipient_id` from the
query string, against the console rule that identity never comes from
path/query.

The contract pinned here:

* the GET action renders a confirmation page and performs no write, no dispatch
  and no provider call;
* the write lives behind an `@expose(..., methods=["POST"])` route that verifies
  the session-bound CSRF token itself (defence in depth over the middleware) and
  reads its selection from the POST body, not from the query string.

Source-only: no database, no console mount, no HTTP server.
"""

from __future__ import annotations

# `tests/conftest.py` skips the suite database when only source-only modules are selected.
__source_only__ = True

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.admin.csrf import SESSION_ID_KEY, CSRFTokenManager
from app.admin.views import AgentScenarioAdmin, AgentTaskAdmin, LLMProviderAdmin, NotificationAdmin

SENTINEL = object()


class _Request:
    """Just enough of a Starlette request for the action bodies."""

    def __init__(self, *, pks="7", admin_user=None, form=None, session=None, app=None):
        self.state = SimpleNamespace(admin_user=admin_user)
        self.session = {} if session is None else session
        self.query_params = {"pks": pks} if pks is not None else {}
        self._form = form or {}
        self.app = app or SimpleNamespace(state=SimpleNamespace())
        self.url = SimpleNamespace(path="/admin/agent-task/action/run-now")

    async def form(self):
        return self._form

    def url_for(self, name, **kwargs):
        return f"/_stub/{name}"


def _run(coro):
    return asyncio.run(coro)


def _unwrap(func):
    while hasattr(func, "__wrapped__"):
        func = func.__wrapped__
    return func


def _csrf(payload=None):
    """A real manager plus a session that already holds a bound, valid token."""
    manager = CSRFTokenManager(secret_key="test-secret")
    session_id = "sid-test"
    token = manager.generate_token(session_id=session_id)
    app = SimpleNamespace(state=SimpleNamespace(csrf_manager=manager))
    session = {SESSION_ID_KEY: session_id}
    return app, session, token, {"csrf_token": token, **(payload or {})}


# --- the CSRF gate --------------------------------------------------------


def test_an_action_post_without_a_token_is_refused():
    from app.admin.actions import verified_action_form

    request = _Request(form={"pks": "7"})

    with pytest.raises(HTTPException) as exc:
        _run(verified_action_form(request))
    assert exc.value.status_code == 403


def test_an_action_post_with_a_foreign_token_is_refused():
    from app.admin.actions import verified_action_form

    app, session, _token, _form = _csrf()
    request = _Request(form={"pks": "7", "csrf_token": "not-a-token"}, session=session, app=app)

    with pytest.raises(HTTPException) as exc:
        _run(verified_action_form(request))
    assert exc.value.status_code == 403


def test_an_action_post_with_a_session_bound_token_passes():
    from app.admin.actions import verified_action_form

    app, session, _token, form = _csrf({"pks": "7"})
    request = _Request(form=form, session=session, app=app)

    assert _run(verified_action_form(request)) == form


# --- GET never mutates ----------------------------------------------------


def test_run_now_get_renders_the_confirmation_and_dispatches_nothing(monkeypatch):
    rendered: dict = {}
    dispatched: list = []

    async def fake_get(**kwargs):
        return SimpleNamespace(id=kwargs["id"], name="demo-task", tenant_id=3)

    async def fake_render(view, request, **kwargs):
        rendered.update(kwargs)
        return SENTINEL

    async def fake_dispatch(task):
        dispatched.append(task)

    monkeypatch.setattr("app.models.AgentTask.objects.get", fake_get)
    monkeypatch.setattr("app.admin.actions.render_action_confirmation", fake_render)
    monkeypatch.setattr("app.jobs.dispatcher.run_task_directly", fake_dispatch)

    result = _run(_unwrap(AgentTaskAdmin.run_now_action)(AgentTaskAdmin(), _Request(pks="7", admin_user=_actor())))

    assert result is SENTINEL
    assert "demo-task" in rendered["message"]
    assert dispatched == []


def test_toggle_active_get_never_writes(monkeypatch):
    written: list = []

    async def fake_get(**kwargs):
        return SimpleNamespace(id=kwargs["id"], name="scenario-a", is_active=True)

    async def fake_update(**kwargs):
        written.append(kwargs)

    async def fake_render(view, request, **kwargs):
        return SENTINEL

    monkeypatch.setattr("app.models.AgentScenario.objects.get", fake_get)
    monkeypatch.setattr("app.models.AgentScenario.objects.update_by_id", fake_update)
    monkeypatch.setattr("app.admin.actions.render_action_confirmation", fake_render)

    result = _run(_unwrap(AgentScenarioAdmin.toggle_active_action)(AgentScenarioAdmin(), _Request(pks="5")))

    assert result is SENTINEL
    assert written == []


def test_send_to_messenger_get_never_sends(monkeypatch):
    sent: list = []

    async def fake_get(**kwargs):
        return SimpleNamespace(id=kwargs["id"], title="Alert", tenant_id=1)

    async def fake_send(**kwargs):
        sent.append(kwargs)

    async def fake_render(view, request, **kwargs):
        return SENTINEL

    monkeypatch.setattr("app.models.Notification.objects.get", fake_get)
    monkeypatch.setattr("app.services.notifications.messenger.messenger_service.send_notification", fake_send)
    monkeypatch.setattr("app.admin.actions.render_action_confirmation", fake_render)

    result = _run(_unwrap(NotificationAdmin.send_to_messenger_action)(NotificationAdmin(), _Request(pks="9")))

    assert result is SENTINEL
    assert sent == []


def test_test_connection_get_never_calls_the_provider(monkeypatch):
    created: list = []

    async def fake_get(**kwargs):
        return SimpleNamespace(id=kwargs["id"], name="provider-a")

    def fake_create(*args, **kwargs):
        created.append((args, kwargs))
        raise AssertionError("GET must not build a provider client")

    async def fake_render(view, request, **kwargs):
        return SENTINEL

    monkeypatch.setattr("app.models.LLMProvider.objects.get", fake_get)
    monkeypatch.setattr("app.services.ai.llm_client.LLMClientFactory.create", fake_create)
    monkeypatch.setattr("app.admin.actions.render_action_confirmation", fake_render)

    request = _Request(pks="3", admin_user=_actor())
    result = _run(_unwrap(LLMProviderAdmin.test_connection_action)(LLMProviderAdmin(), request))

    assert result is SENTINEL
    assert created == []


# --- POST executes, and reads only the body -------------------------------


def test_run_now_submit_takes_the_task_from_the_body_not_the_query(monkeypatch):
    captured: list = []

    async def fake_run(self, request, task_id):
        captured.append(task_id)
        return SENTINEL

    monkeypatch.setattr(AgentTaskAdmin, "_run_task_now", fake_run)
    app, session, _token, form = _csrf({"pks": "9"})
    request = _Request(pks="1", form=form, session=session, app=app)

    result = _run(_unwrap(AgentTaskAdmin.run_now_submit)(AgentTaskAdmin(), request))

    assert result is SENTINEL
    assert captured == [9]


def test_toggle_submit_takes_the_selection_from_the_body(monkeypatch):
    captured: list = []

    async def fake_toggle(self, pks):
        captured.append(pks)

    monkeypatch.setattr(AgentScenarioAdmin, "_toggle_scenarios", fake_toggle)
    app, session, _token, form = _csrf({"pks": "5,6"})
    request = _Request(pks="1", form=form, session=session, app=app)

    _run(_unwrap(AgentScenarioAdmin.toggle_active_submit)(AgentScenarioAdmin(), request))

    assert captured == ["5,6"]


def test_send_submit_takes_the_recipient_from_the_body(monkeypatch):
    captured: list = []

    async def fake_send(self, request, pk, recipient_id):
        captured.append((pk, recipient_id))
        return SENTINEL

    monkeypatch.setattr(NotificationAdmin, "_send_notification_to_messenger", fake_send)
    app, session, _token, form = _csrf({"pks": "9", "recipient_id": " -100123 "})
    request = _Request(pks="1", form=form, session=session, app=app)

    result = _run(_unwrap(NotificationAdmin.send_to_messenger_submit)(NotificationAdmin(), request))

    assert result is SENTINEL
    assert captured == [("9", "-100123")]


def _actor():
    return SimpleNamespace(id=42, username="operator", is_active=True, is_superuser=False)
