"""Unit tests for P12-T06 Shared evidence drawer & /app/chat context handoff.

Covers:
- T06.1: Active P12 domain routers & nav verification (/app/knowledge, /app/documents, /app/communications, /app/followups, /app/cases)
- T06.2: Evidence drawer contract in base.html (#context-panel, data-evidence-*, data-chat-handoff, context_panel.js)
- T06.3: Bidirectional /app/chat context handoff via query parameters and local_only boundary isolation
"""

from __future__ import annotations

import json
import types
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from fastapi import APIRouter
from jinja2 import Environment, FileSystemLoader, select_autoescape

from tests.shared.evidence_fixtures import workspace_double
from tests.shared.template_shell import shell_perms
from tests.source_import_isolation import load_isolated_source

__source_only__ = True
ROOT = Path(__file__).resolve().parents[2]


def _build_test_jinja_env() -> Environment:
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


def _load_chat_web(fake_render=None):
    fake_deps = types.ModuleType("app.web.deps")
    fake_deps.render = fake_render or (lambda req, tmpl, **extra: NS(template=tmpl, context=extra, status_code=200))
    fake_deps.add_flash = lambda req, cat, msg: None
    fake_deps.ensure_csrf = lambda req, token: True
    fake_deps.render_section_denied = lambda req, sec: NS(status_code=403)

    fake_workspace = types.ModuleType("app.web.chat_workspace")
    fake_workspace.router = APIRouter()
    fake_workspace.selected_task = lambda req, tid: None
    fake_workspace.workspace_denied = lambda req: None

    fake_nav = types.ModuleType("app.web.nav")
    fake_nav.section_of = lambda path: "chat"

    async def fake_web_session_id(uid):
        return None

    fake_runtime = types.ModuleType("app.agent.runtime")
    fake_runtime.web_session_id = fake_web_session_id

    async def fake_resolve_policy(**kw):
        return NS(to_public_dict=lambda: {"model_name": "mock-model", "max_tokens": 4096})

    fake_att = types.ModuleType("app.services.chat_attachments")
    fake_att.resolve_workspace_attachment_policy = fake_resolve_policy

    return load_isolated_source(
        "_p12_t06_chat_web",
        ROOT / "app/web/chat.py",
        {
            "app.web.deps": fake_deps,
            "app.web.chat_workspace": fake_workspace,
            "app.web.nav": fake_nav,
            "app.agent.runtime": fake_runtime,
            "app.services.chat_attachments": fake_att,
            "app.services.knowledge.workspace": workspace_double(),
        },
    )


def test_p12_domain_routes_active_in_nav():
    """T06.1: All five P12 domain routes are active (ready=True) with live URLs in NAV_ITEMS."""
    nav_mod = load_isolated_source("_p12_t06_nav", ROOT / "app/web/nav.py")
    nav_map = {item.key: item for item in nav_mod.NAV_ITEMS}

    expected_p12_keys = ["knowledge", "documents", "communications", "followups", "cases"]
    for key in expected_p12_keys:
        assert key in nav_map, f"Missing {key} in NAV_ITEMS"
        item = nav_map[key]
        assert item.ready is True, f"NavItem {key} should have ready=True"
        assert item.href.startswith("/app/"), f"NavItem {key} should have live href, got {item.href}"
        assert item.required_permission is not None, f"NavItem {key} should specify required_permission"


def test_all_p12_routers_included_in_web_router():
    """T06.1: Verify web_router in app/web/__init__.py includes all five domain routers."""
    with open(ROOT / "app/web/__init__.py", "r", encoding="utf-8") as f:
        init_src = f.read()

    for router_name in [
        "knowledge_router",
        "documents_router",
        "communications_router",
        "followups_router",
        "cases_router",
    ]:
        assert router_name in init_src, f"{router_name} is not included in app/web/__init__.py"
        assert f"web_router.include_router({router_name})" in init_src, f"Missing include_router({router_name})"


def test_context_panel_in_base_template():
    """T06.2: Evidence drawer contract in base.html (#context-panel, data-context-panel, data-chat-handoff)."""
    with open(ROOT / "app/web/templates/web/base.html", "r", encoding="utf-8") as f:
        base_html = f.read()

    assert 'id="context-panel"' in base_html
    assert "data-context-panel" in base_html
    assert "data-evidence-title" in base_html
    assert "data-chat-handoff" in base_html
    assert "js/context_panel.js" in base_html


def test_context_panel_js_contract():
    """T06.2: app/static/js/context_panel.js handles data-evidence triggers and chat handoff URLs."""
    js_path = ROOT / "app/static/js/context_panel.js"
    assert js_path.exists(), "app/static/js/context_panel.js must exist"

    with open(js_path, "r", encoding="utf-8") as f:
        js_code = f.read()

    assert "data-evidence-trigger" in js_code
    assert "data-evidence-ref" in js_code
    assert "data-evidence-boundary" in js_code
    assert "data-chat-handoff" in js_code
    assert "/app/chat" in js_code
    assert "local_only" in js_code


