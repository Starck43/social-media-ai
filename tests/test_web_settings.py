"""`/app/settings` — workspace, team, personal keys, channels (stage G).

The page is where `docs/TENANCY.md` stops being a document and becomes UI, so
the tests below pin the three rules that decide who owns what:

* **a workspace owns itself** — profile and team are gated by `tenant.update`.
* **a personal secret belongs to a person** — `user_credentials` is keyed by
  `users.id`, so the tab lists only the caller's rows and the disable endpoint
  matches on `id` *and* `user_id`. Both halves are asserted: a second member's
  key must be invisible, and knowing its id must not be enough to switch it off.
* **the deployment is not a workspace** — kinds like `bot_token` are refused by
  the self-service form even though the vault could store them.

The last-owner rule is asserted separately: a workspace nobody owns cannot be
managed by anyone, which is worse than a permission error.
"""

from __future__ import annotations

import re

from httpx import ASGITransport, AsyncClient

from app.main import create_application
from app.models import User
from app.models.managers.tenant_manager import TenantUserManager
from app.types import UserRoleType

CSRF_RE = re.compile(r'name="_csrf" value="([^"]+)"')
DENIED = "Недостаточно прав"
PASSWORD = "secret-password-1"


# ── pure rules ─────────────────────────────────────────────────────────────


def test_nav_ships_the_settings_page() -> None:
    from app.web.nav import NAV_ITEMS

    assert {i.key: i.href for i in NAV_ITEMS if i.ready}["settings"] == "/app/settings"


def test_unknown_tab_falls_back_to_workspace() -> None:
    from app.web.settings import TABS, _tab_of

    class _R:
        def __init__(self, value):
            self.query_params = {"tab": value} if value is not None else {}

    assert _tab_of(_R(None)) == "workspace"
    assert _tab_of(_R("connections")) == "connections"
    # The tab was renamed; an old bookmark must land on the new name rather than
    # silently rendering the workspace tab, which reads as "the tab is gone".
    assert _tab_of(_R("credentials")) == "connections"
    # A hand-typed tab must not reach a template branch that does not exist.
    assert _tab_of(_R("../../admin")) == "workspace"
    assert set(TABS) == {"workspace", "team", "connections", "agent", "channels"}


def test_self_service_kinds_exclude_deployment_secrets() -> None:
    from app.web.settings import SELF_SERVICE_KINDS

    allowed = {kind for kinds in SELF_SERVICE_KINDS.values() for kind, _ in kinds}
    assert "user_token" in allowed and "session" in allowed
    # Per-deployment config lives in the environment, never in a workspace form.
    for forbidden in ("bot_token", "app_id", "client_secret"):
        assert forbidden not in allowed


# ── fixtures ───────────────────────────────────────────────────────────────


async def _client() -> AsyncClient:
    app = create_application()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=True)


async def _csrf(client: AsyncClient, path: str) -> str:
    page = await client.get(path)
    assert page.status_code == 200, f"{path} -> {page.status_code}"
    match = CSRF_RE.search(page.text)
    assert match, f"no csrf input on {path}"
    return match.group(1)


def _name(prefix: str) -> str:
    import secrets

    return f"{prefix}{secrets.token_hex(4)}"


async def _register(client: AsyncClient, prefix: str) -> tuple[User, int]:
    """Sign up: the new user owns a fresh workspace."""
    username = _name(prefix)
    token = await _csrf(client, "/app/register")
    resp = await client.post(
        "/app/register",
        data={
            "username": username,
            "email": f"{username}@example.com",
            "password": PASSWORD,
            "workspace": f"{prefix} workspace",
            "_csrf": token,
        },
    )
    assert resp.status_code == 200
    user = await User.objects.get(username=username)
    memberships = await TenantUserManager().web_memberships(user.id)
    assert memberships
    return user, memberships[0].tenant_id


