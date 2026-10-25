"""Invite links with a chosen access level (`/app/settings/invites`).

An owner picks the level a newcomer gets; the link carries a single-use code
whose redemption binds that level's `Role` to the new membership. The tests pin
who may issue, what may be issued, that the code is shown once, and that the
link survives a sign-in detour.
"""

from __future__ import annotations

import re
from urllib.parse import unquote

from app.models import User
from app.models.managers.tenant_manager import TenantUserManager
from app.services.tenancy import member_roles
from app.types import PlatformRoleType, WorkspaceRoleType
from tests.web.test_web_settings import (
    DENIED,
    PASSWORD,
    _cleanup,
    _client,
    _csrf,
    _drop_workspaces,
    _invitee,
    _login,
    _name,
    _register,
)

CODE_RE = re.compile(r"/app/invite\?code=([A-Z0-9-]+)")


# ── pure rules ─────────────────────────────────────────────────────────────


def test_the_owner_level_is_not_invitable_and_legacy_member_reads_as_viewer() -> None:
    assert "owner" not in {level.key for level in member_roles.INVITABLE}
    assert member_roles.resolve("owner", invitable_only=True) is None
    assert member_roles.resolve("owner").codename == "owner"
    assert member_roles.resolve(" Member ").key == "viewer"
    assert member_roles.resolve("root") is None
    assert member_roles.resolve(None) is None
    assert {level.codename for level in member_roles.INVITABLE} == {"admin", "manager", "analyst", "viewer"}


def test_key_of_reads_the_membership_role() -> None:
    class _Role:
        def __init__(self, codename):
            self.codename = codename

    class _M:
        def __init__(self, role_id, role):
            self.role_id, self.role = role_id, role

    assert member_roles.key_of(_M(None, None)) is None
    assert member_roles.key_of(_M(3, _Role(WorkspaceRoleType.ANALYST))) == "analyst"
    assert member_roles.key_of(_M(3, _Role(WorkspaceRoleType.MANAGER))) == "manager"
    assert member_roles.key_of(_M(3, None)) is None


def test_the_qr_is_an_inline_svg_of_the_link() -> None:
    from app.web.settings import invite_qr_svg

    svg = invite_qr_svg("http://testserver/app/invite?code=ABCD-1234")
    assert svg is None or svg.lstrip().startswith("<svg")


# ── flows ──────────────────────────────────────────────────────────────────


async def _issue(client, role: str, days: int = 7):
    token = await _csrf(client, "/app/settings?tab=team")
    response = await client.post(
        "/app/settings/invites",
        data={"role": role, "days": str(days), "_csrf": token},
        follow_redirects=False,
    )
    location = response.headers["location"]
    return await client.get("/app/settings/invites" if "invite_dialog=1" in location else location)


async def test_owner_issues_a_link_that_binds_the_chosen_level() -> None:
    owner: User | None = None
    guest: User | None = None
    tenant_id = None
    try:
        async with await _client() as c:
            owner, tenant_id = await _register(c, "InvOwn")
            resp = await _issue(c, "analyst")
            assert resp.status_code == 200
            match = CODE_RE.search(resp.text)
            assert match, "the issued link is shown in the on-demand invitation dialog"
            code = match.group(1)
            assert "Аналитик" in resp.text
            # Shown once: a reload no longer carries the plaintext code.
            again = await c.get("/app/settings/invites")
            assert code not in again.text

        async with await _client() as c:
            username = _name("InvGuest")
            from app.models import PlatformRole

            viewer_role = await PlatformRole.objects.get_by_codename(PlatformRoleType.USER)
            guest = await User.objects.create_user(
                username=username,
                email=f"{username}@example.com",
                password=PASSWORD,
                role_id=viewer_role.id,
            )
            # The link opened while signed out leads to login and back to it.
            first = await c.get(f"/app/invite?code={code}", follow_redirects=False)
            assert first.status_code in (302, 303, 307)
            assert unquote(first.headers["location"]).endswith(f"next=/app/invite?code={code}")

            await _login(c, username)
            page = await c.get(f"/app/invite?code={code}")
            assert f'value="{code}"' in page.text
            token = await _csrf(c, f"/app/invite?code={code}")
            joined = await c.post("/app/invite", data={"code": code, "_csrf": token})
            assert joined.status_code == 200

        membership = await TenantUserManager().get(tenant_id=tenant_id, user_id=guest.id)
        assert membership is not None and membership.is_active
        assert member_roles.key_of(membership) == "analyst"
        assert not membership.is_owner
    finally:
        await _cleanup(owner, guest)
        await _drop_workspaces(tenant_id)


async def test_owner_level_and_unlisted_days_are_refused() -> None:
    from app.models.tenant import TenantInvite

    owner: User | None = None
    tenant_id = None
    try:
        async with await _client() as c:
            owner, tenant_id = await _register(c, "InvBad")
            for role, days in (("owner", 7), ("root", 7), ("viewer", 365)):
                resp = await _issue(c, role, days)
                assert "Выберите уровень доступа" in resp.text
                assert not CODE_RE.search(resp.text)
            assert await TenantInvite.objects.filter(tenant_id=tenant_id) == []
    finally:
        await _cleanup(owner)
        await _drop_workspaces(tenant_id)


async def test_a_member_who_does_not_manage_the_workspace_cannot_invite() -> None:
    from app.models.tenant import TenantInvite

    owner: User | None = None
    viewer: User | None = None
    tenant_id = None
    try:
        async with await _client() as c:
            owner, tenant_id = await _register(c, "InvRoOwn")
            viewer = await _invitee("InvRoViewer", WorkspaceRoleType.VIEWER, tenant_id)

        async with await _client() as c:
            await _login(c, viewer.username)
            page = await c.get("/app/settings?tab=team")
            assert 'action="/app/settings/invites"' not in page.text
            resp = await _issue(c, "admin")
            assert DENIED in resp.text
            assert await TenantInvite.objects.filter(tenant_id=tenant_id) == []
    finally:
        await _cleanup(owner, viewer)
        await _drop_workspaces(tenant_id)


async def test_team_tab_shows_and_changes_the_member_level() -> None:
    from app.models.managers.tenant_manager import tenant_users

    owner: User | None = None
    member: User | None = None
    tenant_id = None
    try:
        async with await _client() as c:
            owner, tenant_id = await _register(c, "InvTeam")
            member = await _invitee("InvTeamMember", WorkspaceRoleType.VIEWER, tenant_id)
            row = [m for m in await tenant_users.web_memberships_for_tenant(tenant_id) if m.user_id == member.id][0]

            page = await c.get("/app/settings?tab=team")
            assert "Наблюдатель" in page.text and "Владелец" in page.text

            token = await _csrf(c, "/app/settings?tab=team")
            resp = await c.post(f"/app/settings/members/{row.id}/role", data={"role": "manager", "_csrf": token})
            assert "Роль изменена" in resp.text
            assert member_roles.key_of(await tenant_users.get(id=row.id)) == "manager"
            assert re.search(r'<option value="manager"\s+selected', resp.text)
    finally:
        await _cleanup(owner, member)
        await _drop_workspaces(tenant_id)
