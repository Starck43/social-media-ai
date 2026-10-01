"""Integration tests for the web UI auth slice (M0+M1, docs/design/ui.md).

Real PostgreSQL (same policy as the rest of the suite): register/login/invite
run through the ASGI stack so `TenantUIMiddleware`, session cookies and CSRF
are exercised as users hit them. Rows created here are cleaned up per test.
"""

from __future__ import annotations

import re
import secrets

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.tenant_context import PlatformScopeMiddleware, current_tenant_id, is_bypass, tenant_scope
from app.main import create_application
from app.models import AgentTask, Job, User
from app.models.managers.tenant_manager import TenantInviteManager, TenantUserManager, tenants

CSRF_RE = re.compile(r'name="_csrf" value="([^"]+)"')


def _name(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(4)}"


async def _client() -> AsyncClient:
    app = create_application()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=True)


async def _csrf(client: AsyncClient, path: str) -> str:
    page = await client.get(path)
    assert page.status_code == 200
    match = CSRF_RE.search(page.text)
    assert match, f"no csrf input on {path}"
    return match.group(1)


async def _delete_user(user_id: int) -> None:
    await User.objects.delete_user(user_id)


@pytest.fixture
async def client():
    async with await _client() as c:
        yield c


# ── redirect behaviour ─────────────────────────────────────────────────────


async def test_unauthenticated_redirects_to_login(client: AsyncClient) -> None:
    resp = await client.get("/app/")
    assert resp.status_code == 200
    assert "/app/login" in [r.url for r in resp.history] or "Вход" in resp.text


async def test_login_page_reachable_without_session(client: AsyncClient) -> None:
    resp = await client.get("/app/login")
    assert resp.status_code == 200
    assert 'name="username"' in resp.text


async def test_invalid_credentials_return_400(client: AsyncClient) -> None:
    token = await _csrf(client, "/app/login")
    resp = await client.post("/app/login", data={"username": _name("nope"), "password": "whatever12", "_csrf": token})
    assert resp.status_code == 400


async def test_missing_csrf_is_rejected(client: AsyncClient) -> None:
    resp = await client.post("/app/register", data={"username": "x", "email": "x@e.test", "password": "password123"})
    assert resp.status_code == 403


# ── register → workspace owner ─────────────────────────────────────────────


async def test_register_creates_workspace_and_membership(client: AsyncClient) -> None:
    username = _name("web")
    token = await _csrf(client, "/app/register")
    resp = await client.post(
        "/app/register",
        data={
            "username": username,
            "email": f"{username}@example.test",
            "password": "secret-password-1",
            "workspace": "Test Studio",
            "_csrf": token,
        },
    )
    user: User | None = None
    tenant = None
    try:
        assert resp.status_code == 200
        assert "Test Studio" in resp.text  # dashboard rendered with the workspace

        user = await User.objects.get(username=username)
        assert user is not None
        memberships = await TenantUserManager().web_memberships(user.id)
        assert len(memberships) == 1
        assert memberships[0].role == "owner"
        tenant = await tenants.get(id=memberships[0].tenant_id)
        assert tenant.name == "Test Studio"

        page = await client.get("/app/")
        assert page.status_code == 200 and "Test Studio" in page.text
    finally:
        if user is not None:
            await _delete_user(user.id)
        if tenant is not None:
            await tenants.delete_by_id(tenant.id)


async def test_logout_clears_session(client: AsyncClient) -> None:
    username = _name("web")
    token = await _csrf(client, "/app/register")
    reg = await client.post(
        "/app/register",
        data={
            "username": username,
            "email": f"{username}@example.test",
            "password": "secret-password-1",
            "workspace": "Bye Studio",
            "_csrf": token,
        },
    )
    try:
        assert reg.status_code == 200
        csrf2 = await _csrf(client, "/app/")
        await client.post("/app/logout", data={"_csrf": csrf2})
        resp = await client.get("/app/")
        assert "Вход" in resp.text or "/app/login" in str(resp.url)
    finally:
        user = await User.objects.get(username=username)
        if user is not None:
            ms = await TenantUserManager().web_memberships(user.id)
            await _delete_user(user.id)  # memberships cascade via tenant_users.user_id FK
            for m in ms:
                await tenants.delete_by_id(m.tenant_id)


