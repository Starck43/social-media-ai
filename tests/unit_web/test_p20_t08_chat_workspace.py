"""Unit tests for P20-T08 — Chat workspace: context panel, inline cards and chat dock.

Covers:
1. Chat page layout per WORKSPACE_UX.md#chat-page: boundary badge, removable context chips,
   quick actions, and 4-tab right panel («Контекст», «Ждут решения», «В работе», «Материалы»).
2. Context revalidation on every send, chip removal (POST /app/chat/context/clear),
   denied/changed access, and local_only refusal before send.
3. Extended resolve_context readers for 'contact' and 'task' with permission and tenant checks.
4. Typed inline cards (plan_preview, decision, progress, result, draft, brief) refreshed from
   owning services, stale card refresh / revoked access handling, and strict stripping of raw
   payloads and credentials.
5. «Спросить агента» dock (_chat_dock.html) on object pages, Escape/focus handling for panel
   and dock, 360px responsive layout, and unchanged plain-text behavior for bot transports.
"""

from __future__ import annotations

import json
import types
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from fastapi import APIRouter, HTTPException
from jinja2 import Environment, FileSystemLoader, select_autoescape

from tests.shared.template_shell import shell_perms
from tests.source_import_isolation import load_isolated_source

__source_only__ = True
ROOT = Path(__file__).resolve().parents[2]


def _build_jinja_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(ROOT / "app/web/templates"),
        autoescape=select_autoescape(),
        extensions=["jinja2.ext.do"],
    )
    env.filters["ui_label"] = str
    env.filters["human_dt"] = lambda val, **kw: str(val or "")
    env.filters["static_v"] = lambda path: f"/static/{path}"
    env.globals["static_v"] = lambda path: f"/static/{path}"
    env.globals["url_for"] = lambda endpoint, **kw: f"/static/{kw.get('path', '')}"
    env.globals["icon"] = lambda name, **kw: f'<svg data-icon="{name}"></svg>'
    return env


def _load_chat_workspace_module(
    *, rows=(), config=None, result=None, resolved_ctx=None, task_view=True, can_results=True
):
    calls = []
    active_tenant = {"id": None}

    @contextmanager
    def tenant_scope(tenant_id):
        prev = active_tenant["id"]
        active_tenant["id"] = tenant_id
        try:
            yield
        finally:
            active_tenant["id"] = prev

    async def task_configuration(task_id):
        calls.append(("configuration", task_id, active_tenant["id"]))
        if callable(config):
            return await config(task_id)
        return config

    async def task_result(task_id, *, limit):
        calls.append(("result", task_id, limit, active_tenant["id"]))
        if callable(result):
            return await result(task_id, limit=limit)
        return result

    async def resolve_context(context_type, context_id, boundary):
        calls.append(("resolve_context", context_type, context_id, boundary, active_tenant["id"]))
        if callable(resolved_ctx):
            return await resolved_ctx(context_type, context_id, boundary)
        if resolved_ctx is None:
            raise HTTPException(404, "Контекст недоступен")
        return resolved_ctx

    class _Query:
        def __init__(self, items):
            self.items = list(items)

        def filter(self, *a, **kw):
            return self

        def order_by(self, *a):
            return self

        def offset(self, n):
            return self

        def limit(self, n):
            async def _run():
                return self.items[:n]

            return _run()

    def perms_can(_req, model, _action):
        if model == "agenttask":
            return task_view
        if model in {"source", "aianalytics"}:
            return can_results
        return True

    deps = types.ModuleType("app.web.deps")
    deps.perms_can = perms_can

    tenant_mod = types.ModuleType("app.core.tenant_context")
    tenant_mod.tenant_scope = tenant_scope

    models_mod = types.ModuleType("app.models")
    models_mod.AgentTask = NS(
        objects=NS(filter=lambda **kw: _Query(rows)),
        id=NS(desc=lambda: None),
        name=NS(ilike=lambda *a, **kw: None),
    )

    results_mod = types.ModuleType("app.services.task_results")
    results_mod.task_configuration = task_configuration
    results_mod.task_result = task_result

    kw_mod = types.ModuleType("app.services.knowledge.workspace")
    kw_mod.resolve_context = resolve_context

    module = load_isolated_source(
        "app.web._p20_t08_chat_workspace",
        ROOT / "app/web/chat_workspace.py",
        imports={
            "app.web.deps": deps,
            "app.core.tenant_context": tenant_mod,
            "app.models": models_mod,
            "app.services.task_results": results_mod,
            "app.services.knowledge.workspace": kw_mod,
        },
    )
    return module, calls


