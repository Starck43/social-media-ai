"""Integration tests for the web UI auth slice documented in docs/API.md.

Real PostgreSQL (same policy as the rest of the suite): register/login/invite
run through the ASGI stack so `TenantUIMiddleware`, session cookies and CSRF
are exercised as users hit them. Rows created here are cleaned up per test.
"""

from __future__ import annotations

import asyncio
import re
import secrets

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.tenant_context import PlatformScopeMiddleware, current_tenant_id, is_bypass, tenant_scope
from app.main import create_application
from app.models import AgentTask, Job, PersonalBillingAccount, TaskBillingPayer, User
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


async def _default_scenario(tenant_id: int) -> int:
    """An active default methodology for a freshly registered workspace.

    A web-created `collect` task is refused without one, and the form writes no
    row in that case. The scenario dies with its workspace (tenant cascade).
    """
    from app.models import AgentScenario

    row = await AgentScenario.objects.create(
        tenant_id=tenant_id,
        name=_name("method"),
        description="created by tests",
        is_active=True,
        is_default=True,
    )
    return row.id


async def _create_task(user: User, tenant_id: int, **fields: object) -> "AgentTask":
    """Create an owned, activated task and wait for any queued run to finish.

    Rows created here are cleaned up per test, so the task waits for its own
    background run before the fixture can delete the row out from under it.
    """
    from app.jobs.dispatcher import _BACKGROUND_TASKS

    with tenant_scope(tenant_id, bypass=True):
        # Explicit tenant: bypass leaves the ambient scope empty, so an
        # unowned row would be invisible to the route and the payer consent.
        task = await AgentTask.objects.create(tenant_id=tenant_id, **fields)
    while _BACKGROUND_TASKS:
        await asyncio.gather(*list(_BACKGROUND_TASKS), return_exceptions=True)
    return task


async def _task_payer(user: User, tenant_id: int, task_id: int) -> None:
    """Bind `user` as the task's payer: an inline run resolves stored consent.

    Without consent a `digest` run is refused (`PersonalBillingUnavailable`), so
    this goes through the same service a real operator's settings page uses.
    """
    from app.core.permissions import set_current_user
    from app.services.billing.payer import configure_current_personal_account, consent_current_user_as_task_payer

    token = set_current_user(user)
    try:
        with tenant_scope(tenant_id):
            await configure_current_personal_account("5")
            await consent_current_user_as_task_payer(task_id)
    finally:
        set_current_user(None)
        del token


async def _active_source(tenant_id: int) -> int:
    """One active VK source in the workspace; the task must link a real one."""
    from app.models import Platform, Source
    from app.types import SourceType

    platform = await Platform.objects.filter(platform_type="vk").first()
    row = await Source.objects.create(
        tenant_id=tenant_id,
        platform_id=platform.id,
        name=_name("src"),
        source_type=SourceType.CHANNEL.name,
        external_id=f"runnow-{secrets.token_hex(6)}",
        params={},
        is_active=True,
    )
    return row.id


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
            "email": f"{username}@example.com",
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
        assert memberships[0].is_owner
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
            "email": f"{username}@example.com",
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
            "email": f"{owner_name}@example.com",
            "password": "secret-password-1",
            "workspace": "Invite Host",
            "_csrf": owner_token,
        },
    )
    owner = await User.objects.get(username=owner_name)
    host_memberships = await TenantUserManager().web_memberships(owner.id)
    host_tenant_id = host_memberships[0].tenant_id
    # Issuing for a workspace is a workspace-scoped write: the manager reads the
    # ambient tenant, so an unscoped call is denied by design. The suite runs as
    # the platform owner here, exactly as the other admin fixtures do.
    with tenant_scope(host_tenant_id, bypass=True):
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
                    "email": f"{member_name}@example.com",
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


async def test_dashboard_shows_compact_overview(client: AsyncClient) -> None:
    """Home is a read-only overview, without the retired KPI/command panels."""
    username = _name("web")
    token = await _csrf(client, "/app/register")
    await client.post(
        "/app/register",
        data={
            "username": username,
            "email": f"{username}@example.com",
            "password": "secret-password-1",
            "workspace": "KPI Studio",
            "_csrf": token,
        },
    )
    user = await User.objects.get(username=username)
    try:
        resp = await client.get("/app/")
        assert resp.status_code == 200
        assert "Последние результаты" in resp.text
        assert "data-home-chat" in resp.text
        for label in ("Источники активны", "Постов за сутки", "LLM-расход за сутки", "Быстрые действия"):
            assert label not in resp.text
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


