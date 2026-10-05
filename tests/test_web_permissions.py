"""Rights on the client UI: the workspace owner or the platform role — nobody else.

`/app` is a first-class surface, so it answers "may this caller do this" the
same way the sqladmin console and `/api` do (`User.has_perm_for` over the
structured `permissions.model_type_id` + `action_type`), with the one product
rule: whoever owns a workspace may configure it whatever their platform role
is. Hiding a button is not a check — every mutation is gated server-side by
`guard_web`, which the second half of this file proves by posting straight past
the hidden form.

The rule itself lives in `app/web/perms.py`; the first half pins it as a plain
unit, the second half drives it through the ASGI stack so the middleware
(membership + eager `role.permissions`), the flash and the template are
exercised the way users hit them.
"""

from __future__ import annotations

import re
import secrets

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import AgentTask, Platform, Role, Source, User
from app.models.managers.tenant_manager import TenantUserManager, tenants
from app.types import ActionType, UserRoleType
from app.web.perms import WebPerms

CSRF_RE = re.compile(r'name="_csrf" value="([^"]+)"')
DENIED = "Недостаточно прав для этого действия"
# The toolbar button is the gated one; the same words sit in an always-present
# modal heading, so the marker has to be the opener, not the label.
CREATE_BUTTON = '@click="openAdd = true"'


# ── the rule, as a unit ────────────────────────────────────────────────────


class _StubUser:
    """Just enough of `User` for the rule: the superuser flag and the predicate."""

    def __init__(self, *, superuser: bool = False, rights: set[tuple[str, ActionType]] | None = None) -> None:
        self.is_superuser = superuser
        self._rights = rights or set()

    def has_perm_for(self, model_name: str, action: ActionType) -> bool:
        return (model_name.lower(), action) in self._rights

    def _is_superuser_role(self) -> bool:
        # The flag half of `User._is_superuser_role`; the SUPERUSER-role half is
        # the real User's contract (tests/test_permissions.py) and needs a db row.
        return self.is_superuser


class _StubMembership:
    def __init__(self, tenant_id: int, role: str) -> None:
        self.tenant_id = tenant_id
        self.role = role


def _perms(user, *, role: str = "member", same_tenant: bool = True) -> WebPerms:
    memberships = [_StubMembership(1 if same_tenant else 999, role)] if user is not None else []
    return WebPerms(user, memberships, 1)


def test_section_gate_asks_who_the_user_is_and_fails_closed() -> None:
    # Whole-section gates (the job queue) ask *who* the caller is, not *what*
    # they may do — owning a workspace is not an operator role.
    assert not _perms(None).is_superuser_role
    assert not _perms(_StubUser()).is_superuser_role
    assert not _perms(_StubUser(), role="owner").is_superuser_role
    assert _perms(_StubUser(superuser=True)).is_superuser_role


def test_workspace_owner_writes_his_own_workspace_regardless_of_platform_role() -> None:
    # The documented rule: the owner of a workspace may always configure it.
    viewer = _StubUser(rights={("source", ActionType.VIEW)})
    perms = _perms(viewer, role="owner")
    assert perms.is_owner
    assert perms.can("source", ActionType.CREATE)
    assert perms.can("agenttask", ActionType.DELETE)


def test_superuser_writes_everywhere() -> None:
    perms = _perms(_StubUser(superuser=True), role="member")
    assert perms.can("source", "create")


def test_plain_member_without_platform_rights_is_read_only() -> None:
    viewer = _StubUser(rights={("source", ActionType.VIEW)})
    perms = _perms(viewer, role="member")
    assert not perms.is_owner
    assert perms.can("source", ActionType.VIEW)
    assert not perms.can("source", ActionType.CREATE)
    assert not perms.can("agenttask", ActionType.CREATE)


def test_member_gets_rights_only_on_the_models_his_role_grants() -> None:
    manager = _StubUser(rights={("source", ActionType.CREATE), ("source", ActionType.UPDATE)})
    perms = _perms(manager, role="member")
    assert perms.can("source", ActionType.CREATE)
    # ...and only there: the seeded MANAGER matrix has no `agenttask` rows.
    assert not perms.can("agenttask", ActionType.CREATE)


def test_membership_in_another_workspace_grants_nothing_here() -> None:
    viewer = _StubUser(rights=set())
    perms = _perms(viewer, role="owner", same_tenant=False)
    assert not perms.is_owner
    assert not perms.can("source", ActionType.CREATE)


def test_anonymous_may_do_nothing() -> None:
    perms = WebPerms(None, [], 1)
    assert not perms.can("source", ActionType.VIEW)
    assert not perms.can_any("source", ActionType.VIEW, ActionType.CREATE)


