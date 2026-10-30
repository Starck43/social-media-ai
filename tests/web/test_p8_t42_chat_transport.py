"""Selected chat context stays explicit and is revalidated before dispatch."""

from __future__ import annotations

from types import ModuleType, SimpleNamespace

import pytest
from fastapi import APIRouter, Request

from tests.source_import_isolation import load_isolated_source

__source_only__ = True


def _module(name, **values):
    module = ModuleType(name)
    module.__dict__.update(values)
    return module


def _request():
    return SimpleNamespace(
        headers={"x-requested-with": "XMLHttpRequest", "accept": "application/json"},
        state=SimpleNamespace(
            web_user=SimpleNamespace(id=7),
            tenant_id=12,
            memberships=[],
            web_perms=SimpleNamespace(can_open_chat=True),
        ),
    )


def _load_chat(monkeypatch, *, permitted=True, confirmation_pending=False):
    seen = []
    turns = []

    async def handle_web_message(text, *, tenant_id, user_id, role_id=None, role_codename=None):
        turns.append((text, tenant_id, user_id, role_id, role_codename))
        return "ok"

    async def confirmation(_user_id):
        return confirmation_pending

    async def transcript(_user_id):
        return []

    def workspace_denied(request):
        seen.append("workspace-check")
        return (
            None
            if permitted
            else __import__("fastapi.responses", fromlist=["JSONResponse"]).JSONResponse(
                {"error": "Выбранная задача больше недоступна. Снимите выбор."}, status_code=403
            )
        )

    async def selected_task(request, task_id):
        seen.append(("selected", request.state.tenant_id, task_id))
        return {"id": task_id, "name": "Research"} if permitted else None

    deps = _module(
        "app.web.deps",
        add_flash=lambda *args: None,
        ensure_csrf=lambda *_args: True,
        render=lambda *args, **kwargs: None,
        render_section_denied=lambda *args, **kwargs: None,
    )
    nav = _module("app.web.nav", section_of=lambda _path: None)
    workspace = _module(
        "app.web.chat_workspace",
        router=APIRouter(prefix="/workspace"),
        selected_task=selected_task,
        workspace_denied=workspace_denied,
    )
    runtime = _module(
        "app.agent.runtime",
        LIMIT_REACHED_PREFIX="LIMIT:",
        handle_web_message=handle_web_message,
    )
    module = load_isolated_source(
        "app.web._p8_t42_chat_under_test",
        "app/web/chat.py",
        imports={
            "app.web.deps": deps,
            "app.web.nav": nav,
            "app.web.chat_workspace": workspace,
            "app.agent.runtime": runtime,
        },
    )
    module._confirmation_pending = confirmation
    module._transcript = transcript
    module._display_transcript = lambda _items: []
    return module, seen, turns


async def test_selected_task_is_revalidated_and_carried_into_shared_turn(monkeypatch):
    module, checks, turns = _load_chat(monkeypatch)

    response = await module.chat_send(_request(), text="Покажи результат", token="csrf", task_id=31)

    assert response.status_code == 200
    assert checks == ["workspace-check", ("selected", 12, 31)]
    assert turns == [("[Контекст: задача #31]\nПокажи результат", 12, 7, None, None)]


async def test_inaccessible_selected_task_never_reaches_agent(monkeypatch):
    module, checks, turns = _load_chat(monkeypatch, permitted=False)

    response = await module.chat_send(_request(), text="Покажи результат", token="csrf", task_id=31)

    assert response.status_code == 403
    assert checks == ["workspace-check"]
    assert turns == []


async def test_confirmation_reply_keeps_existing_confirmation_protocol(monkeypatch):
    module, checks, turns = _load_chat(monkeypatch, confirmation_pending=True)

    response = await module.chat_send(_request(), text="да", token="csrf", task_id=31)

    assert response.status_code == 200
    assert checks == []
    assert turns == [("да", 12, 7, None, None)]


async def test_confirmation_can_finish_after_selected_task_access_is_revoked(monkeypatch):
    module, checks, turns = _load_chat(monkeypatch, permitted=False, confirmation_pending=True)

    response = await module.chat_send(_request(), text="да", token="csrf", task_id=31)

    assert response.status_code == 200
    assert checks == []
    assert turns == [("да", 12, 7, None, None)]