async def test_vk_oauth_moved_to_settings(client: AsyncClient) -> None:
    """Connecting VK is a Settings concern now; the old route still works.

    The login button left `/app/sources` on purpose: a permanent button next to
    the source list nagged everyone whose collection works with the community
    token alone. The legacy `/app/vk/oauth` path is kept so bookmarks and the
    API-driven flow do not dead-end.
    """
    from app.core.config import settings

    user, tenant_id = await _register(client, "VK Button Studio")
    try:
        page = await client.get("/app/sources")
        assert page.status_code == 200
        assert "Войти через VK ID" not in page.text

        settings_page = await client.get("/app/settings?tab=connections")
        assert settings_page.status_code == 200
        assert "Мои подключения" in settings_page.text
        assert "ВКонтакте" in settings_page.text
        if not settings.VK_APP_ID:
            # Nothing to redirect to — the card says so instead of offering a
            # button that would only produce an error.
            assert "Не настроено оператором" in settings_page.text
        else:
            assert "/app/connections/vk/authorize" in settings_page.text
    finally:
        await _delete_user(user.id)
        await tenants.delete_by_id(tenant_id)


async def test_vk_oauth_redirects_to_authorize(client: AsyncClient) -> None:
    """/app/vk/oauth redirects the active workspace to the VK authorize URL."""
    from app.core.config import settings

    if not settings.VK_APP_ID:
        pytest.skip("VK_APP_ID not configured")
    user, tenant_id = await _register(client, "VK Redirect Studio")
    try:
        resp = await client.get("/app/vk/oauth", follow_redirects=False)
        assert resp.status_code == 302
        location = resp.headers.get("location", "")
        # The authorize URL comes from the shared catalog, not process settings.
        assert "/authorize?" in location
        assert "client_id=" in location
    finally:
        await _delete_user(user.id)
        await tenants.delete_by_id(tenant_id)


async def test_sources_page_has_no_scenario_filter(client: AsyncClient) -> None:
    """The sources page no longer offers a per-source scenario filter.

    The scenario belongs to the task, not the source, so the /app/sources
    dropdown (and its `scenario_id` query filter) is gone.
    """
    user, tenant_id = await _register(client, "Scenario Filter Studio")
    try:
        resp = await client.get("/app/sources")
        assert resp.status_code == 200
        assert "Все сценарии" not in resp.text
    finally:
        await _delete_user(user.id)
        await tenants.delete_by_id(tenant_id)


@pytest.mark.tenancy
async def test_platform_scope_skips_app_paths() -> None:
    """PlatformScope must not run /app requests as bypass — TenantUI owns them.

    `/admin` is out too: `AdminScopeMiddleware` binds the console to its actor
    (the owner gets the bypass there, a member does not).
    """
    seen: dict[str, object] = {}

    async def probe(scope, receive, send):
        seen["bypass"] = is_bypass()
        seen["tenant"] = current_tenant_id()

    mw = PlatformScopeMiddleware(probe)

    async def call(path: str) -> None:
        await mw({"type": "http", "path": path, "method": "GET", "headers": []}, None, None)

    await call("/admin/tasks")
    assert seen["bypass"] is False

    # /static is a file mount with no actor: it owns its own (empty) scope, so a
    # console page that lost its session cannot serve someone else's asset.
    await call("/static/logo.png")
    assert seen["bypass"] is False

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
            "email": f"{username}@example.com",
            "password": "secret-password-1",
            "workspace": workspace,
            "_csrf": token,
        },
    )
    assert resp.status_code == 200
    user = await User.objects.get(username=username)
    memberships = await TenantUserManager().web_memberships(user.id)
    return user, memberships[0].tenant_id


async def test_dashboard_keeps_results_without_the_analytical_sidebar(client: AsyncClient) -> None:
    """Existing analysis cards remain; specialized metrics are not home widgets."""
    from datetime import date

    from app.models import AIAnalytics, Platform, Source
    from app.types import SourceType

    user, tenant_id = await _register(client, "Result Studio")
    platform = await Platform.objects.filter(platform_type="vk").first()
    source = None
    try:
        source = await Source.objects.create(
            tenant_id=tenant_id,
            platform_id=platform.id,
            name="Overview Channel",
            source_type=SourceType.CHANNEL.name,
            external_id=f"overview-{secrets.token_hex(6)}",
            params={},
            is_active=True,
        )
        await AIAnalytics.objects.create(
            tenant_id=tenant_id,
            source_id=source.id,
            analysis_date=date.today(),
            summary_data={"multi_llm_analysis": {"text_analysis": {"toxicity_score": 0.9}}},
        )
        resp = await client.get("/app/")
        assert resp.status_code == 200
        assert "Последние результаты" in resp.text
        assert "Overview Channel" in resp.text
        for label in ("Токсичных за 7 дней", "Хэштеги за 7 дней", "Топ тем за 7 дней", "Типы контента"):
            assert label not in resp.text
    finally:
        if user is not None:
            await _delete_user(user.id)
        if source is not None:
            with tenant_scope(bypass=True):
                await AIAnalytics.objects.filter(source_id=source.id).delete()
                await Source.objects.delete_by_id(source.id)
        await tenants.delete_by_id(tenant_id)