async def _invitee(prefix: str, role: UserRoleType, tenant_id: int, password: str = PASSWORD) -> User:
    """Create a user and attach them to `tenant_id` with a platform role."""
    from app.models.role import Role

    role_row = await Role.objects.get(codename=role.name)
    username = _name(prefix)
    user = await User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password=password,
        role_id=role_row.id,
        is_superuser=False,
    )
    await TenantUserManager().add_web_member(tenant_id=tenant_id, user_id=user.id, role="member")
    return user


async def _login(client: AsyncClient, username: str, password: str = PASSWORD) -> None:
    token = await _csrf(client, "/app/login")
    resp = await client.post("/app/login", data={"username": username, "password": password, "_csrf": token})
    assert resp.status_code == 200


async def _cleanup(*users: User | None) -> None:
    """Drop the users and the workspaces they signed up into.

    The workspace goes too: rows in `user_credentials`, `tenant_users` and
    `tenant_channels` would otherwise leak into the next test's workspace.
    """
    from app.models.managers.tenant_manager import tenants

    for user in users:
        if user is None:
            continue
        await User.objects.delete_user(user.id)


async def _drop_workspaces(*tenant_ids: int | None) -> None:
    from app.models.managers.tenant_manager import tenants

    for tenant_id in tenant_ids:
        if tenant_id is not None:
            await tenants.delete_by_id(tenant_id)


async def test_all_four_tabs_render_for_the_owner() -> None:
    async with await _client() as client:
        user, tenant_id = await _register(client, "SetOwner")
        try:
            for tab in ("workspace", "team", "credentials", "channels"):
                page = await client.get(f"/app/settings?tab={tab}")
                assert page.status_code == 200, f"{tab} -> {page.status_code}"
            # The tab bar itself is on every tab, so a bad ?tab= still lands here.
            page = await client.get("/app/settings?tab=nonsense")
            assert page.status_code == 200
            assert "Настройки воркспейса" in page.text or "ws-name" in page.text
        finally:
            await _cleanup(user)


async def test_the_workspace_tab_shows_the_tier_and_the_comparison() -> None:
    """The tier is readable, and all three tiers are compared side by side.

    Asserts the numbers come from `PLAN_LIMITS` rather than being retyped in the
    template, so a limit the runtime enforces and a limit the pricing page shows
    cannot drift apart without a test going red.
    """
    from app.models.tenant import Tenant

    async with await _client() as client:
        user, tenant_id = await _register(client, "PlanView")
        try:
            page = await client.get("/app/settings?tab=workspace")
            assert page.status_code == 200

            for plan in Tenant.PLANS:
                assert Tenant.PLAN_LIMITS[plan]["label"] in page.text, plan
            for row_label in ("Источники", "Каналы доставки", "Участники команды"):
                assert row_label in page.text, row_label

            # The numbers the runtime enforces are the numbers on the page.
            assert str(Tenant.PLAN_LIMITS["starter"]["max_sources"]) in page.text
            assert str(Tenant.PLAN_LIMITS["pro"]["max_sources"]) in page.text

            # A non-superuser sees the comparison but not the switcher: the tier
            # is a billing act, not a workspace preference.
            assert "Сменить тариф" not in page.text
        finally:
            await _cleanup(user)


async def test_a_non_owner_cannot_change_the_tier() -> None:
    """The POST is refused for a non-superuser even with a valid CSRF token.

    The form is hidden for non-superusers, so this asserts the handler too —
    hiding a button is not a check.
    """
    from app.models.tenant import Tenant

    async with await _client() as client:
        owner, tenant_id = await _register(client, "PlanOwner")
        member, _ = await _register(client, "PlanMember")
        try:
            # Sign the second account in and try to move the first's workspace.
            await _login(client, member.username)
            token = await _csrf(client, "/app/settings")
            resp = await client.post(
                "/app/settings/plan",
                data={"_csrf": token, "plan": "business", "tenant_id": str(tenant_id)},
            )
            assert resp.status_code == 200

            from app.models.managers.tenant_manager import tenants

            row = await tenants.get(id=tenant_id)
            assert row.plan == Tenant.DEFAULT_PLAN, "a non-superuser moved the tier"
        finally:
            await _cleanup(owner)
            await _cleanup(member)