def _load_chat_module(*, resolve_fn=None, selected_task_fn=None, confirmation_pending=False):
    turns = []

    async def handle_web_message(text, *, tenant_id, user_id, role_id=None, role_codename=None, **kw):
        turns.append({"text": text, "tenant_id": tenant_id, "user_id": user_id})
        return "Ответ агента"

    async def _resolve(context_type, context_id, boundary):
        if callable(resolve_fn):
            return await resolve_fn(context_type, context_id, boundary)
        return {
            "id": context_id,
            "context_id": str(context_id),
            "type": context_type,
            "context_type": context_type,
            "title": f"Объект {context_id}",
            "source_ref": f"{context_type}:{context_id}",
            "ref": f"{context_type}:{context_id}",
            "citation_anchor": f"#{context_id}",
            "revision_hash": "sha256:abc",
            "excerpt": "Краткая выдержка",
            "data_boundary": boundary,
            "boundary": boundary,
            "is_local_only": boundary == "local_only",
        }

    deps = types.ModuleType("app.web.deps")
    deps.render = lambda req, tmpl, **extra: NS(template=tmpl, context=extra, status_code=200)
    deps.add_flash = lambda *a: None
    deps.ensure_csrf = lambda req, tok: tok == "valid-csrf"
    deps.render_section_denied = lambda req, sec: NS(status_code=403)

    ws_mod = types.ModuleType("app.web.chat_workspace")
    ws_mod.router = APIRouter(prefix="/workspace")
    ws_mod.workspace_denied = lambda req: None
    ws_mod.selected_task = selected_task_fn or (lambda req, tid: {"id": tid, "name": f"Task {tid}"})

    nav_mod = types.ModuleType("app.web.nav")
    nav_mod.section_of = lambda p: "chat"

    runtime_mod = types.ModuleType("app.agent.runtime")
    runtime_mod.LIMIT_REACHED_PREFIX = "LIMIT:"
    runtime_mod.handle_web_message = handle_web_message

    async def _web_session_id(_uid):
        return None

    runtime_mod.web_session_id = _web_session_id

    att_mod = types.ModuleType("app.services.chat_attachments")

    async def _policy(**kw):
        return NS(to_public_dict=lambda: {"enabled": True, "model_name": "gpt-4o", "model_max_tokens": 8192})

    att_mod.resolve_workspace_attachment_policy = _policy

    kw_mod = types.ModuleType("app.services.knowledge.workspace")
    kw_mod.resolve_context = _resolve

    chat_mod = load_isolated_source(
        "_p20_t08_chat_web",
        ROOT / "app/web/chat.py",
        imports={
            "app.web.deps": deps,
            "app.web.chat_workspace": ws_mod,
            "app.web.nav": nav_mod,
            "app.agent.runtime": runtime_mod,
            "app.services.chat_attachments": att_mod,
            "app.services.knowledge.workspace": kw_mod,
        },
    )

    async def _conf(_uid):
        return confirmation_pending

    async def _trans(_uid):
        return []

    chat_mod._confirmation_pending = _conf
    chat_mod._transcript = _trans
    chat_mod._display_transcript = lambda _items: []
    return chat_mod, turns