async def test_run_now_on_once_completes_task(client: AsyncClient) -> None:
    """A @once task created with 'Создать и выполнить' is disarmed for the run.

    The web form maps `collect` to the unified `analyze` task, which needs an
    active methodology of its own. Without a chosen scenario the task is still
    created, but inactive and with the reason — no job is queued behind the
    operator's back. The trigger bookkeeping stays: `@once` is disarmed and
    carries no next run.
    """
    async with await _client() as c:
        user, tenant_id = await _register(c, "RunNow Once")
        # A web-created analysis task needs a methodology: the form refuses to
        # stage a collection the workspace has no scenario for.
        await _default_scenario(tenant_id)
        # ...and an active source to collect from, which the task links to.
        await _active_source(tenant_id)
        name = _name("once")
        task_id = None
        try:
            csrf = await _csrf(c, "/app/tasks")
            resp = await c.post(
                "/app/tasks",
                data={
                    "name": name,
                    "job_type": "collect",
                    "cron_custom": "@once",
                    "run_now": "on",
                    "start_date": "2026-09-01",
                    "_csrf": csrf,
                },
            )
            assert resp.status_code == 200

            # The task belongs to the registered workspace, and the manager is
            # fail-closed for an unscoped read even under bypass.
            with tenant_scope(tenant_id, bypass=True):
                task = await AgentTask.objects.get(name=name)
                task_id = task.id
                jobs = await Job.objects.filter(agent_task_id=task.id).values(Job.id).rows()
            assert task.cron_expr == "@once"
            assert task.next_run_at is None
            # No methodology was chosen, so the task is created inactive. The
            # clicked run is still admitted once (the operator asked for it) and
            # the task is disarmed: `@once` has no next run and one job, queued
            # in this process rather than executed inside the request.
            assert task.is_active is False
            assert task.last_run_at is not None
            assert len(jobs) == 1
        finally:
            # The job points at the user who started it, so the task (and its
            # jobs) go first: the FK is not cascade-deleted. The queued run runs
            # in this loop; wait for it, or it inserts a job mid-cleanup.
            from app.jobs.dispatcher import _BACKGROUND_TASKS

            if _BACKGROUND_TASKS:
                await asyncio.gather(*list(_BACKGROUND_TASKS), return_exceptions=True)
            if task_id is not None:
                with tenant_scope(bypass=True):
                    await Job.objects.filter(initiated_by_user_id=user.id).delete()
                    await AgentTask.objects.delete(id=task_id)
            if user is not None:
                with tenant_scope(bypass=True):
                    # The personal account is RESTRICT on the user. A consent
                    # row is immutable by trigger, so a consenting fixture
                    # leaves it (and its workspace) behind: only rows this user
                    # merely *consented* can go, and only where it is not payer.
                    await TaskBillingPayer.objects.exclude(payer_user_id=user.id).filter(
                        consented_by_user_id=user.id
                    ).delete()
                    await PersonalBillingAccount.objects.filter(user_id=user.id).delete()
                await _delete_user(user.id)
            # The run leaves action-audit evidence with a RESTRICT FK on the
            # workspace: purge it through the supported retention path first.
            from tests.p11_t12_audit_cleanup import purge_tenant_audit_events

            await purge_tenant_audit_events(tenant_id)
            await tenants.delete_by_id(tenant_id)