def test_actions_accept_enum_db_value_and_name() -> None:
    # The same right, asked for three ways, must reach the same predicate.
    member = _perms(_StubUser(rights={("source", ActionType.UPDATE)}), role="member")
    assert member.can("source", ActionType.UPDATE)
    assert member.can("source", "update")
    assert member.can("source", "UPDATE")
    assert not member.can("source", "not-an-action")


# ── through the ASGI stack ─────────────────────────────────────────────────


async def _client() -> AsyncClient:
    app = create_application()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=True)


async def _csrf(client: AsyncClient, path: str) -> str:
    page = await client.get(path)
    assert page.status_code == 200
    match = CSRF_RE.search(page.text)
    assert match, f"no csrf input on {path}"
    return match.group(1)


def _name(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(4)}"


async def _register(client: AsyncClient, prefix: str) -> tuple[User, int]:
    """Sign up: the new user owns a fresh workspace (membership role `owner`)."""
    username = _name(prefix)
    token = await _csrf(client, "/app/register")
    resp = await client.post(
        "/app/register",
        data={
            "username": username,
            "email": f"{username}@example.test",
            "password": "secret-password-1",
            "workspace": f"{prefix} workspace",
            "_csrf": token,
        },
    )
    assert resp.status_code == 200
    user = await User.objects.get(username=username)
    memberships = await TenantUserManager().web_memberships(user.id)
    assert user is not None and memberships
    return user, memberships[0].tenant_id


async def _login(client: AsyncClient, username: str, password: str = "secret-password-1") -> None:
    token = await _csrf(client, "/app/login")
    resp = await client.post("/app/login", data={"username": username, "password": password, "_csrf": token})
    assert resp.status_code == 200


async def _loaded(user: User) -> User:
    """The user as the middleware hands them over: role and permissions eager."""
    return await User.objects.prefetch_related("role.permissions").get(id=user.id)


async def _platform_value() -> str:
    platform = await Platform.objects.filter(is_active=True).first()
    assert platform is not None, "the test database seeds platforms"
    return platform.platform_type.db_value


async def _drop(user: User | None, tenant_id: int | None) -> None:
    if user is not None:
        await User.objects.delete_user(user.id)
    if tenant_id is not None:
        await tenants.delete_by_id(tenant_id)


async def _invitee(prefix: str, platform_role: UserRoleType, tenant_id: int, password: str) -> User:
    """A second web user in someone else's workspace with a *non*-owner membership,
    so the ownership branch of the rule cannot rescue them."""
    username = _name(prefix)
    role = await Role.objects.get(codename=platform_role.name)
    assert role is not None, f"role {platform_role.name} is seeded"
    user = await User.objects.create_user(
        username=username,
        email=f"{username}@example.test",
        password=password,
        role_id=role.id,
        is_superuser=False,
    )
    await TenantUserManager().add_web_member(tenant_id=tenant_id, user_id=user.id, role="member")
    return user


async def test_owner_may_create_a_task_and_sees_the_button() -> None:
    async with await _client() as client:
        user, tenant_id = await _register(client, "OwnerTask")
        name = _name("owner-task")
        task_id = None
        try:
            page = await client.get("/app/tasks")
            assert CREATE_BUTTON in page.text

            token = CSRF_RE.search(page.text).group(1)
            resp = await client.post(
                "/app/tasks",
                data={"name": name, "job_type": "collect", "cron_custom": "@once", "start_date": "2026-09-01", "_csrf": token},
            )
            assert resp.status_code == 200
            assert DENIED not in resp.text

            with tenant_scope(bypass=True):
                task = await AgentTask.objects.filter(name=name).first()
            assert task is not None
            task_id = task.id
        finally:
            if task_id is not None:
                with tenant_scope(bypass=True):
                    await AgentTask.objects.delete(id=task_id)
            await _drop(user, tenant_id)


async def test_owner_may_create_a_source() -> None:
    async with await _client() as client:
        user, tenant_id = await _register(client, "OwnerSource")
        external_id = secrets.token_hex(6)
        source_id = None
        try:
            token = await _csrf(client, "/app/sources")
            resp = await client.post(
                "/app/sources",
                data={
                    "name": "Owner source",
                    "platform": await _platform_value(),
                    "external_id": external_id,
                    "source_type": "user",
                    "monitored_users": "",
                    "_csrf": token,
                },
            )
            assert resp.status_code == 200
            assert DENIED not in resp.text

            with tenant_scope(bypass=True):
                source = await Source.objects.filter(external_id=external_id).first()
            assert source is not None
            source_id = source.id
        finally:
            if source_id is not None:
                with tenant_scope(bypass=True):
                    await Source.objects.delete(id=source_id)
            await _drop(user, tenant_id)


