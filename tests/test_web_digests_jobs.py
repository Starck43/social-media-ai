"""`/app/digests` and `/app/jobs` — the two "what happened?" pages (stage E).

Both pages expose history plus one mutation, and the tests below aim at the
three things that could quietly be wrong:

* **scope** — an ordinary member must see only their workspace's rows. The
  managers' tenant guard is bypassed in tests (`tests/conftest.py`), so this is
  asserted through the page's own predicate, with two workspaces on screen.
* **the mutation is not a free action** — a read-only member gets no button and
  a refused direct POST (no `digest_runs` / `jobs` row written).
* **the distinction the pages exist for** — a preview never publishes and
  never summarises, and a non-retryable job cannot be re-run.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import DigestRun, Job, User
from app.models.managers.tenant_manager import TenantUserManager
from app.types import UserRoleType
from app.web.digests import valid_period
from app.web.jobs import RETRYABLE

CSRF_RE = re.compile(r'name="_csrf" value="([^"]+)"')
DENIED = "Недостаточно прав"
PASSWORD = "secret-password-1"


# ── pure rules ─────────────────────────────────────────────────────────────


def test_valid_period_falls_back_instead_of_passing_through() -> None:
    # `period` reaches the builder, which maps it to a date window — an unknown
    # value must not become a window, so it degrades to the default.
    assert valid_period("week") == "week"
    assert valid_period("month") == "month"
    assert valid_period("nonsense") == "day"
    assert valid_period(None) == "day"
    assert valid_period("../../../etc/passwd") == "day"


def test_only_failed_jobs_are_retryable() -> None:
    # `claim_job` returns None for anything not pending, so re-running a
    # `running` job would be a silent no-op and a `done` one is pointless.
    assert RETRYABLE == {"failed"}


def test_nav_ships_both_pages() -> None:
    from app.web.nav import NAV_ITEMS

    ready = {item.key: item.href for item in NAV_ITEMS if item.ready}
    assert ready["digests"] == "/app/digests"
    assert ready["jobs"] == "/app/jobs"


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


async def _register(client: AsyncClient, prefix: str) -> tuple[User, int]:
    """Sign up: the new user owns a fresh workspace (membership role `owner`)."""
    username = _name(prefix)
    token = await _csrf(client, "/app/register")
    resp = await client.post(
        "/app/register",
        data={
            "username": username,
            "email": f"{username}@example.test",
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


async def _invitee(username: str, role: UserRoleType, tenant_id: int, password: str = PASSWORD) -> User:
    """Create a user and attach them to `tenant_id` with a platform role."""
    from app.models.role import Role

    # `codename` is the enum column (stored as the member name); `name` is the
    # human label, so filtering on `name` would hand the enum to asyncpg.
    role_row = await Role.objects.get(codename=role.name)
    user = await User.objects.create_user(
        username=_name(username),
        email=f"{_name(username)}@example.test",
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


def _name(prefix: str) -> str:
    import secrets

    return f"{prefix}{secrets.token_hex(4)}"


async def _cleanup(*users: User | None) -> None:
    """Remove the test users and the workspaces they signed up into.

    The workspace matters as much as the user: `jobs` and `digest_runs` rows
    hang off the tenant, so leaving it behind would leak these fixtures into
    the next test's page.
    """
    from app.models.managers.tenant_manager import tenants

    for user in users:
        if user is None:
            continue
        memberships = await TenantUserManager().web_memberships(user.id)
        tenant_ids = [m.tenant_id for m in memberships]
        await User.objects.delete_user(user.id)
        for tenant_id in tenant_ids:
            await tenants.delete_by_id(tenant_id)


async def _seed_job(tenant_id: int, status: str, job_type: str = "collect", error: str | None = None) -> Job:
    """A job row in an explicit workspace (bypass: the tests disable the guard)."""
    with tenant_scope(bypass=True):
        return await Job.objects.create(
            tenant_id=tenant_id,
            job_type=job_type,
            status=status,
            run_at=datetime.now(timezone.utc),
            error=error,
        )


async def _seed_digest_run(tenant_id: int, status: str, channel: str = "telegram") -> DigestRun:
    today = date.today()
    with tenant_scope(bypass=True):
        return await DigestRun.objects.create(
            tenant_id=tenant_id,
            period="day",
            period_start=today,
            period_end=today + timedelta(days=1),
            channel=channel,
            chat_id="-100123",
            status=status,
            content="содержимое дайджеста",
            llm_cost=0.02 if status == "sent" else None,
        )


# ── /app/digests ───────────────────────────────────────────────────────────


async def test_digest_history_shows_runs_and_cost_for_the_workspace() -> None:
    owner: User | None = None
    try:
        async with await _client() as client:
            owner, tenant_id = await _register(client, "DigOwner")
            await _seed_digest_run(tenant_id, "sent")
            await _seed_digest_run(tenant_id, "failed")

            page = await client.get("/app/digests")
            assert page.status_code == 200
            assert "Дайджесты" in page.text
            # The run's LLM cost is summed for the header — the page reports spend.
            assert "0.02" in page.text
            assert "Дайджестов ещё не было" not in page.text
    finally:
        await _cleanup(owner)


async def test_digest_history_does_not_leak_another_workspace() -> None:
    owner: User | None = None
    other: User | None = None
    try:
        async with await _client() as mine, await _client() as theirs:
            owner, _my_tenant = await _register(mine, "DigMine")
            other, their_tenant = await _register(theirs, "DigTheirs")
            await _seed_digest_run(their_tenant, "sent")

            page = await mine.get("/app/digests")
            assert page.status_code == 200
            # The other workspace has a real `sent` run; it must not appear here.
            assert "Дайджестов ещё не было" in page.text
    finally:
        await _cleanup(other, owner)


async def test_preview_renders_without_publishing_or_summarising() -> None:
    owner: User | None = None
    try:
        async with await _client() as client:
            owner, tenant_id = await _register(client, "DigPrev")

            page = await client.get("/app/digests/preview?period=week")
            assert page.status_code == 200
            assert "Предпросмотр" in page.text
            # The label that keeps a preview from being mistaken for delivery.
            assert "без AI-сводки" in page.text

            with tenant_scope(bypass=True):
                runs = list(await DigestRun.objects.filter(tenant_id=tenant_id))
            assert runs == [], "a preview must not create a digest_runs row"
    finally:
        await _cleanup(owner)


async def test_unknown_preview_period_degrades_to_day() -> None:
    owner: User | None = None
    try:
        async with await _client() as client:
            owner, _ = await _register(client, "DigPeriod")
            resp = await client.get("/app/digests/preview?period=../../etc")
            assert resp.status_code == 200
    finally:
        await _cleanup(owner)


async def test_read_only_member_cannot_send_a_digest() -> None:
    owner: User | None = None


# ── /app/jobs ──────────────────────────────────────────────────────────────


async def test_jobs_page_lists_queue_health_and_failures() -> None:
    owner: User | None = None
    try:
        async with await _client() as client:
            owner, tenant_id = await _register(client, "JobOwner")
            await _seed_job(tenant_id, "pending")
            await _seed_job(tenant_id, "failed", error="connection reset by peer")

            page = await client.get("/app/jobs")
            assert page.status_code == 200
            assert "Задания" in page.text
            # The error text is the whole point of the page.
            assert "connection reset by peer" in page.text
    finally:
        await _cleanup(owner)


async def test_jobs_page_does_not_leak_another_workspace() -> None:
    owner: User | None = None
    other: User | None = None
    try:
        async with await _client() as mine, await _client() as theirs:
            owner, _my_tenant = await _register(mine, "JobMine")
            other, their_tenant = await _register(theirs, "JobTheirs")
            await _seed_job(their_tenant, "failed", error="СОСЕДНИЙ-ВОРКСПЕЙС")

            page = await mine.get("/app/jobs")
            assert page.status_code == 200
            assert "СОСЕДНИЙ-ВОРКСПЕЙС" not in page.text
    finally:
        await _cleanup(other, owner)


async def test_only_failed_jobs_offer_a_rerun_button() -> None:
    owner: User | None = None
    try:
        async with await _client() as client:
            owner, tenant_id = await _register(client, "JobBtn")
            failed = await _seed_job(tenant_id, "failed", error="boom")
            done = await _seed_job(tenant_id, "done")

            page = await client.get("/app/jobs")
            assert f'action="/app/jobs/{failed.id}/run"' in page.text
            # A finished job is not offered a re-run — that would be a no-op.
            assert f'action="/app/jobs/{done.id}/run"' not in page.text
    finally:
        await _cleanup(owner)


async def test_rerun_refuses_a_job_that_is_not_failed() -> None:
    owner: User | None = None
    try:
        async with await _client() as client:
            owner, tenant_id = await _register(client, "JobNoRun")
            done = await _seed_job(tenant_id, "done")

            token = await _csrf(client, "/app/jobs")
            resp = await client.post(f"/app/jobs/{done.id}/run", data={"_csrf": token})
            assert resp.status_code == 200
            assert "перезапуск не требуется" in resp.text

            with tenant_scope(bypass=True):
                assert (await Job.objects.get(id=done.id)).status == "done"
    finally:
        await _cleanup(owner)


async def test_read_only_member_cannot_rerun_a_job() -> None:
    owner: User | None = None
    viewer: User | None = None
    tenant_id = None
    job_id = None
    try:
        async with await _client() as client:
            owner, tenant_id = await _register(client, "JobROwner")
            viewer = await _invitee("JobRViewer", UserRoleType.VIEWER, tenant_id)
            job = await _seed_job(tenant_id, "failed", error="boom")
            job_id = job.id

        async with await _client() as client:
            await _login(client, viewer.username)
            page = await client.get("/app/jobs")
            assert page.status_code == 200
            assert f'action="/app/jobs/{job_id}/run"' not in page.text

            # Refused, and the failed row stays failed — no execution happened.
            token = CSRF_RE.search(page.text).group(1)
            resp = await client.post(f"/app/jobs/{job_id}/run", data={"_csrf": token})
            assert DENIED in resp.text
            with tenant_scope(bypass=True):
                assert (await Job.objects.get(id=job_id)).status == "failed"
    finally:
        await _cleanup(viewer, owner)
    viewer: User | None = None
    tenant_id = None
    try:
        async with await _client() as client:
            owner, tenant_id = await _register(client, "DigROwner")
            viewer = await _invitee("DigRViewer", UserRoleType.VIEWER, tenant_id)

        async with await _client() as client:
            await _login(client, viewer.username)
            page = await client.get("/app/digests")
            assert page.status_code == 200
            # No send button for a role without the `digestrun.create` right.
            assert 'action="/app/digests/send-now"' not in page.text

            # The button is not the check: a crafted POST is refused and publishes nothing.
            token = CSRF_RE.search(page.text).group(1)
            resp = await client.post("/app/digests/send-now", data={"period": "day", "_csrf": token})
            assert resp.status_code == 200
            assert DENIED in resp.text
            with tenant_scope(bypass=True):
                assert list(await DigestRun.objects.filter(tenant_id=tenant_id)) == []
    finally:
        await _cleanup(viewer, owner)