async def test_run_now_executes_the_job_without_queueing_it(client: AsyncClient) -> None:
    """Run-now must not leave the job waiting for a worker.

    The button used to enqueue a `pending` job and let the worker pick it up, so
    "now" depended on a worker being free — and a worker that claimed it first
    ran the same collection twice while the user watched a spinner. The job now
    runs in the request itself and is never observable as `pending`.
    """
    from app.tasks.cron import next_run_at as compute_next
    from app.web.tasks import run_task_now

    async with await _client() as c:
        user, tenant_id = await _register(c, "RunNow Recurring")
        name = _name("hourly")
        task_id = None
        try:
            task = await _create_task(
                user,
                tenant_id,
                name=name,
                job_type="digest",
                cron_expr="0 * * * *",
                payload={},
                is_active=True,
                next_run_at=compute_next("0 * * * *", "Europe/Moscow"),
            )
            task_id = task.id
            next_before = task.next_run_at
            # The same path the /run-now endpoint and 'Сохранить и выполнить'
            # use. `digest` is used deliberately: it has no network side
            # effect, so the test measures the queueing, not the collection.
            # The inline run resolves the stored payer, so one has to consent.
            await _task_payer(user, tenant_id, task_id)
            with tenant_scope(tenant_id, bypass=True):
                task = await AgentTask.objects.get(id=task_id)
            outcome = await run_task_now(task)

            assert outcome["status"] in ("done", "failed")
            assert outcome.get("job_id") is not None

            with tenant_scope(bypass=True):
                job = await Job.objects.get(id=outcome["job_id"])
                refreshed = await AgentTask.objects.get(id=task_id)

            # Executed, not queued: the worker had no chance to claim it, and the
            # row carries its result.
            assert job.status == "done"
            assert job.result is not None
            # A recurring task keeps its schedule either way.
            assert refreshed.next_run_at == next_before
            assert refreshed.is_active is True
        finally:
            # The job points at the user who started it, so the task (and its
            # jobs) go first: the FK is not cascade-deleted. The queued run runs
            # in this loop; wait for it, or it inserts a job mid-cleanup.
            from app.jobs.dispatcher import _BACKGROUND_TASKS

            if _BACKGROUND_TASKS:
                await asyncio.gather(*list(_BACKGROUND_TASKS), return_exceptions=True)
            if task_id is not None:
                with tenant_scope(bypass=True):
                    await Job.objects.filter(initiated_by_user_id=user.id).delete()
                    await AgentTask.objects.delete(id=task_id)
            if user is not None:
                with tenant_scope(bypass=True):
                    # The personal account is RESTRICT on the user. A consent
                    # row is immutable by trigger, so a consenting fixture
                    # leaves it (and its workspace) behind: only rows this user
                    # merely *consented* can go, and only where it is not payer.
                    await TaskBillingPayer.objects.exclude(payer_user_id=user.id).filter(
                        consented_by_user_id=user.id
                    ).delete()
                    await PersonalBillingAccount.objects.filter(user_id=user.id).delete()
                await _delete_user(user.id)
            # The run leaves action-audit evidence with a RESTRICT FK on the
            # workspace: purge it through the supported retention path first.
            from tests.p11_t12_audit_cleanup import purge_tenant_audit_events

            await purge_tenant_audit_events(tenant_id)
            await tenants.delete_by_id(tenant_id)


async def test_a_direct_run_job_is_never_claimable_by_the_worker(client: AsyncClient) -> None:
    """`start_running` must leave nothing a worker's `claim_next` could take.

    This is the property that makes the direct run safe: the row is `running`
    before the handler is invoked, so there is no window in which a concurrent
    worker sees it as pending work and duplicates the side effects.
    """
    from app.models.managers.job_manager import JobManager
    from app.web.tasks import run_task_now

    async with await _client() as c:
        user, tenant_id = await _register(c, "RunNow Claim")
        task_id = None
        try:
            task = await _create_task(
                user,
                tenant_id,
                name=_name("direct"),
                job_type="digest",
                cron_expr="0 * * * *",
                payload={},
                is_active=True,
                next_run_at=None,
            )
            task_id = task.id
            outcome = await run_task_now(task)

            # Whatever the outcome, the finished row is not claimable.
            with tenant_scope(bypass=True):
                assert await JobManager.claim_job(outcome["job_id"]) is None
        finally:
            # The job points at the user who started it, so the task (and its
            # jobs) go first: the FK is not cascade-deleted. The queued run runs
            # in this loop; wait for it, or it inserts a job mid-cleanup.
            from app.jobs.dispatcher import _BACKGROUND_TASKS

            if _BACKGROUND_TASKS:
                await asyncio.gather(*list(_BACKGROUND_TASKS), return_exceptions=True)
            if task_id is not None:
                with tenant_scope(bypass=True):
                    await Job.objects.filter(initiated_by_user_id=user.id).delete()
                    await AgentTask.objects.delete(id=task_id)
            if user is not None:
                with tenant_scope(bypass=True):
                    # The personal account is RESTRICT on the user. A consent
                    # row is immutable by trigger, so a consenting fixture
                    # leaves it (and its workspace) behind: only rows this user
                    # merely *consented* can go, and only where it is not payer.
                    await TaskBillingPayer.objects.exclude(payer_user_id=user.id).filter(
                        consented_by_user_id=user.id
                    ).delete()
                    await PersonalBillingAccount.objects.filter(user_id=user.id).delete()
                await _delete_user(user.id)
            # The run leaves action-audit evidence with a RESTRICT FK on the
            # workspace: purge it through the supported retention path first.
            from tests.p11_t12_audit_cleanup import purge_tenant_audit_events

            await purge_tenant_audit_events(tenant_id)
            await tenants.delete_by_id(tenant_id)