# ── invite redeem over the web ─────────────────────────────────────────────


async def test_invite_redeem_adds_second_membership(client: AsyncClient) -> None:
    owner_name, member_name = _name("web"), _name("web")
    owner_token = await _csrf(client, "/app/register")
    await client.post(
        "/app/register",
        data={
            "username": owner_name,
            "email": f"{owner_name}@example.test",
            "password": "secret-password-1",
            "workspace": "Invite Host",
            "_csrf": owner_token,
        },
    )
    owner = await User.objects.get(username=owner_name)
    host_memberships = await TenantUserManager().web_memberships(owner.id)
    host_tenant_id = host_memberships[0].tenant_id
    _, code = await TenantInviteManager().issue(tenant_id=host_tenant_id, role="member", max_uses=5)

    member = None
    own_tenant_id = None
    try:
        async with await _client() as fresh:
            member_token = await _csrf(fresh, "/app/register")
            await fresh.post(
                "/app/register",
                data={
                    "username": member_name,
                    "email": f"{member_name}@example.test",
                    "password": "secret-password-1",
                    "workspace": "Own Studio",
                    "_csrf": member_token,
                },
            )
            member = await User.objects.get(username=member_name)
            own_tenant_id = (await TenantUserManager().web_memberships(member.id))[0].tenant_id

            csrf3 = await _csrf(fresh, "/app/invite")
            resp = await fresh.post("/app/invite", data={"code": code, "_csrf": csrf3})
            assert resp.status_code == 200

            memberships = await TenantUserManager().web_memberships(member.id)
            assert {m.tenant_id for m in memberships} == {host_tenant_id, own_tenant_id}
            # the header switcher renders both workspaces
            assert "Invite Host" in resp.text and "Own Studio" in resp.text
    finally:
        if member is not None:
            await _delete_user(member.id)
        await _delete_user(owner.id)
        for tid in {host_tenant_id, own_tenant_id}:
            await tenants.delete_by_id(tid)


# ── middleware scope handoff ───────────────────────────────────────────────


async def test_dashboard_shows_kpis(client: AsyncClient) -> None:
    """Dashboard renders real KPIs (even if all zeros on empty DB)."""
    username = _name("web")
    token = await _csrf(client, "/app/register")
    await client.post(
        "/app/register",
        data={
            "username": username,
            "email": f"{username}@example.test",
            "password": "secret-password-1",
            "workspace": "KPI Studio",
            "_csrf": token,
        },
    )
    user = await User.objects.get(username=username)
    try:
        resp = await client.get("/app/")
        assert resp.status_code == 200
        # KPIs are rendered (even if zero)
        assert "Источники активны" in resp.text
        assert "Постов за сутки" in resp.text
        assert "LLM-расход за сутки" in resp.text
        # Values are present (0 or numbers)
        assert ">0<" in resp.text or ">0.00<" in resp.text
    finally:
        if user is not None:
            ms = await TenantUserManager().web_memberships(user.id)
            await _delete_user(user.id)
            for m in ms:
                await tenants.delete_by_id(m.tenant_id)


async def test_sources_list_requires_auth(client: AsyncClient) -> None:
    """Unauthenticated access to /app/sources redirects to login."""
    resp = await client.get("/app/sources")
    assert resp.status_code == 200
    assert "Вход" in resp.text or "/app/login" in str(resp.url)