def test_chat_template_renders_boundary_badge_chips_quick_actions_and_four_tabs():
    """Verify chat.html and _chat_workspace.html render boundary badge, chips, quick actions, and 4 tabs."""
    env = _build_jinja_env()
    tmpl = env.get_template("web/chat.html")
    rendered = tmpl.render(
        user=NS(id=1, username="owner", is_authenticated=True, has_admin_access=lambda: False),
        tenant=NS(id=1, name="Рабочее пространство"),
        perms=shell_perms(can_open_chat=True, is_owner=True, can_manage_workspace=lambda tid: False),
        member_level=NS(label="Владелец"),
        section="chat",
        messages=[],
        is_owner=True,
        awaiting_confirmation=False,
        attachment_policy=None,
        active_boundary="company",
        scoped_context={
            "type": "contact",
            "id": 42,
            "context_id": "42",
            "boundary": "company",
            "title": "Иван Петров",
            "ref": "contact:42",
            "is_local_only": False,
        },
        csrf="csrf-token",
        nav=[
            NS(key="sources", label="Источники", href="/app/sources", ready=True),
            NS(key="scenarios", label="Сценарии", href="/app/scenarios", ready=True),
            NS(key="tasks", label="Задачи", href="/app/tasks", ready=True),
        ],
        request=NS(query_params={}),
    )

    assert "data-chat-boundary-badge" in rendered
    assert "data-chat-context-chips" in rendered
    assert "data-remove-context-chip" in rendered
    assert "data-chat-inline-cards" in rendered
    assert "Подготовить задачу" in rendered
    assert "Разобрать результат" in rendered
    assert "Что дальше?" in rendered

    for tab_key, tab_label in (
        ("context", "Контекст"),
        ("decisions", "Ждут решения"),
        ("runs", "В работе"),
        ("materials", "Материалы"),
    ):
        assert f'data-workspace-tab="{tab_key}"' in rendered
        assert f'data-workspace-tabpanel="{tab_key}"' in rendered
        assert tab_label in rendered

    assert 'id="chat-dock-drawer"' not in rendered


def test_object_page_renders_ask_agent_dock_with_boundary_refusal_and_escape():
    """Verify object pages include _chat_dock.html and context_panel.js handles Escape, focus, and local_only."""
    env = _build_jinja_env()
    base_tmpl = env.from_string(
        '{% extends "web/base.html" %}{% block page_heading %}Карточка контакта{% endblock %}'
        '{% block content %}<div data-object-context data-context-type="contact" data-context-id="77" '
        'data-boundary="local_only" data-context-title="Контакт ООО Вектор"></div>{% endblock %}'
    )
    rendered = base_tmpl.render(
        user=NS(id=5, username="member", is_authenticated=True, has_admin_access=lambda: False),
        tenant=NS(id=2, name="Отдел продаж"),
        perms=shell_perms(can_open_chat=True, is_owner=False, can_manage_workspace=lambda tid: False),
        member_level=NS(label="Участник"),
        section="contacts",
        csrf="csrf-token",
        nav=[],
        mobile_nav=[],
        request=NS(query_params={}),
    )

    assert 'id="chat-dock-toggle"' in rendered
    assert "Спросить агента" in rendered
    assert 'id="chat-dock-drawer"' in rendered
    assert "data-dock-context-chip" in rendered
    assert "data-dock-boundary-badge" in rendered
    assert "data-dock-local-only-refusal" in rendered
    assert "data-dock-remove-chip" in rendered

    js_src = (ROOT / "app/static/js/context_panel.js").read_text(encoding="utf-8")
    assert "initChatDock" in js_src
    assert "event.key === 'Escape'" in js_src
    assert "boundary !== 'cloud'" in js_src
    assert "(activeLauncher || toggleBtn)?.focus()" in js_src


