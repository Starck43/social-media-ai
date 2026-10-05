"""Scenario wizard `/app/scenarios/new` and preview (docs/design/ui.md §4.5).

The wizard is the JSON-free way to create a scenario: the user ticks analysis
and content types, the scope is generated for them, and the preview shows the
final prompt before saving. These tests pin that contract.
"""

from __future__ import annotations

import json
import re

from tests.test_web_permissions import (  # noqa: F401 — shared web harness
    CSRF_RE,
    DENIED,
    AsyncClient,
    User,
    UserRoleType,
    _client,
    _csrf,
    _drop,
    _invitee,
    _login,
    _name,
    _register,
)


async def _scenario(tenant_id: int, name: str, **overrides) -> int:
    """Create a scenario owned by `tenant_id`, overriding any column by keyword."""
    from app.models import AgentScenario

    fields: dict = {"tenant_id": tenant_id, "name": name, "description": "created by tests", "is_active": True}
    fields.update(overrides)
    row = await AgentScenario.objects.create(**fields)
    return row.id


# ── reading the wizard ──────────────────────────────────────────────────────


async def test_wizard_new_page_renders_all_four_steps() -> None:
    async with await _client() as client:
        user, tenant_id = await _register(client, "WizNew")
        try:
            page = await client.get("/app/scenarios/new")
            assert page.status_code == 200
            for label in ("Интерес", "Промпт", "Модели", "Предпросмотр"):
                assert label in page.text
            assert 'name="content_types"' in page.text
            assert 'name="analysis_types"' in page.text
            assert 'name="scope"' in page.text
            assert 'name="base_prompt"' in page.text
            assert 'id="prompt-description"' in page.text, "the LLM assist box must render"
            assert 'name="media_overrides_image"' in page.text
            assert 'name="summary_prompt"' in page.text
        finally:
            await _drop(user, tenant_id)


async def test_wizard_create_persists_scenario() -> None:
    from app.core.tenant_context import tenant_scope
    from app.models import AgentScenario

    async with await _client() as client:
        user, tenant_id = await _register(client, "WizCreate")
        try:
            page = await client.get("/app/scenarios/new")
            form = {
                "_csrf": CSRF_RE.search(page.text).group(1),
                "name": _name("wizard-created"),
                "content_types": ["posts", "comments"],
                "analysis_types": ["sentiment", "keywords"],
                "analyze_type": "days",
                "base_prompt": "Проанализируй {text} из {platform}",
            }
            resp = await client.post("/app/scenarios/new", data=form)
            assert resp.status_code == 200
            assert "создан" in resp.text.lower()

            with tenant_scope(tenant_id):
                row = await AgentScenario.objects.filter(name=form["name"], tenant_id=tenant_id).first()
            assert row is not None
            assert row.content_types == ["posts", "comments"]
            assert row.analysis_types == ["sentiment", "keywords"]
            assert row.analyze_type and row.analyze_type.db_value == "days"
            # The scope was generated server-side: each selected type got its defaults.
            assert "sentiment" in row.scope
            assert "keywords" in row.scope
            assert "max_keywords" in row.scope["keywords"]
            assert row.base_prompt == "Проанализируй {text} из {platform}"
        finally:
            await _drop(user, tenant_id)


async def test_preview_returns_final_prompt_fragment() -> None:
    async with await _client() as client:
        user, tenant_id = await _register(client, "WizPrev")
        try:
            page = await client.get("/app/scenarios/new")
            csrf = CSRF_RE.search(page.text).group(1)
            form = {
                "name": "p",
                "content_types": ["posts"],
                "analysis_types": ["sentiment", "keywords"],
                "analyze_type": "themes",
                "base_prompt": "Тональность из {platform}",
                "_csrf": csrf,
            }
            resp = await client.post("/app/scenarios/preview", data=form)
            assert resp.status_code == 200
            assert "analysis_title" in resp.text, "COMMON_FIELDS must show in the preview"
            assert "sentiment_score" in resp.text, "analysis-type schema must show in the preview"
            assert "Тональность из" in resp.text, "the base prompt must be visible"
        finally:
            await _drop(user, tenant_id)