async def test_non_owner_without_rights_is_read_only_even_when_posting_directly() -> None:
    """The hidden button is not the check: a crafted POST is refused and writes nothing."""
    owner: User | None = None
    member: User | None = None
    tenant_id = None
    name = _name("blocked-task")
    password = "secret-password-1"
    try:
        async with await _client() as c:
            owner, tenant_id = await _register(c, "RightsOwner")
            member = await _invitee("RightsViewer", UserRoleType.VIEWER, tenant_id, password)
            loaded = await _loaded(member)
            assert not loaded.has_perm_for("agenttask", ActionType.CREATE), "premise: VIEWER may not create tasks"

        async with await _client() as m:
            await _login(m, member.username, password)

            page = await m.get("/app/tasks")
            assert page.status_code == 200
            assert CREATE_BUTTON not in page.text, "a read-only member must not see the create button"

            token = CSRF_RE.search(page.text).group(1)
            resp = await m.post(
                "/app/tasks",
                data={"name": name, "job_type": "collect", "cron_custom": "@once", "start_date": "2026-09-01", "_csrf": token},
            )
            assert resp.status_code == 200
            assert DENIED in resp.text

            with tenant_scope(bypass=True):
                assert await AgentTask.objects.filter(name=name).first() is None
    finally:
        await _drop(member, tenant_id)
        await _drop(owner, tenant_id)


async def test_platform_role_decides_for_a_non_owner() -> None:
    """MANAGER's `social.source.*` opens sources; its missing `agenttask` rows close tasks."""
    owner: User | None = None
    member: User | None = None
    tenant_id = None
    external_id = secrets.token_hex(6)
    name = _name("managed-task")
    source_id = None
    try:
        async with await _client() as c:
            owner, tenant_id = await _register(c, "MgrOwner")
            member = await _invitee("RightsManager", UserRoleType.MANAGER, tenant_id, "secret-password-1")
            loaded = await _loaded(member)
            assert loaded.has_perm_for("source", ActionType.CREATE), "premise: MANAGER may create sources"
            assert not loaded.has_perm_for("agenttask", ActionType.CREATE), "premise: MANAGER may not create tasks"

        async with await _client() as m:
            await _login(m, member.username)

            page = await m.get("/app/sources")
            assert page.status_code == 200
            assert CREATE_BUTTON in page.text, "MANAGER keeps the source create button"

            token = CSRF_RE.search(page.text).group(1)
            resp = await m.post(
                "/app/sources",
                data={
                    "name": "Managed source",
                    "platform": await _platform_value(),
                    "external_id": external_id,
                    "source_type": "user",
                    "monitored_users": "",
                    "_csrf": token,
                },
            )
            assert resp.status_code == 200
            assert DENIED not in resp.text
            with tenant_scope(bypass=True):
                source = await Source.objects.filter(external_id=external_id).first()
            assert source is not None
            source_id = source.id

            tasks_page = await m.get("/app/tasks")
            assert CREATE_BUTTON not in tasks_page.text
            token = CSRF_RE.search(tasks_page.text).group(1)
            resp = await m.post(
                "/app/tasks",
                data={"name": name, "job_type": "collect", "cron_custom": "@once", "start_date": "2026-09-01", "_csrf": token},
            )
            assert DENIED in resp.text
            with tenant_scope(bypass=True):
                assert await AgentTask.objects.filter(name=name).first() is None
    finally:
        if source_id is not None:
            with tenant_scope(bypass=True):
                await Source.objects.delete(id=source_id)
        await _drop(member, tenant_id)
        await _drop(owner, tenant_id)


@pytest.mark.parametrize("path", ["/app/sources", "/app/tasks"])
async def test_pages_stay_browsable_for_a_read_only_member(path: str) -> None:
    """Denying a write never hides the page itself — the UI stays readable."""
    owner: User | None = None
    member: User | None = None
    tenant_id = None
    try:
        async with await _client() as c:
            owner, tenant_id = await _register(c, "BrowsOwner")
            member = await _invitee("RightsBrowser", UserRoleType.VIEWER, tenant_id, "secret-password-1")

        async with await _client() as m:
            await _login(m, member.username)
            page = await m.get(path)
            assert page.status_code == 200
            assert DENIED not in page.text
            assert CREATE_BUTTON not in page.text
    finally:
        await _drop(member, tenant_id)
        await _drop(owner, tenant_id)
