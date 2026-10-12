"""Open invites on the team tab and their revocation (`/app/settings/invites/{id}/revoke`).

Someone who manages the workspace sees every invite that can still be redeemed
— its level, when it was issued, until when it works and how many uses are
left — and can revoke one; a revoked code no longer joins anybody. Members who
do not manage the workspace see no list and cannot revoke.
"""

from __future__ import annotations

import re

from app.models import User
from app.models.managers.tenant_manager import TenantInviteManager, TenantUserManager
from app.types import UserRoleType
from tests.test_web_settings import (
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
REVOKE_RE = re.compile(r'action="/app/settings/invites/(\d+)/revoke"')


async def _issue(client, role: str, days: int = 7) -> str:
    token = await _csrf(client, "/app/settings?tab=team")
    resp = await client.post("/app/settings/invites", data={"role": role, "days": str(days), "_csrf": token})
    match = CODE_RE.search(resp.text)
    assert match, "the issued link is shown on the team tab"
    return match.group(1)


async def _revoke(client, invite_id: int):
    token = await _csrf(client, "/app/settings?tab=team")
    return await client.post(f"/app/settings/invites/{invite_id}/revoke", data={"_csrf": token})


async def _new_user(prefix: str) -> User:
    from app.models.role import Role

    username = _name(prefix)
    viewer_role = await Role.objects.get(codename="VIEWER")
    return await User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password=PASSWORD,
        role_id=viewer_role.id,
        is_superuser=False,
    )


async def test_owner_sees_open_invites_and_a_revoked_code_no_longer_joins() -> None:
    owner: User | None = None
    guest: User | None = None
    tenant_id = None
    try:
        async with await _client() as c:
            owner, tenant_id = await _register(c, "InvLsOwn")
            empty = await c.get("/app/settings?tab=team")
            assert "data-open-invites" not in empty.text

            code = await _issue(c, "analyst", days=1)
            await _issue(c, "manager")
            page = await c.get("/app/settings?tab=team")
            assert "Активные приглашения" in page.text
            ids = [int(i) for i in REVOKE_RE.findall(page.text)]
            assert len(ids) == 2
            assert "Аналитик" in page.text and "Менеджер" in page.text
            assert "0 из 1" in page.text
            # The plaintext code is never part of the list.
            assert code not in page.text

            open_rows = await TenantInviteManager().open_for_tenant(tenant_id)
            analyst_id = [row.id for row in open_rows if row.id in ids][-1]
            resp = await _revoke(c, analyst_id)
            assert "Приглашение отозвано" in resp.text
            assert len(REVOKE_RE.findall(resp.text)) == 1

            again = await _revoke(c, analyst_id)
            assert "Приглашение не найдено или уже недействительно" in again.text

        async with await _client() as c:
            guest = await _new_user("InvLsGuest")
            await _login(c, guest.username)
            token = await _csrf(c, f"/app/invite?code={code}")
            joined = await c.post("/app/invite", data={"code": code, "_csrf": token})
            assert joined.status_code == 400
            assert "Код не найден, истёк или уже использован" in joined.text

        assert await TenantUserManager().get(tenant_id=tenant_id, user_id=guest.id) is None
    finally:
        await _cleanup(owner, guest)
        await _drop_workspaces(tenant_id)


async def test_a_used_invite_leaves_the_list() -> None:
    owner: User | None = None
    guest: User | None = None
    tenant_id = None
    try:
        async with await _client() as c:
            owner, tenant_id = await _register(c, "InvUsedOwn")
            code = await _issue(c, "viewer")

        async with await _client() as c:
            guest = await _new_user("InvUsedGuest")
            await _login(c, guest.username)
            token = await _csrf(c, f"/app/invite?code={code}")
            joined = await c.post("/app/invite", data={"code": code, "_csrf": token})
            assert joined.status_code == 200

        assert await TenantInviteManager().open_for_tenant(tenant_id) == []
    finally:
        await _cleanup(owner, guest)
        await _drop_workspaces(tenant_id)


async def test_an_invite_of_another_workspace_is_not_revoked() -> None:
    first: User | None = None
    second: User | None = None
    first_tenant = second_tenant = None
    try:
        async with await _client() as c:
            first, first_tenant = await _register(c, "InvForA")
            await _issue(c, "viewer")
        foreign_id = (await TenantInviteManager().open_for_tenant(first_tenant))[0].id

        async with await _client() as c:
            second, second_tenant = await _register(c, "InvForB")
            resp = await _revoke(c, foreign_id)
            assert "Приглашение не найдено или уже недействительно" in resp.text

        assert [row.id for row in await TenantInviteManager().open_for_tenant(first_tenant)] == [foreign_id]
    finally:
        await _cleanup(first, second)
        await _drop_workspaces(first_tenant, second_tenant)


async def test_a_member_who_does_not_manage_the_workspace_sees_no_list_and_cannot_revoke() -> None:
    owner: User | None = None
    viewer: User | None = None
    tenant_id = None
    try:
        async with await _client() as c:
            owner, tenant_id = await _register(c, "InvLsRoOwn")
            await _issue(c, "viewer")
            viewer = await _invitee("InvLsRoViewer", UserRoleType.VIEWER, tenant_id)
        invite_id = (await TenantInviteManager().open_for_tenant(tenant_id))[0].id

        async with await _client() as c:
            await _login(c, viewer.username)
            page = await c.get("/app/settings?tab=team")
            assert "data-open-invites" not in page.text
            assert not REVOKE_RE.search(page.text)
            resp = await _revoke(c, invite_id)
            assert DENIED in resp.text

        assert [row.id for row in await TenantInviteManager().open_for_tenant(tenant_id)] == [invite_id]
    finally:
        await _cleanup(owner, viewer)
        await _drop_workspaces(tenant_id)