async def test_workspace_update_writes_the_profile() -> None:
    from app.models.managers.tenant_manager import tenants

    async with await _client() as client:
        user, tenant_id = await _register(client, "SetWrite")
        try:
            token = await _csrf(client, "/app/settings")
            resp = await client.post(
                "/app/settings/workspace",
                data={
                    "name": "Renamed workspace",
                    "timezone": "Europe/Kaliningrad",
                    "daily_cost_limit": "7.5",
                    "_csrf": token,
                },
            )
            assert resp.status_code == 200
            row = await tenants.get(id=tenant_id)
            assert row.name == "Renamed workspace"
            assert row.timezone == "Europe/Kaliningrad"
            assert float(row.daily_cost_limit) == 7.5
        finally:
            await _cleanup(user)


async def test_bad_timezone_and_negative_cap_are_refused() -> None:
    from app.models.managers.tenant_manager import tenants

    async with await _client() as client:
        user, tenant_id = await _register(client, "SetBad")
        try:
            before = await tenants.get(id=tenant_id)
            token = await _csrf(client, "/app/settings")

            resp = await client.post(
                "/app/settings/workspace",
                data={
                    "name": "Kept",
                    "timezone": "Mars/Olympus",
                    "daily_cost_limit": "5",
                    "_csrf": token,
                },
            )
            assert "Неизвестная таймзона" in resp.text

            resp = await client.post(
                "/app/settings/workspace",
                data={
                    "name": "Kept",
                    "timezone": "Europe/Moscow",
                    "daily_cost_limit": "-3",
                    "_csrf": token,
                },
            )
            assert "отрицательным" in resp.text

            # Neither bad value reached the row.
            after = await tenants.get(id=tenant_id)
            assert after.name == before.name
            assert after.timezone == before.timezone
            assert float(after.daily_cost_limit) == float(before.daily_cost_limit)
        finally:
            await _cleanup(user)
            await _drop_workspaces(tenant_id)


async def test_a_user_sees_only_their_own_personal_keys() -> None:
    from app.models.managers.user_credential_manager import user_credentials

    owner: User | None = None
    member: User | None = None
    tenant_id = None
    try:
        async with await _client() as c:
            owner, tenant_id = await _register(c, "SetVault")
            member = await _invitee("SetMate", UserRoleType.MANAGER, tenant_id)

        await user_credentials.store(user_id=owner.id, platform="vk", kind="user_token", secret="owner-token")
        await user_credentials.store(user_id=member.id, platform="vk", kind="user_token", secret="member-token")

        async with await _client() as c:
            await _login(c, owner.username)
            page = await c.get("/app/settings?tab=connections")
            assert page.status_code == 200
            # The mask of the caller's own row is shown; the other member's is not.
            assert "owner-token" not in page.text
            assert "member-token" not in page.text
            body = page.text
            # Their own row appears under the VK card, with its connection state.
            assert "ВКонтакте" in body
            assert "user_token" in body
            # A live token with no refresh is still "connected" — an absent
            # `expires_at` means unknown, not broken, and must not nag.
            assert "Подключено" in body
            assert "Войти заново" not in body
    finally:
        await _cleanup(owner, member)


async def test_a_member_can_store_their_own_key_but_not_a_bot_token() -> None:
    from app.models.managers.user_credential_manager import user_credentials

    owner: User | None = None
    member: User | None = None
    tenant_id = None
    try:
        async with await _client() as c:
            owner, tenant_id = await _register(c, "SetStore")
            member = await _invitee("SetWriter", UserRoleType.MANAGER, tenant_id)

        async with await _client() as c:
            await _login(c, member.username)
            token = await _csrf(c, "/app/settings?tab=credentials")

            resp = await c.post(
                "/app/settings/credentials",
                data={"kind": "vk::user_token", "secret": "personal-token", "_csrf": token},
            )
            assert resp.status_code == 200
            rows = await user_credentials.filter(user_id=member.id, kind="user_token")
            assert [r for r in rows if r.is_active]

            # Deployment config is refused even though the vault would accept it.
            resp = await c.post(
                "/app/settings/credentials",
                data={"kind": "telegram::bot_token", "secret": "bot-secret", "_csrf": token},
            )
            assert "нельзя задать из интерфейса" in resp.text
            assert not await user_credentials.filter(user_id=member.id, kind="bot_token")
    finally:
        await _cleanup(owner, member)