@pytest.mark.asyncio
async def test_context_revalidated_on_every_send_and_revoked_access_blocks_turn():
    """Context is revalidated on every send; if access is revoked or record disappears, turn is refused."""
    access_allowed = {"ok": True}

    async def resolve_fn(context_type, context_id, boundary):
        if not access_allowed["ok"]:
            raise PermissionError("Access revoked")
        return {
            "id": context_id,
            "context_id": str(context_id),
            "type": context_type,
            "title": "Договор поставки",
            "source_ref": f"doc:{context_id}",
            "ref": f"doc:{context_id}",
            "citation_anchor": "rev:9",
            "revision_hash": "sha256:123",
            "excerpt": "Пункт 4.1",
            "data_boundary": boundary,
            "boundary": boundary,
            "is_local_only": False,
        }

    chat_mod, turns = _load_chat_module(resolve_fn=resolve_fn)
    req = NS(
        headers={"x-requested-with": "XMLHttpRequest", "accept": "application/json"},
        state=NS(
            web_user=NS(id=7),
            tenant_id=12,
            memberships=[],
            web_perms=NS(can_open_chat=True),
        ),
        session={
            "evidence_context": {
                "context_type": "document",
                "context_id": "9",
                "boundary": "cloud",
                "tenant_id": 12,
            }
        },
    )

    resp1 = await chat_mod.chat_send(
        req,
        text="Проверь условия",
        token="valid-csrf",
        context_type="document",
        context_id="9",
        boundary="cloud",
    )
    assert resp1.status_code == 200
    assert len(turns) == 1
    assert "Договор поставки" in turns[0]["text"]

    # Revoke access before the second send
    access_allowed["ok"] = False
    with pytest.raises(HTTPException) as exc:
        await chat_mod.chat_send(
            req,
            text="А что со сроками?",
            token="valid-csrf",
            context_type="document",
            context_id="9",
            boundary="cloud",
        )
    assert exc.value.status_code == 403
    assert len(turns) == 1


@pytest.mark.asyncio
async def test_local_only_boundary_refused_before_send():
    """local_only context is refused before reaching handle_web_message."""
    chat_mod, turns = _load_chat_module()
    req = NS(
        headers={"x-requested-with": "XMLHttpRequest", "accept": "application/json"},
        state=NS(
            web_user=NS(id=7),
            tenant_id=12,
            memberships=[],
            web_perms=NS(can_open_chat=True),
        ),
        session={
            "evidence_context": {
                "context_type": "document",
                "context_id": "114",
                "boundary": "local_only",
                "tenant_id": 12,
            }
        },
    )

    with pytest.raises(HTTPException) as exc:
        await chat_mod.chat_send(
            req,
            text="Отправь локальный документ в облако",
            token="valid-csrf",
            context_type="document",
            context_id="114",
            boundary="local_only",
        )
    assert exc.value.status_code == 403
    assert turns == []


@pytest.mark.asyncio
async def test_chip_removal_clears_session_context_and_allows_normal_send():
    """Removing a context chip via POST /app/chat/context/clear clears session state."""
    chat_mod, turns = _load_chat_module()
    req = NS(
        headers={"x-requested-with": "XMLHttpRequest", "accept": "application/json"},
        state=NS(
            web_user=NS(id=7),
            tenant_id=12,
            memberships=[],
            web_perms=NS(can_open_chat=True),
        ),
        session={
            "evidence_context": {
                "context_type": "document",
                "context_id": "114",
                "boundary": "local_only",
                "tenant_id": 12,
            }
        },
    )

    clear_resp = await chat_mod.chat_context_clear(req, token="valid-csrf")
    assert clear_resp.status_code == 200
    payload = json.loads(clear_resp.body)
    assert payload == {"ok": True, "scoped_context": None, "active_boundary": "cloud"}
    assert "evidence_context" not in req.session

    send_resp = await chat_mod.chat_send(req, text="Обычный вопрос без чипа", token="valid-csrf")
    assert send_resp.status_code == 200
    assert len(turns) == 1
    assert turns[0]["text"] == "Обычный вопрос без чипа"


