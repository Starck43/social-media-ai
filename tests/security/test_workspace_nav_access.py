"""Sections a member's access level cannot open are not offered and explain themselves.

The sidebar and the mobile bar list only sections the caller may view; a direct
visit to a hidden section answers 403 with the reason and the caller's level
instead of an empty page. Writes keep their own `guard_web` check.
"""

from __future__ import annotations

import re

from app.models import User
from app.types import WorkspaceRoleType
from app.web.nav import NAV_ITEMS, allows, section_of
from tests.web.test_web_settings import _cleanup, _client, _drop_workspaces, _invitee, _login, _register


def _sidebar_link(key_href: str) -> re.Pattern[str]:
    # The sidebar entry, not a content link that happens to share the href.
    return re.compile(rf'<a href="{re.escape(key_href)}"\s+class="block rounded-lg px-3 py-2')


class _Perms:
    def __init__(self, rights: set[tuple[str, str]]):
        self._rights = rights

    def can(self, model_name, action) -> bool:
        return (model_name, action) in self._rights


# ── pure rules ─────────────────────────────────────────────────────────────


def test_each_gated_section_names_its_view_right() -> None:
    required = {item.key: item.required_permission for item in NAV_ITEMS}
    assert required["sources"] == "collection.source.view"
    assert required["scenarios"] == "analysis.scenario.view"
    assert required["tasks"] == "scheduling.task.view"
    assert required["analytics"] == "analysis.result.view"
    assert required["digests"] == "notifications.digest_run.view"
    # Always reachable once signed in: the dashboard and personal settings.
    assert required["dashboard"] is None and required["settings"] is None


def test_section_of_matches_the_section_and_its_subpages_only() -> None:
    assert section_of("/app/tasks").key == "tasks"
    assert section_of("/app/tasks/12/edit").key == "tasks"
    assert section_of("/app/scenarios/new").key == "scenarios"
    assert section_of("/app/taskschedule") is None
    assert section_of("/app/") is None
    assert section_of("/app/settings") is None


def test_allows_fails_closed_without_perms() -> None:
    tasks = next(item for item in NAV_ITEMS if item.key == "tasks")
    dashboard = next(item for item in NAV_ITEMS if item.key == "dashboard")
    assert not allows(tasks, None)
    assert allows(dashboard, None)
    assert allows(tasks, _Perms({("scheduling.task", "view")}))
    assert not allows(tasks, _Perms({("collection.source", "view")}))


# ── flows ──────────────────────────────────────────────────────────────────


async def test_viewer_sees_only_viewable_sections_and_a_reason_on_direct_access() -> None:
    owner: User | None = None
    viewer: User | None = None
    tenant_id = None
    try:
        async with await _client() as c:
            owner, tenant_id = await _register(c, "NavOwn")
            page = await c.get("/app/settings")
            for href in ("/app/sources", "/app/scenarios", "/app/tasks", "/app/analytics", "/app/digests"):
                assert _sidebar_link(href).search(page.text), f"owner misses {href}"
            assert (await c.get("/app/scenarios")).status_code == 200
            viewer = await _invitee("NavViewer", WorkspaceRoleType.VIEWER, tenant_id)

        async with await _client() as c:
            await _login(c, viewer.username)
            page = await c.get("/app/settings")
            for href in ("/app/sources", "/app/analytics", "/app/digests"):
                assert _sidebar_link(href).search(page.text), f"viewer misses {href}"
            for href in ("/app/scenarios", "/app/tasks"):
                assert not _sidebar_link(href).search(page.text), f"viewer is offered {href}"

            for path in ("/app/scenarios", "/app/tasks", "/app/tasks/999999"):
                resp = await c.get(path)
                assert resp.status_code == 403, path
                assert "data-section-denied" in resp.text
                assert "Наблюдатель" in resp.text
            assert (await c.get("/app/sources")).status_code == 200
    finally:
        await _cleanup(owner, viewer)
        await _drop_workspaces(tenant_id)


async def test_a_role_change_opens_the_section() -> None:
    from app.models.managers.tenant_manager import tenant_users
    from app.models.tenancy.workspace_role import WorkspaceRole

    owner: User | None = None
    member: User | None = None
    tenant_id = None
    try:
        async with await _client() as c:
            owner, tenant_id = await _register(c, "NavPromo")
            member = await _invitee("NavPromoMember", WorkspaceRoleType.VIEWER, tenant_id)
        row = [m for m in await tenant_users.web_memberships_for_tenant(tenant_id) if m.user_id == member.id][0]

        async with await _client() as c:
            await _login(c, member.username)
            assert (await c.get("/app/tasks")).status_code == 403
            admin = await WorkspaceRole.objects.get(codename=WorkspaceRoleType.ADMIN.value)
            await tenant_users.update_by_id(row.id, role_id=admin.id)
            resp = await c.get("/app/tasks")
            assert resp.status_code == 200
            assert _sidebar_link("/app/tasks").search(resp.text)
    finally:
        await _cleanup(owner, member)
        await _drop_workspaces(tenant_id)