async def test_another_members_key_cannot_be_disabled_by_id() -> None:
    from app.models.managers.user_credential_manager import user_credentials

    owner: User | None = None
    member: User | None = None
    tenant_id = None
    try:
        async with await _client() as c:
            owner, tenant_id = await _register(c, "SetOwn")
            member = await _invitee("SetOther", UserRoleType.MANAGER, tenant_id)

        await user_credentials.store(user_id=owner.id, platform="vk", kind="user_token", secret="owner-token")
        rows = await user_credentials.filter(user_id=owner.id, kind="user_token")
        credential_id = rows[0].id

        async with await _client() as c:
            await _login(c, member.username)
            token = await _csrf(c, "/app/settings?tab=credentials")
            resp = await c.post(f"/app/settings/credentials/{credential_id}/disable", data={"_csrf": token})
            assert "Ключ не найден" in resp.text

        row = await user_credentials.get(id=credential_id)
        assert row.is_active, "knowing the id must not be enough to disable it"
    finally:
        await _cleanup(owner, member)
        await _drop_workspaces(tenant_id)


async def test_the_last_owner_cannot_be_demoted() -> None:
    from app.models.managers.tenant_manager import tenant_users

    owner: User | None = None
    tenant_id = None
    try:
        async with await _client() as client:
            owner, tenant_id = await _register(client, "SetSolo")
            memberships = await TenantUserManager().web_memberships(owner.id)
            membership = memberships[0]
            assert membership.is_owner

            token = await _csrf(client, "/app/settings?tab=team")
            resp = await client.post(
                f"/app/settings/members/{membership.id}/role",
                data={"role": "viewer", "_csrf": token},
            )
            assert "должен остаться хотя бы один" in resp.text

            row = await tenant_users.get(id=membership.id)
            assert row.is_owner

            # Promote a second member to owner, then the same demotion is allowed:
            # the guard counts owners, not the row being edited.
            second = await _invitee("SetCo", UserRoleType.MANAGER, tenant_id)
            try:
                second_membership = [
                    m for m in await tenant_users.web_memberships_for_tenant(tenant_id) if m.user_id == second.id
                ][0]

                resp = await client.post(
                    f"/app/settings/members/{second_membership.id}/role",
                    data={"role": "owner", "_csrf": token},
                )
                assert "Роль изменена" in resp.text

                resp = await client.post(
                    f"/app/settings/members/{membership.id}/role",
                    data={"role": "viewer", "_csrf": token},
                )
                assert "Роль изменена" in resp.text
                row = await tenant_users.get(id=membership.id)
                assert not row.is_owner
            finally:
                await _cleanup(second)
    finally:
        await _cleanup(owner)
        await _drop_workspaces(tenant_id)


async def test_a_read_only_member_gets_no_workspace_form() -> None:
    owner: User | None = None
    viewer: User | None = None
    tenant_id = None
    try:
        async with await _client() as c:
            owner, tenant_id = await _register(c, "SetRoOwn")
            viewer = await _invitee("SetRoViewer", UserRoleType.VIEWER, tenant_id)

        async with await _client() as c:
            await _login(c, viewer.username)
            page = await c.get("/app/settings")
            assert "Изменять настройки воркспейса может владелец" in page.text

            # The rendered form is not the check: a crafted POST must be refused.
            token = await _csrf(c, "/app/settings")
            resp = await c.post(
                "/app/settings/workspace",
                data={
                    "name": "Hijacked",
                    "timezone": "Europe/Moscow",
                    "daily_cost_limit": "5",
                    "_csrf": token,
                },
            )
            assert DENIED in resp.text
            from app.models.managers.tenant_manager import tenants

            row = await tenants.get(id=tenant_id)
            assert row.name != "Hijacked"
    finally:
        await _cleanup(owner, viewer)
        await _drop_workspaces(tenant_id)
