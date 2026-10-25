"""The compact overview loads and offers only permitted workspace content."""

from __future__ import annotations

import pytest

from app.models import User
from app.types import PlatformRoleType, WorkspaceRoleType
from app.web.dashboard import _offers
from tests.web.test_web_settings import _cleanup, _client, _drop_workspaces, _invitee, _login, _register


class _Perms:
    def __init__(self, rights: set[tuple[str, str]]):
        self._rights = rights

    def can(self, model_name, action) -> bool:
        return (model_name, action) in self._rights


# ── pure rules ─────────────────────────────────────────────────────────────


def test_offers_fail_closed_without_perms() -> None:
    assert not any(_offers(None).values())


def test_a_call_to_create_needs_both_view_and_create() -> None:
    offer = _offers(_Perms({("agenttask", "create"), ("source", "view")}))
    assert offer["sources"] and not offer["add_source"]
    assert not offer["tasks"] and not offer["add_task"]

    offer = _offers(_Perms({("agenttask", "view"), ("agenttask", "create"), ("agentscenario", "view")}))
    assert offer["tasks"] and offer["add_task"]
    assert not offer["add_scenario"]


# ── flows ──────────────────────────────────────────────────────────────────


@pytest.mark.tenancy
async def test_owner_and_viewer_get_only_permitted_overview_content() -> None:
    owner: User | None = None
    viewer: User | None = None
    tenant_id = None
    try:
        async with await _client() as c:
            owner, tenant_id = await _register(c, "DashOwn")
            page = await c.get("/app/")
            assert page.status_code == 200
            for href in ("/app/sources", "/app/digests", "/app/chat"):
                assert f'href="{href}"' in page.text, f"owner misses {href}"
            assert ">Задачи</h2>" in page.text
            assert "data-home-next-step" in page.text
            assert "Быстрые действия" not in page.text
            assert "run-now" not in page.text
            assert "роль: Владелец" not in page.text
            assert "data-home-results" in page.text
            viewer = await _invitee("DashViewer", WorkspaceRoleType.VIEWER, tenant_id)

        async with await _client() as c:
            await _login(c, viewer.username)
            page = await c.get("/app/")
            assert page.status_code == 200
            # Nothing on the page leads to a section the viewer cannot open.
            for href in ("/app/tasks", "/app/scenarios", "/app/onboarding"):
                assert f'href="{href}' not in page.text, f"viewer is offered {href}"
            assert "run-now" not in page.text
            assert ">Задачи</h2>" not in page.text
            assert "Быстрые действия" not in page.text
            assert "data-home-next-step" not in page.text
            assert "data-home-results" in page.text
            assert "data-home-chat" in page.text
            assert "роль: Наблюдатель" not in page.text
    finally:
        await _cleanup(owner, viewer)
        await _drop_workspaces(tenant_id)
