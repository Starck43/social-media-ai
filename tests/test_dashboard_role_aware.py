"""The dashboard offers only what the member's access level can open.

Welcome steps, the tasks card, quick actions and recent-analysis links point at
sections behind a view right (and, for a call to create, a create right). A
member whose level cannot open the target is not led there; an owner keeps the
full dashboard.
"""

from __future__ import annotations

from app.models import User
from app.types import UserRoleType
from app.web.dashboard import _offers
from tests.test_web_settings import _cleanup, _client, _drop_workspaces, _invitee, _login, _register

QUICK_ACTION = 'class="block w-full rounded-lg border'


class _Perms:
    def __init__(self, rights: set[tuple[str, str]]):
        self._rights = rights

    def can(self, model_name, action) -> bool:
        return (model_name, action) in self._rights


def _quick_action(href: str) -> str:
    return f'<a href="{href}" {QUICK_ACTION}'


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


async def test_owner_gets_the_full_dashboard_and_a_viewer_only_what_they_can_open() -> None:
    owner: User | None = None
    viewer: User | None = None
    tenant_id = None
    try:
        async with await _client() as c:
            owner, tenant_id = await _register(c, "DashOwn")
            page = await c.get("/app/")
            assert page.status_code == 200
            for href in ("/app/onboarding", "/app/scenarios/new", "/app/digests"):
                assert f'href="{href}"' in page.text, f"owner misses {href}"
            assert ">Задачи</h2>" in page.text
            assert _quick_action("/app/tasks") in page.text
            assert _quick_action("/app/sources") in page.text
            viewer = await _invitee("DashViewer", UserRoleType.VIEWER, tenant_id)

        async with await _client() as c:
            await _login(c, viewer.username)
            page = await c.get("/app/")
            assert page.status_code == 200
            # Nothing on the page leads to a section the viewer cannot open.
            for href in ("/app/tasks", "/app/scenarios", "/app/onboarding"):
                assert f'href="{href}' not in page.text, f"viewer is offered {href}"
            assert "run-now" not in page.text
            assert ">Задачи</h2>" not in page.text
            assert _quick_action("/app/sources") in page.text
    finally:
        await _cleanup(owner, viewer)
        await _drop_workspaces(tenant_id)
