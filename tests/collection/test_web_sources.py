"""Sources v2: the collection mode and token owner are visible and editable.

Before this, `params["mode"]` (which layer fetches — API, bot updates, or a
personal L2 session) and `params["token_owner"]` (whose personal token collects)
existed in the data model and were honoured by `app/services/social/owner.py`,
but the web UI never showed them: a user could not see why a source was silent,
let alone fix it.

These tests pin what was actually broken — the values reach `params`, a foreign
`token_owner` is refused instead of silently stored, an unknown mode is named
rather than coerced — plus the detail page that answers "why is nothing
collected from it?".
"""

from __future__ import annotations

import re
import secrets

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import AIAnalytics, Job, Platform, Role, Source, User
from app.models.managers.tenant_manager import TenantUserManager, tenants
from app.types import PlatformRoleType, WorkspaceRoleType
from app.web.sources import COLLECTION_MODES

CSRF_RE = re.compile(r'name="_csrf" value="([^"]+)"')
DENIED = "Недостаточно прав для этого действия"


async def _client() -> AsyncClient:
    app = create_application()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=True)


def _name(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(4)}"


async def _csrf(client: AsyncClient, path: str) -> str:
    page = await client.get(path)
    assert page.status_code == 200
    match = CSRF_RE.search(page.text)
    assert match, f"no csrf input on {path}"
    return match.group(1)


