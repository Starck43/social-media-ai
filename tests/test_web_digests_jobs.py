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
from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import DigestRun, Job, User
from app.models.managers.tenant_manager import TenantUserManager
from app.types import UserRoleType
from app.web.digests import valid_period
from app.web.jobs import RETRYABLE

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.agent_task import AgentTask

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


async def _seed_job(
    tenant_id: int,
    status: str,
    job_type: str = "collect",
    error: str | None = None,
    agent_task_id: int | None = None,
) -> Job:
    """A job row in an explicit workspace (bypass: the tests disable the guard)."""
    with tenant_scope(bypass=True):
        return await Job.objects.create(
            tenant_id=tenant_id,
            job_type=job_type,
            status=status,
            run_at=datetime.now(timezone.utc),
            error=error,
            agent_task_id=agent_task_id,
        )


async def _seed_task(tenant_id: int, name: str = "Сбор VK") -> "AgentTask":
    from app.models.agent_task import AgentTask

    with tenant_scope(bypass=True):
        return await AgentTask.objects.create(
            tenant_id=tenant_id,
            name=name,
            job_type="collect",
            cron_expr="0 * * * *",
            is_active=False,
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


async def _as_admin_plus(user: User) -> User:
    """`/app/jobs` is gated to a platform role above ADMIN — the queue tests act as one.

    The account stays the workspace owner it was; only the platform flag moves,
    which is exactly the half `User._is_superuser_role` checks first.
    """
    await User.objects.filter(id=user.id).update(is_superuser=True)
    return user


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


# ── /app/jobs ──────────────────────────────────────────────────────────────


async def test_queue_is_closed_below_the_admin_role() -> None:
    """Operator-only: the sidebar never offers the queue, the route refuses it."""
    owner: User | None = None
    try:
        async with await _client() as client:
            owner, tenant_id = await _register(client, "JobGate")
            await _seed_job(tenant_id, "failed", error="hidden")

            refused = await client.get("/app/jobs", follow_redirects=False)
            assert refused.status_code == 302
            assert refused.headers["location"] == "/app/"
            dash = await client.get("/app/")
            assert DENIED in dash.text, "the refusal says why"
            assert "Очередь задач" not in dash.text, "the sidebar must not offer the queue"

            # A platform role above ADMIN opens both — same account, no re-login.
            await _as_admin_plus(owner)
            page = await client.get("/app/jobs")
            assert page.status_code == 200
            assert "hidden" in page.text
            assert "Очередь задач" in page.text
    finally:
        await _cleanup(owner)


async def test_jobs_page_lists_queue_health_and_failures() -> None:
    owner: User | None = None
    try:
        async with await _client() as client:
            owner, tenant_id = await _register(client, "JobOwner")
            await _as_admin_plus(owner)
            await _seed_job(tenant_id, "pending")
            await _seed_job(tenant_id, "failed", error="connection reset by peer")

            page = await client.get("/app/jobs")
            assert page.status_code == 200
            assert "Задания" in page.text
            # The error text is the whole point of the page.
            assert "connection reset by peer" in page.text
            # The workspace column wears the product word — shown because this
            # owner now holds a role above ADMIN (the gate's own proof).
            assert ">Пространство<" in page.text
    finally:
        await _cleanup(owner)


async def test_jobs_page_does_not_leak_another_workspace() -> None:
    owner: User | None = None
    other: User | None = None
    try:
        async with await _client() as mine, await _client() as theirs:
            owner, _my_tenant = await _register(mine, "JobMine")
            await _as_admin_plus(owner)
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
            await _as_admin_plus(owner)
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
            await _as_admin_plus(owner)
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
    """Below the admin role the queue is closed: no page, no nav item, no POST."""
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
            # The page itself refuses — hiding the sidebar item is not the check.
            refused = await client.get("/app/jobs", follow_redirects=False)
            assert refused.status_code == 302
            dash = await client.get("/app/")
            assert "Очередь задач" not in dash.text, "the sidebar must not offer the queue"
            token = CSRF_RE.search(dash.text).group(1)

            # Refused, and the failed row stays failed — no execution happened.
            resp = await client.post(f"/app/jobs/{job_id}/run", data={"_csrf": token})
            assert DENIED in resp.text
            with tenant_scope(bypass=True):
                assert (await Job.objects.get(id=job_id)).status == "failed"
    finally:
        await _cleanup(viewer, owner)


# ── /app/jobs — cleanup ─────────────────────────────────────────────────────


async def test_row_delete_button_on_every_row_but_a_running_one() -> None:
    owner: User | None = None
    try:
        async with await _client() as client:
            owner, tenant_id = await _register(client, "JobDel")
            await _as_admin_plus(owner)
            pending = await _seed_job(tenant_id, "pending")
            failed = await _seed_job(tenant_id, "failed", error="boom")
            done = await _seed_job(tenant_id, "done")
            running = await _seed_job(tenant_id, "running")

            page = await client.get("/app/jobs")
            assert page.status_code == 200
            for job in (pending, failed, done):
                assert f'action="/app/jobs/{job.id}/delete"' in page.text
            # A claimed row belongs to the worker — no button, and the handler
            # refuses it too (see the next test).
            assert f'action="/app/jobs/{running.id}/delete"' not in page.text
    finally:
        await _cleanup(owner)


async def test_row_delete_removes_only_that_job() -> None:
    owner: User | None = None
    try:
        async with await _client() as client:
            owner, tenant_id = await _register(client, "JobDelOne")
            await _as_admin_plus(owner)
            target = await _seed_job(tenant_id, "done")
            neighbour = await _seed_job(tenant_id, "done")

            token = await _csrf(client, "/app/jobs")
            resp = await client.post(f"/app/jobs/{target.id}/delete", data={"_csrf": token})
            assert resp.status_code == 200
            assert f"#{target.id} удалено" in resp.text

            with tenant_scope(bypass=True):
                assert await Job.objects.get(id=target.id) is None
                assert await Job.objects.get(id=neighbour.id) is not None
    finally:
        await _cleanup(owner)


async def test_row_delete_refuses_a_running_job() -> None:
    owner: User | None = None
    try:
        async with await _client() as client:
            owner, tenant_id = await _register(client, "JobDelRun")
            await _as_admin_plus(owner)
            job = await _seed_job(tenant_id, "running")

            token = await _csrf(client, "/app/jobs")
            resp = await client.post(f"/app/jobs/{job.id}/delete", data={"_csrf": token})
            assert resp.status_code == 200
            assert "нельзя удалить" in resp.text

            with tenant_scope(bypass=True):
                assert (await Job.objects.get(id=job.id)).status == "running"
    finally:
        await _cleanup(owner)


async def test_clear_empties_the_queue_but_keeps_running_jobs() -> None:
    owner: User | None = None
    try:
        async with await _client() as client:
            owner, tenant_id = await _register(client, "JobClear")
            await _as_admin_plus(owner)
            await _seed_job(tenant_id, "done")
            await _seed_job(tenant_id, "failed", error="boom")
            await _seed_job(tenant_id, "pending")
            running = await _seed_job(tenant_id, "running")

            token = await _csrf(client, "/app/jobs")
            resp = await client.post("/app/jobs/clear", data={"_csrf": token})
            assert resp.status_code == 200
            # The count is what was removed, and the survivor is named — a
            # «clean slate» that silently kept a row would read as a bug.
            assert "Очищено: 3 задания" in resp.text
            assert "выполняющихся оставлено — 1" in resp.text

            with tenant_scope(bypass=True):
                left = list(await Job.objects.filter(tenant_id=tenant_id))
            assert [j.id for j in left] == [running.id]
    finally:
        await _cleanup(owner)


async def test_clear_does_not_reach_another_workspace() -> None:
    owner: User | None = None
    other: User | None = None
    try:
        async with await _client() as mine, await _client() as theirs:
            owner, _my_tenant = await _register(mine, "JobClearMine")
            await _as_admin_plus(owner)
            other, their_tenant = await _register(theirs, "JobClearTheirs")
            await _seed_job(their_tenant, "done")

            token = await _csrf(mine, "/app/jobs")
            resp = await mine.post("/app/jobs/clear", data={"_csrf": token})
            assert resp.status_code == 200
            assert "Очищено: 0 заданий" in resp.text

            with tenant_scope(bypass=True):
                assert len(list(await Job.objects.filter(tenant_id=their_tenant))) == 1
    finally:
        await _cleanup(other, owner)


async def test_clear_button_is_hidden_on_an_empty_queue() -> None:
    owner: User | None = None
    try:
        async with await _client() as client:
            owner, _tenant_id = await _register(client, "JobClearEmpty")
            await _as_admin_plus(owner)

            page = await client.get("/app/jobs")
            assert page.status_code == 200
            # Nothing to clear → no destructive control on an idle page.
            assert 'action="/app/jobs/clear"' not in page.text
    finally:
        await _cleanup(owner)


async def test_read_only_member_can_neither_delete_rows_nor_clear() -> None:
    owner: User | None = None
    viewer: User | None = None
    tenant_id = None
    job_id = None
    try:
        async with await _client() as client:
            owner, tenant_id = await _register(client, "JobDelOwner")
            viewer = await _invitee("JobDelViewer", UserRoleType.VIEWER, tenant_id)
            job = await _seed_job(tenant_id, "done")
            job_id = job.id

        async with await _client() as client:
            await _login(client, viewer.username)
            # The queue page itself is closed below the admin role...
            refused = await client.get("/app/jobs", follow_redirects=False)
            assert refused.status_code == 302

            # ...and a crafted POST is refused too — the hidden nav is not the check.
            dash = await client.get("/app/")
            token = CSRF_RE.search(dash.text).group(1)
            resp = await client.post(f"/app/jobs/{job_id}/delete", data={"_csrf": token})
            assert DENIED in resp.text
            resp = await client.post("/app/jobs/clear", data={"_csrf": token})
            assert DENIED in resp.text

            with tenant_scope(bypass=True):
                assert await Job.objects.get(id=job_id) is not None
    finally:
        await _cleanup(viewer, owner)


async def test_cleanup_without_csrf_is_refused() -> None:
    owner: User | None = None
    try:
        async with await _client() as client:
            owner, tenant_id = await _register(client, "JobDelCsrf")
            job = await _seed_job(tenant_id, "done")

            resp = await client.post(f"/app/jobs/{job.id}/delete", data={"_csrf": ""})
            assert resp.status_code == 200
            assert "Сессия истекла" in resp.text

            resp = await client.post("/app/jobs/clear", data={"_csrf": ""})
            assert "Сессия истекла" in resp.text

            with tenant_scope(bypass=True):
                assert await Job.objects.get(id=job.id) is not None
    finally:
        await _cleanup(owner)


async def test_row_delete_does_not_reach_another_workspace() -> None:
    owner: User | None = None
    other: User | None = None
    try:
        async with await _client() as mine, await _client() as theirs:
            owner, _my_tenant = await _register(mine, "JobDelMine")
            await _as_admin_plus(owner)
            other, their_tenant = await _register(theirs, "JobDelTheirs")
            theirs_job = await _seed_job(their_tenant, "done")

            token = await _csrf(mine, "/app/jobs")
            resp = await mine.post(f"/app/jobs/{theirs_job.id}/delete", data={"_csrf": token})
            assert resp.status_code == 200
            assert "не найдено" in resp.text

            with tenant_scope(bypass=True):
                assert await Job.objects.get(id=theirs_job.id) is not None
    finally:
        await _cleanup(other, owner)


# ── /app/jobs → the task's own page ─────────────────────────────────────────


async def test_queue_row_links_to_that_task_not_to_the_task_list() -> None:
    owner: User | None = None
    try:
        async with await _client() as client:
            owner, tenant_id = await _register(client, "JobLink")
            await _as_admin_plus(owner)
            task = await _seed_task(tenant_id)
            await _seed_job(tenant_id, "done", agent_task_id=task.id)

            page = await client.get("/app/jobs")
            assert page.status_code == 200
            # The row answers «which task ran and what happened», so the link has
            # to land on that task — `/app/tasks` alone dropped the reader on the
            # list with nothing selected.
            assert f'href="/app/tasks/{task.id}?from=jobs"' in page.text
    finally:
        await _cleanup(owner)


async def test_a_one_off_job_says_so_instead_of_a_broken_link() -> None:
    owner: User | None = None
    try:
        async with await _client() as client:
            owner, tenant_id = await _register(client, "JobOneOff")
            await _as_admin_plus(owner)
            await _seed_job(tenant_id, "done")

            page = await client.get("/app/jobs")
            assert page.status_code == 200
            # No `agent_task_id` → there is no task page to link to.
            assert "разовая" in page.text
            assert 'href="/app/tasks?' not in page.text
    finally:
        await _cleanup(owner)


async def test_task_card_opens_and_sends_you_back_to_the_queue() -> None:
    owner: User | None = None
    try:
        async with await _client() as client:
            owner, tenant_id = await _register(client, "JobCard")
            await _as_admin_plus(owner)
            task = await _seed_task(tenant_id)
            await _seed_job(tenant_id, "done", agent_task_id=task.id)

            link = re.search(r'href="(/app/tasks/\d+\?from=jobs)"', (await client.get("/app/jobs")).text).group(1)

            card = await client.get(link)
            assert card.status_code == 200
            # Regression: the card used to 500 here for EVERY task — the m2m
            # lookup read a column that does not exist.
            assert task.name in card.text
            # Arrived from the queue → the way back is the queue, not the list.
            assert "← Очередь заданий" in card.text
            assert "← Задачи" not in card.text

            # Opened directly, the breadcrumb is the task list as before.
            direct = await client.get(f"/app/tasks/{task.id}")
            assert "← Задачи" in direct.text
            assert "← Очередь заданий" not in direct.text
    finally:
        await _cleanup(owner)


async def test_unknown_from_parameter_does_not_become_a_link() -> None:
    owner: User | None = None
    try:
        async with await _client() as client:
            owner, tenant_id = await _register(client, "JobSpoof")
            task = await _seed_task(tenant_id)

            resp = await client.get(f"/app/tasks/{task.id}?from=../../etc/passwd")
            assert resp.status_code == 200
            # The parameter names a page, not a URL: an unknown value falls back
            # to the task list instead of being echoed into an href.
            assert "← Задачи" in resp.text
            assert "/etc/passwd" not in resp.text
    finally:
        await _cleanup(owner)