async def test_edit_wizard_preserves_analysis_types_and_scope() -> None:
    from app.core.tenant_context import tenant_scope
    from app.models import AgentScenario

    async with await _client() as client:
        user, tenant_id = await _register(client, "WizEdit")
        scenario_id = await _scenario(
            tenant_id,
            _name("keep"),
            analysis_types=["sentiment"],
            content_types=["posts"],
            scope={"sentiment": {"categories": ["Позитивный", "Негативный"]}, "brand": "Acme"},
        )
        try:
            page = await client.get(f"/app/scenarios/{scenario_id}")
            form = {
                m.group(1): m.group(2) or ""
                for m in __import__("re").finditer(r'<input[^>]*name="([a-z_0-9]+)"[^>]*value="([^"]*)"', page.text)
            }
            for m in __import__("re").finditer(
                r'<textarea[^>]*name="([a-z_0-9]+)"[^>]*>(.*?)</textarea>', page.text, __import__("re").S
            ):
                form[m.group(1)] = m.group(2).strip()
            form["_csrf"] = CSRF_RE.search(page.text).group(1)
            form["analysis_types"] = "sentiment"
            form["content_types"] = "posts"
            form["is_active"] = "on"

            resp = await client.post(f"/app/scenarios/{scenario_id}", data=form)
            assert resp.status_code == 200
            assert "сохран" in resp.text.lower()

            with tenant_scope(tenant_id):
                row = await AgentScenario.objects.get(id=scenario_id, tenant_id=tenant_id)
            assert row.analysis_types == ["sentiment"], "analysis types must survive an edit"
            assert "sentiment" in (row.scope or {}), "analysis config must survive an edit"
            assert (row.scope or {}).get("brand") == "Acme", "custom scope keys must survive an edit"
            assert row.is_active is True
        finally:
            await _drop(user, tenant_id)


async def test_wizard_requires_create_permission() -> None:
    owner = member = None
    tenant_id = None
    try:
        async with await _client() as c:
            owner, tenant_id = await _register(c, "WizOwner")
            member = await _invitee("WizViewer", UserRoleType.VIEWER, tenant_id, "secret-password-1")

        async with await _client() as m:
            await _login(m, member.username)
            # The create page is gated: a viewer must not get the form.
            page = await m.get("/app/scenarios/new")
            assert 'name="content_types"' not in page.text

            form = {"name": "sneaky", "analysis_types": ["sentiment"], "_csrf": CSRF_RE.search(page.text).group(1)}
            resp = await m.post("/app/scenarios/new", data=form)

            from app.core.tenant_context import tenant_scope
            from app.models import AgentScenario

            with tenant_scope(tenant_id):
                assert await AgentScenario.objects.filter(name="sneaky").count() == 0
    finally:
        await _drop(member, tenant_id)
        await _drop(owner, tenant_id)


async def test_wizard_create_persists_media_overrides() -> None:
    from app.core.tenant_context import tenant_scope
    from app.models import AgentScenario

    async with await _client() as client:
        user, tenant_id = await _register(client, "WizMedia")
        try:
            page = await client.get("/app/scenarios/new")
            form = {
                "_csrf": CSRF_RE.search(page.text).group(1),
                "name": _name("wizard-media"),
                "content_types": ["posts", "videos"],
                "analysis_types": ["sentiment"],
                "media_overrides_image": "Картинки {count}",
                "summary_prompt": "Резюме",
                "base_prompt": "Базовый {platform}",
            }
            resp = await client.post("/app/scenarios/new", data=form)
            assert resp.status_code == 200
            assert "создан" in resp.text.lower()

            with tenant_scope(tenant_id):
                row = await AgentScenario.objects.filter(name=form["name"], tenant_id=tenant_id).first()
            assert row is not None
            assert row.base_prompt == "Базовый {platform}"
            assert row.media_overrides == {"image": "Картинки {count}"}
            assert row.summary_prompt == "Резюме"
        finally:
            await _drop(user, tenant_id)


async def test_suggest_prompt_falls_back_when_no_model_answers() -> None:
    """With no LLM model the LLM assist must answer 'fallback' instead of blocking."""
    async with await _client() as client:
        user, tenant_id = await _register(client, "WizSuggest")
        try:
            page = await client.get("/app/scenarios/new")
            csrf = CSRF_RE.search(page.text).group(1)
            resp = await client.post(
                "/app/scenarios/suggest-prompt",
                data={"description": "следить за брендом", "_csrf": csrf},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body.get("fallback") is True, "with no LLM model the wizard must not block"
        finally:
            await _drop(user, tenant_id)


async def test_wizard_starts_from_remembered_preferences() -> None:
    """A second scenario opens with the choices the owner already made.

    The grouping mode and the brand list are the two things re-typed every
    time; both come from `agent_memory(scope=scenario_prefs)`, which the chat
    tools and the wizard's own save write.
    """
    from app.core.tenant_context import tenant_scope
    from app.services.ai import scenario_prefs

    async with await _client() as client:
        user, tenant_id = await _register(client, "WizPrefs")
        try:
            with tenant_scope(tenant_id):
                await scenario_prefs.remember(preferred_analyze_type="days", brands=["Fanta"])

            page = await client.get("/app/scenarios/new")
            assert page.status_code == 200
            # The template wraps the option tag, so match the tag and its
            # attributes rather than one line of it.
            assert any(
                'value="days"' in tag and "selected" in tag for tag in re.findall(r"<option[^>]*>", page.text)
            ), "the remembered grouping mode must be preselected"
            assert "Fanta" in page.text, "the remembered brand list must be pre-filled"
        finally:
            with tenant_scope(bypass=True):
                from app.models.managers.agent_memory_manager import agent_memory

                await agent_memory.filter(scope=scenario_prefs.SCOPE).delete()
            await _drop(user, tenant_id)