async def _register(client: AsyncClient, prefix: str) -> tuple[User, int]:
    username = _name(prefix)
    token = await _csrf(client, "/app/register")
    resp = await client.post(
        "/app/register",
        data={
            "username": username,
            "email": f"{username}@example.com",
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


async def _invitee(prefix: str, role_type: WorkspaceRoleType, tenant_id: int) -> User:
    """A standard platform user with the supplied workspace membership role."""
    role = await Role.objects.get(codename=PlatformRoleType.USER.value)
    username = _name(prefix)
    user = await User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="secret-password-1",
        role_id=role.id,
    )
    await TenantUserManager().add_web_member(tenant_id=tenant_id, user_id=user.id, role=role_type.value)
    return user


async def _platform_value() -> str:
    platform = await Platform.objects.filter(is_active=True).first()
    assert platform is not None, "the test database seeds platforms"
    return platform.platform_type.db_value


async def _drop(user: User | None, tenant_id: int | None) -> None:
    if user is not None:
        await User.objects.delete_user(user.id)
    if tenant_id is not None:
        await tenants.delete_by_id(tenant_id)


async def _make_source(client: AsyncClient, name: str, mode: str = "pull", owner_id: int | None = None) -> int:
    """Create a source through the real add form and return its id."""
    external_id = secrets.token_hex(6)
    token = await _csrf(client, "/app/sources")
    resp = await client.post(
        "/app/sources",
        data={
            "name": name,
            "platform": await _platform_value(),
            "external_id": external_id,
            "source_type": "user",
            "mode": mode,
            "token_owner": str(owner_id or ""),
            "_csrf": token,
        },
    )
    assert resp.status_code == 200, "the add form refused a valid source"
    with tenant_scope(bypass=True):
        source = await Source.objects.filter(external_id=external_id).first()
    assert source is not None
    return source.id


async def _drop_source(source_id: int | None) -> None:
    if source_id is not None:
        with tenant_scope(bypass=True):
            await Source.objects.delete(id=source_id)


async def test_the_sources_page_does_not_push_the_vk_login():
    """The sources page no longer carries a VK badge; Settings owns that state.

    What it must still get right is *silence*: with no source that needs a
    personal token, nothing about VK is shown at all. A login button parked on
    this page was noise for everyone whose collection works with the community
    token alone.
    """
    client = await _client()
    user, tenant_id = await _register(client, "vkbadge")
    try:
        await _login(client, user.username)

        from app.models.managers.user_credential_manager import user_credentials

        page = await client.get("/app/sources")
        assert page.status_code == 200
        assert "Войти через VK ID" not in page.text

        await user_credentials.store(
            user_id=user.id,
            platform="vk",
            kind="user_token",
            secret="test-token",
            label="VK test",
        )
        page = await client.get("/app/sources")
        assert page.status_code == 200
        assert "Войти через VK ID" not in page.text
    finally:
        await _drop(user, tenant_id)


async def test_task_scoped_query_lane_is_hidden_from_the_global_source_list():
    """A task's search lane is not a workspace source.

    The row exists so staged items keep a stable `source_id`, but showing it in
    `/app/sources` would offer the operator a source they cannot collect from
    and cannot delete without breaking the task.
    """
    from app.models import AgentTask
    from app.types import SourceType

    client = await _client()
    user, tenant_id = await _register(client, "lanefilter")
    task = lane = visible = None
    try:
        await _login(client, user.username)
        platform = await Platform.objects.filter(is_active=True).first()
        assert platform is not None
        with tenant_scope(bypass=True):
            task = await AgentTask.objects.create(
                tenant_id=tenant_id,
                name=_name("lane-task"),
                job_type="collect",
                cron_expr="0 9 * * *",
                payload={},
            )
            lane = await Source.objects.create(
                tenant_id=tenant_id,
                platform_id=platform.id,
                name=_name("lane-query"),
                external_id=secrets.token_hex(6),
                source_type=SourceType.SEARCH,
                owner_task_id=task.id,
            )
            visible = await Source.objects.create(
                tenant_id=tenant_id,
                platform_id=platform.id,
                name=_name("known-wall"),
                external_id=secrets.token_hex(6),
                source_type=SourceType.GROUP,
            )

        page = await client.get("/app/sources")
        assert page.status_code == 200
        assert lane.name not in page.text, "a task-owned lane must not be offered as a workspace source"
        assert visible.name in page.text

        # The lane is also outside the "all active sources" fallback: it belongs
        # to one task and must not become another task's collection target.
        from types import SimpleNamespace

        from app.jobs.handlers import _resolve_sources
        from app.models.managers.agent_task_manager import AgentTaskManager

        with tenant_scope(tenant_id):
            active = await AgentTaskManager().get_workspace_active_source_ids()
            resolved = await _resolve_sources(
                SimpleNamespace(id=task.id, tenant_id=tenant_id, sources=[])
            )
        assert visible.id in active
        assert lane.id not in active
        resolved_ids = {source.id for source in resolved}
        assert visible.id in resolved_ids
        assert lane.id not in resolved_ids
    finally:
        with tenant_scope(bypass=True):
            if task is not None:
                await AgentTask.objects.delete_by_id(task.id)
            if visible is not None:
                await Source.objects.delete(id=visible.id)
        await _drop(user, tenant_id)


# ── delete ───────────────────────────────────────────────────────────────────


async def _row_source(tenant_id: int, name: str, **extra) -> Source:
    """A Source row in a given workspace, for tests that run under the real guard.

    In tenancy mode `Source.objects.create` is permission-gated and stamps the
    tenant from the active scope, so the row is created inside the workspace's
    scope with the `source.create` service grant — the same pattern the
    tenancy-marked detail tests use.
    """
    from app.core.permissions import service_permission_scope
    from app.types import SourceType

    platform = await Platform.objects.filter(is_active=True).first()
    assert platform is not None, "the test database seeds platforms"
    extra.setdefault("source_type", SourceType.GROUP)
    with tenant_scope(tenant_id), service_permission_scope("source", "create"):
        return await Source.objects.create(
            tenant_id=tenant_id,
            platform_id=platform.id,
            name=name,
            external_id=_name("del-ext"),
            is_active=True,
            **extra,
        )


@pytest.mark.tenancy
async def test_delete_removes_a_workspace_source_and_lands_on_the_list():
    """Deleting a source: confirm on the page, land on the list, cascade named.

    Both pages that show a source offer the delete through the shared confirm
    dialog; the successful delete redirects to `/app/sources`, the flash names
    the cascade (analytics go with the row), and the row is gone from the list.
    """
    client = await _client()
    user, tenant_id = await _register(client, "SrcDelRow")
    name = _name("doomed")
    source = None
    try:
        await _login(client, user.username)
        source = await _row_source(tenant_id, name)

        detail = await client.get(f"/app/sources/{source.id}")
        assert detail.status_code == 200
        assert f'action="/app/sources/{source.id}/delete"' in detail.text
        assert 'data-confirm-title="Удалить источник?"' in detail.text
        listing = await client.get("/app/sources")
        assert f'action="/app/sources/{source.id}/delete"' in listing.text

        token = await _csrf(client, f"/app/sources/{source.id}")
        resp = await client.post(f"/app/sources/{source.id}/delete", data={"_csrf": token})
        assert resp.status_code == 200
        assert str(resp.url).endswith("/app/sources")
        assert "удалён вместе с его аналитикой" in resp.text
        with tenant_scope(bypass=True):
            assert await Source.objects.get(id=source.id) is None
        listing = await client.get("/app/sources")
        assert name not in listing.text
    finally:
        if source is not None:
            await _drop_source(source.id)
        await _drop(user, tenant_id)


@pytest.mark.tenancy
async def test_delete_refuses_another_workspaces_source():
    """The workspace boundary holds on delete: not-found abroad, row survives.

    B owns a workspace of their own, so the route's guard is the tenant filter —
    A's source is simply not in B's scope, and the row stays A's.
    """
    owner = other = None
    owner_tenant = other_tenant = None
    source = None
    try:
        async with await _client() as owner_client:
            owner, owner_tenant = await _register(owner_client, "SrcDelOwn")
        async with await _client() as intruder_client:
            other, other_tenant = await _register(intruder_client, "SrcDelOth")
        source = await _row_source(owner_tenant, _name("private"))

        async with await _client() as intruder_client:
            await _login(intruder_client, other.username)
            detail = await intruder_client.get(f"/app/sources/{source.id}")
            assert detail.status_code == 200
            assert f'action="/app/sources/{source.id}/delete"' not in detail.text

            token = await _csrf(intruder_client, "/app/sources")
            resp = await intruder_client.post(f"/app/sources/{source.id}/delete", data={"_csrf": token})
            assert resp.status_code == 200
            assert "Источник не найден" in resp.text

        with tenant_scope(bypass=True):
            assert await Source.objects.get(id=source.id) is not None
    finally:
        if source is not None:
            await _drop_source(source.id)
        if other is not None:
            await _drop(other, other_tenant)
        if owner is not None:
            await _drop(owner, owner_tenant)


@pytest.mark.tenancy
async def test_delete_refuses_a_task_owned_query_lane():
    """A task-owned query lane must not be deletable from the source pages.

    The lane keeps the task's collection stable; deleting it here would break
    that collection. The detail page hides the delete for a lane, and a direct
    POST is refused with a flash naming the owning task — the row survives.
    """
    from app.core.permissions import service_permission_scope
    from app.models import AgentTask
    from app.types import SourceType

    client = await _client()
    user, tenant_id = await _register(client, "SrcDelLane")
    task = None
    source = None
    try:
        await _login(client, user.username)
        platform = await Platform.objects.filter(is_active=True).first()
        assert platform is not None
        with tenant_scope(tenant_id), service_permission_scope("agenttask", "create"):
            task = await AgentTask.objects.create(
                tenant_id=tenant_id,
                name=_name("lane-task"),
                job_type="collect",
                cron_expr="0 9 * * *",
                payload={},
            )
        source = await _row_source(tenant_id, _name("lane-query"), owner_task_id=task.id, source_type=SourceType.SEARCH)

        detail = await client.get(f"/app/sources/{source.id}")
        assert detail.status_code == 200
        assert f'action="/app/sources/{source.id}/delete"' not in detail.text
        assert "удаляется в задаче" in detail.text

        token = await _csrf(client, f"/app/sources/{source.id}")
        resp = await client.post(f"/app/sources/{source.id}/delete", data={"_csrf": token})
        assert resp.status_code == 200
        assert f"поиску задачи №{task.id} " in resp.text
        assert "удалите его в задаче" in resp.text
        with tenant_scope(bypass=True):
            assert await Source.objects.get(id=source.id) is not None
    finally:
        if source is not None:
            await _drop_source(source.id)
        if task is not None:
            with tenant_scope(tenant_id), service_permission_scope("agenttask", "delete"):
                await AgentTask.objects.delete_by_id(task.id)
        await _drop(user, tenant_id)


@pytest.mark.tenancy
async def test_delete_requires_the_source_delete_permission():
    """Without `source.delete` the button is gone and the POST is refused.

    A viewer may look at a source but not delete it: the detail page hides the
    form, and the POST hits `guard_web`, which flashes the standing denial and
    redirects back — the row survives.
    """
    client = await _client()
    owner, tenant_id = await _register(client, "SrcDelPerm")
    source = None
    member = None
    try:
        await _login(client, owner.username)
        source = await _row_source(tenant_id, _name("protected"))
        member = await _invitee("SrcDelNope", WorkspaceRoleType.VIEWER, tenant_id)

        async with await _client() as viewer_client:
            await _login(viewer_client, member.username)
            detail = await viewer_client.get(f"/app/sources/{source.id}")
            assert detail.status_code == 200
            assert f'action="/app/sources/{source.id}/delete"' not in detail.text

            token = await _csrf(viewer_client, "/app/sources")
            resp = await viewer_client.post(f"/app/sources/{source.id}/delete", data={"_csrf": token})
            assert resp.status_code == 200
            assert str(resp.url).endswith("/app/sources")
            assert DENIED in resp.text

        with tenant_scope(bypass=True):
            assert await Source.objects.get(id=source.id) is not None
    finally:
        if source is not None:
            await _drop_source(source.id)
        if member is not None:
            await User.objects.delete_user(member.id)
        await _drop(owner, tenant_id)