async def test_platform_scope_skips_app_paths() -> None:
    """PlatformScope must not run /app requests as bypass — TenantUI owns them."""
    seen: dict[str, object] = {}

    async def probe(scope, receive, send):
        seen["bypass"] = is_bypass()
        seen["tenant"] = current_tenant_id()

    mw = PlatformScopeMiddleware(probe)

    async def call(path: str) -> None:
        await mw({"type": "http", "path": path, "method": "GET", "headers": []}, None, None)

    await call("/admin/tasks")
    assert seen["bypass"] is True

    # /api is a client surface, not the operator console: it must stay out of
    # the bypass (ApiScopeMiddleware authenticates and scopes it instead).
    seen.clear()
    await call("/api/v1/ping")
    assert seen["bypass"] is False

    seen.clear()
    await call("/app/settings")
    assert seen["bypass"] is False and seen["tenant"] is None


# ── task run-now ───────────────────────────────────────────────────────────


async def _register(client: AsyncClient, workspace: str) -> tuple[User, int]:
    username = _name("web")
    token = await _csrf(client, "/app/register")
    resp = await client.post(
        "/app/register",
        data={
            "username": username,
            "email": f"{username}@example.test",
            "password": "secret-password-1",
            "workspace": workspace,
            "_csrf": token,
        },
    )
    assert resp.status_code == 200
    user = await User.objects.get(username=username)
    memberships = await TenantUserManager().web_memberships(user.id)
    return user, memberships[0].tenant_id


async def test_run_now_on_once_completes_task(client: AsyncClient) -> None:
    """Creating a @once task with 'Создать и выполнить' enqueues a job and completes it."""
    async with await _client() as c:
        user, tenant_id = await _register(c, "RunNow Once")
        name = _name("once")
        task_id = None
        try:
            csrf = await _csrf(c, "/app/tasks")
            resp = await c.post(
                "/app/tasks",
                data={"name": name, "job_type": "collect", "cron_custom": "@once", "run_now": "on", "_csrf": csrf},
            )
            assert resp.status_code == 200

            with tenant_scope(bypass=True):
                task = await AgentTask.objects.get(name=name)
                task_id = task.id
                job = await Job.objects.get(agent_task_id=task.id, job_type="collect")
            assert task.cron_expr == "@once"
            assert task.is_active is False
            assert task.last_run_at is not None
            assert task.next_run_at is None
            assert job is not None and job.status == "pending"
        finally:
            if user is not None:
                await _delete_user(user.id)
            if task_id is not None:
                with tenant_scope(bypass=True):
                    await AgentTask.objects.delete(id=task_id)
            await tenants.delete_by_id(tenant_id)


async def test_run_now_endpoint_enqueues_recurring_task(client: AsyncClient) -> None:
    """The /run-now action enqueues a job for a recurring task without touching its schedule."""
    from app.tasks.cron import next_run_at as compute_next
    from app.web.tasks import enqueue_task_now

    async with await _client() as c:
        user, tenant_id = await _register(c, "RunNow Recurring")
        name = _name("hourly")
        task_id = None
        try:
            with tenant_scope(bypass=True):
                task = await AgentTask.objects.create(
                    name=name,
                    job_type="collect",
                    cron_expr="0 * * * *",
                    timezone="Europe/Moscow",
                    payload={},
                    is_active=True,
                    next_run_at=compute_next("0 * * * *", "Europe/Moscow"),
                )
                task_id = task.id
                next_before = task.next_run_at
                # enqueue immediately (same path the /run-now endpoint and
                # 'Сохранить и выполнить' use)
                await enqueue_task_now(task)

            with tenant_scope(bypass=True):
                job = await Job.objects.get(agent_task_id=task_id, job_type="collect")
                refreshed = await AgentTask.objects.get(id=task_id)
            assert job is not None and job.status == "pending"
            # recurring task keeps its schedule
            assert refreshed.next_run_at == next_before
            assert refreshed.is_active is True
        finally:
            if user is not None:
                await _delete_user(user.id)
            if task_id is not None:
                with tenant_scope(bypass=True):
                    await AgentTask.objects.delete(id=task_id)
            await tenants.delete_by_id(tenant_id)