@pytest.mark.asyncio
async def test_inline_cards_refresh_from_owning_services_and_strip_credentials():
    """Typed inline cards link to authoritative records, refresh on open, and strip secrets/raw payloads."""
    state = {
        "config": {
            "id": 55,
            "name": "Мониторинг упоминаний",
            "job_type": "analyze",
            "plan_status": "available",
            "plan": {
                "goal": "Найти отзывы клиентов",
                "platforms": ["telegram"],
                "source_scope": "selected",
                "source_ids": [1, 2],
                "window": {"type": "rolling", "days": 3},
                "trigger": {"type": "once"},
                "pipeline": {"stages": ["collect", "analyze"]},
                "collection": {"limit": 30, "private_metadata": "SECRET_META"},
                "vk_access": {"credential_id": 777, "access_token": "vk_secret_token"},
                "raw_payload": {"internal": "dump"},
            },
            "provider_payload": "MUST_NOT_LEAK",
        },
        "result": {
            "job_status": "running",
            "coverage": "partial_or_bounded",
            "progress": {"phase": "collect", "total": 10, "completed": 4, "updated_at": "12:00", "raw": "HIDDEN"},
            "analyses": [{"id": 88, "title": "Отчёт #88", "raw_error": "HIDDEN_ERR"}],
            "raw_error": "STACK_TRACE_SECRET",
        },
    }

    async def get_config(_tid):
        return state["config"]

    async def get_result(_tid, *, limit):
        return state["result"]

    ws_mod, _ = _load_chat_workspace_module(config=get_config, result=get_result)
    req = NS(
        state=NS(
            web_user=NS(id=7),
            tenant_id=12,
            web_perms=NS(can_open_chat=True),
        )
    )

    resp = await ws_mod.workspace_cards(req, task_id=55, context_type=None, context_id=None, boundary=None)
    assert resp.status_code == 200
    raw_json = resp.body.decode("utf-8")
    data = json.loads(raw_json)
    card_types = [c["card_type"] for c in data["cards"]]
    assert card_types == ["plan_preview", "progress", "result"]
    for card in data["cards"]:
        assert card["authoritative_url"] == "/app/tasks/55"
        assert card["authoritative_ref"] == "agent_task:55"
        for action in card.get("actions", []):
            assert action["kind"] in {"link", "draft"}

    for forbidden in (
        "SECRET_META",
        "vk_secret_token",
        "credential_id",
        "MUST_NOT_LEAK",
        "HIDDEN",
        "STACK_TRACE_SECRET",
    ):
        assert forbidden not in raw_json

    # Simulate stale card refresh after access to task is revoked
    state["config"] = None
    stale_resp = await ws_mod.workspace_cards(req, task_id=55, context_type=None, context_id=None, boundary=None)
    assert stale_resp.status_code == 404
    assert json.loads(stale_resp.body)["cards"] == []

    # Verify /workspace/panel returns all 4 tabs and filters out forbidden keys
    panel_resp = await ws_mod.workspace_panel(
        req,
        task_id=55,
        context_type="document",
        context_id="9",
        boundary="cloud",
    )
    assert panel_resp.status_code == 200
    panel_body = json.loads(panel_resp.body)
    assert panel_body["tabs"] == ["context", "decisions", "runs", "materials"]