@pytest.mark.asyncio
async def test_chat_page_scoped_context_from_query_params():
    """T06.3: /app/chat parses context_type, context_id, boundary, and builds scoped_context."""
    rendered_calls = []

    def fake_render(request, template_name, **extra):
        rendered_calls.append((template_name, extra))
        return NS(template=template_name, context=extra, status_code=200)

    chat_web = _load_chat_web(fake_render=fake_render)

    req = NS(
        state=NS(
            web_user=NS(id=1, email="test@example.com"),
            tenant_id=1,
            web_perms=NS(can_open_chat=True),
        ),
        headers={},
        query_params={},
        session={},
    )

    resp = await chat_web.chat_page(
        req,
        context_type="case",
        context_id="case-2026-042",
        boundary="company",
        context_title="Закупка полимерного сырья",
        context_ref="CASE-2026-042",
    )

    assert resp.status_code == 200
    assert len(rendered_calls) == 1
    tmpl, ctx = rendered_calls[0]
    assert tmpl == "web/chat.html"
    assert "scoped_context" in ctx
    scoped = ctx["scoped_context"]
    assert scoped is not None
    assert scoped["type"] == "case"
    assert scoped["id"] == "case-2026-042"
    assert scoped["title"] == "Stored test evidence"  # Server resolution, not the untrusted query label.
    assert scoped["boundary"] == "company"
    assert scoped["is_local_only"] is False


@pytest.mark.asyncio
async def test_chat_page_scoped_context_local_only_boundary():
    """T06.3: /app/chat sets is_local_only=True when boundary is local_only."""
    rendered_calls = []

    def fake_render(request, template_name, **extra):
        rendered_calls.append((template_name, extra))
        return NS(template=template_name, context=extra, status_code=200)

    chat_web = _load_chat_web(fake_render=fake_render)

    req = NS(
        state=NS(
            web_user=NS(id=1, email="test@example.com"),
            tenant_id=1,
            web_perms=NS(can_open_chat=True),
        ),
        headers={},
        query_params={},
        session={},
    )

    resp = await chat_web.chat_page(
        req,
        context_type="document",
        context_id="doc-114",
        boundary="local_only",
        context_title="Рамочный договор ДП-2026/114",
        context_ref="sha256:9c4a18f2",
    )

    assert resp.status_code == 200
    _, ctx = rendered_calls[0]
    scoped = ctx["scoped_context"]
    assert scoped is not None
    assert scoped["is_local_only"] is True
    assert scoped["boundary"] == "local_only"


@pytest.mark.asyncio
async def test_chat_page_ajax_returns_scoped_context():
    """T06.3: AJAX request to /app/chat returns scoped_context in JSON."""
    chat_web = _load_chat_web()

    req = NS(
        state=NS(
            web_user=NS(id=1, email="test@example.com"),
            tenant_id=1,
            web_perms=NS(can_open_chat=True),
        ),
        headers={"x-requested-with": "XMLHttpRequest"},
        query_params={},
        session={},
    )

    resp = await chat_web.chat_page(
        req,
        context_type="communication",
        context_id="out-101",
        boundary="cloud",
        context_title="Письмо ООО «ТехПолимер»",
        context_ref="UID 9817",
    )

    data = json.loads(resp.body.decode("utf-8"))
    assert "scoped_context" in data
    assert data["scoped_context"]["id"] == "out-101"
    assert data["scoped_context"]["boundary"] == "cloud"
    assert data["scoped_context"]["is_local_only"] is False


def test_chat_template_renders_scoped_context_banner():
    """T06.3: web/chat.html renders scoped context banner and local_only badge."""
    env = _build_test_jinja_env()
    template = env.get_template("web/chat.html")

    perms_mock = shell_perms(
        can_open_chat=True,
        is_owner=True,
        can_manage_workspace=lambda tid: False,
    )

    context = {
        "user": NS(id=1, username="testuser", is_authenticated=True, has_admin_access=lambda: False),
        "tenant": NS(id=1, name="Основное пространство"),
        "perms": perms_mock,
        "member_level": NS(label="Владелец"),
        "section": "chat",
        "messages": [],
        "is_owner": True,
        "awaiting_confirmation": False,
        "attachment_policy": None,
        "scoped_context": {
            "type": "document",
            "id": "doc-114",
            "boundary": "local_only",
            "title": "Рамочный договор ДП-2026/114",
            "ref": "sha256:9c4a18f2",
            "is_local_only": True,
        },
        "csrf": "test-csrf-token",
        "nav": [],
        "request": NS(query_params={}),
    }

    rendered = template.render(**context)
    assert "data-chat-scoped-context" in rendered
    assert "Рамочный договор ДП-2026/114" in rendered
    assert "local_only: отправка в чат заблокирована" in rendered
    assert "data-local-only-badge" in rendered
    assert "Снять контекст" in rendered