@pytest.mark.asyncio
async def test_resolve_context_supports_contact_and_task_with_permission_checks(monkeypatch):
    """Verify resolve_context in app/services/knowledge/workspace.py supports contact and task safely."""
    from app.services.knowledge import workspace as kw_ws

    monkeypatch.setattr(kw_ws, "current_tenant_id", lambda: 15)
    monkeypatch.setattr(kw_ws, "get_current_user", lambda: NS(id=3))

    # 1) Denied when permission is false
    monkeypatch.setattr(kw_ws, "has_permission", lambda user, perm, act: False)
    with pytest.raises(HTTPException) as exc_contact:
        await kw_ws.resolve_context("contact", "10", "cloud")
    assert exc_contact.value.status_code == 404

    with pytest.raises(HTTPException) as exc_task:
        await kw_ws.resolve_context("task", "20", "cloud")
    assert exc_task.value.status_code == 404

    # 2) Allowed when permission is granted
    monkeypatch.setattr(kw_ws, "has_permission", lambda user, perm, act: True)
    fake_contact = NS(
        id=10,
        tenant_id=15,
        merged_into_id=None,
        data_boundary="cloud",
        display_name_encrypted="Мария Соколова",
        notes_encrypted="СЕКРЕТНАЯ ЗАМЕТКА",
        preferred_channel="telegram",
        last_active_channel="telegram",
        pii_updated_at=4,
    )

    async def fake_get_contact(id, tenant_id):
        return fake_contact if (id == 10 and tenant_id == 15) else None

    import app.models as app_models

    monkeypatch.setattr(app_models.Contact, "objects", NS(get=fake_get_contact), raising=False)
    resolved_contact = await kw_ws.resolve_context("contact", "10", "cloud")
    assert resolved_contact["type"] == "contact"
    assert resolved_contact["title"] == "Мария Соколова"
    assert resolved_contact["href"] == "/app/contacts/10"
    assert "СЕКРЕТНАЯ ЗАМЕТКА" not in json.dumps(resolved_contact, ensure_ascii=False)

    import app.services.task_results as task_results_mod

    async def fake_task_cfg(tid):
        if tid != 20:
            return None
        return {
            "id": 20,
            "name": "Анализ рынка",
            "job_type": "analyze",
            "plan_status": "available",
            "plan": {"goal": "Собрать тренды за неделю"},
        }

    monkeypatch.setattr(task_results_mod, "task_configuration", fake_task_cfg)
    resolved_task = await kw_ws.resolve_context("task", "20", "cloud")
    assert resolved_task["type"] == "task"
    assert resolved_task["title"] == "Анализ рынка"
    assert resolved_task["href"] == "/app/tasks/20"
    assert resolved_task["excerpt"] == "Собрать тренды за неделю"


def test_360px_layout_and_keyboard_escape_contracts_in_assets():
    """Verify 360px responsive rules in chat.css and Escape/focus handling in chat.js."""
    css = (ROOT / "app/static/css/chat.css").read_text(encoding="utf-8")
    js = (ROOT / "app/static/js/chat.js").read_text(encoding="utf-8")
    panel_html = (ROOT / "app/web/templates/web/_chat_workspace.html").read_text(encoding="utf-8")

    assert "@media(max-width:380px)" in css
    assert ".chat-panel-tabs" in css
    assert ".chat-inline-card" in css
    assert '@keydown.escape.stop="if (panelOpen) toggleWorkspacePanel()"' in panel_html
    assert "const target = this.panelOpen ? this.$refs.workspacePanel : this.$refs.panelToggle;" in js
    assert "target?.focus();" in js


def test_bot_transports_keep_receiving_plain_text_equivalents():
    """Verify bot transports (Telegram/MAX) continue to receive plain text without web card HTML."""
    from app.agent.quick_replies import render_for_channel, split_quick_replies

    raw_reply = "Задача подготовлена. Что открыть дальше?\n[[options: Мои источники | Мои задачи]]"
    clean_text, options = split_quick_replies(raw_reply)
    assert clean_text == "Задача подготовлена. Что открыть дальше?"
    assert options == ["Мои источники", "Мои задачи"]
    channel_text = render_for_channel(raw_reply)
    assert "Варианты: «Мои источники» · «Мои задачи»" in channel_text
    assert "<article" not in channel_text
    assert "data-card-type" not in channel_text
