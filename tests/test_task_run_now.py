"""Task create/update must not hang on "save and run", and must not 500 on a
repeated submission.

Reproduces the operator-visible failure: pressing "Сохранить и выполнить" on a
long `analyze` task left the page frozen for minutes (the handler ran inside the
request), the second press collided with the row the first press had already
written, and `uq_agent_task_tenant_name` surfaced as a 500 with a stack trace.

Also covers the retry ceiling that keeps an unanalysable staged item from being
offered on every run forever.

Real PostgreSQL (same policy as the rest of the suite).
"""

from __future__ import annotations

import re
import secrets

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import new_session
from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import AgentTask, CollectedItem, Job, Platform, Source, User
from app.models.managers.tenant_manager import TenantUserManager, tenants
from app.types import SourceType

CSRF_RE = re.compile(r'name="_csrf" value="([^"]+)"')

# Real tenancy, no platform-owner bypass: these tests create a task through the
# web form as a normal member, and where the task lands *is* what they check.
# Under the suite-wide bypass, `_apply_tenant` falls back to the bootstrap
# workspace and the tenant assertion below cannot mean anything.
pytestmark = pytest.mark.tenancy


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
    # The session picks `ids[0]` of these (`TenantUIMiddleware._active_tenant_id`),
    # which for a fresh registration is the workspace just created — not the
    # bootstrap one, whose membership sorts first by id.
    fresh = [m.tenant_id for m in memberships]
    return user, fresh[-1]


async def _make_source(tenant_id: int) -> Source:
    platform = await Platform.objects.filter(is_active=True).first()
    assert platform is not None, "the test database seeds platforms"
    with tenant_scope(bypass=True):
        return await Source.objects.create(
            name=_name("src"),
            platform_id=platform.id,
            external_id=_name("ext"),
            source_type=SourceType.USER,
            is_active=True,
            params={},
            tenant_id=tenant_id,
        )


async def _create(client: AsyncClient, task_name: str, source_id: int, run_now: bool = False, path="/app/tasks"):
    data = {
        "name": task_name,
        "job_type": "analyze",
        "source_ids": str(source_id),
        "_csrf": await _csrf(client, "/app/tasks"),
    }
    # The create form names the field `cron_custom`, the update form `cron_expr`.
    data["cron_custom" if path == "/app/tasks" else "cron_expr"] = "@once"
    if run_now:
        data["run_now"] = "on"
    return await client.post(path, data=data)


async def _drop(user: User | None, tenant_id: int | None) -> None:
    if user is not None:
        await User.objects.delete_user(user.id)
    if tenant_id is not None:
        await tenants.delete_by_id(tenant_id)


@pytest.fixture
async def client():
    app = create_application()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=True) as c:
        yield c


@pytest.mark.asyncio
async def test_saving_and_running_queues_the_job_instead_of_blocking(client):
    """ "Сохранить и выполнить" must return immediately, not run the job inline.

    The handler used to execute inside the POST, so a long job froze the page for
    minutes and the operator's second click hit the unique-name constraint. The
    response now comes back as soon as the job row exists, and the job is left
    `pending` for the worker rather than stamped `running` in-request.
    """
    user, tenant_id = await _register(client, "queue")
    source = await _make_source(tenant_id)
    task_name = _name("queued")
    try:
        assert tenant_id != 1, f"registered into the bootstrap workspace: {tenant_id}"
        resp = await _create(client, task_name, source.id, run_now=True)
        assert resp.status_code == 200
        assert "поставлена в очередь" in resp.text

        with tenant_scope(bypass=True):
            all_tasks = await AgentTask.objects.filter(name=task_name)
            assert all_tasks, f"no task named {task_name}"
            task = all_tasks[0]
            assert task.tenant_id == tenant_id, f"landed in tenant {task.tenant_id}, expected {tenant_id}"
            job = await Job.objects.filter(agent_task_id=task.id).order_by(Job.id.desc()).first()
        assert job is not None, "a job row is written so the modal can follow it"
        # Left for the worker: this is the behaviour that changed.
        assert job.status == "pending", f"expected a queued job, got {job.status}"
    finally:
        await _drop(user, tenant_id)


@pytest.mark.asyncio
async def test_a_repeated_submit_reports_the_clash_instead_of_a_500(client):
    """The second press must say "that name is taken", not raise IntegrityError.

    `(tenant_id, name)` is unique. Nothing caught the violation, so the retry
    after a slow first press came back as a 500 with a stack trace.
    """
    user, tenant_id = await _register(client, "dupe")
    source = await _make_source(tenant_id)
    task_name = _name("twice")
    try:
        first = await _create(client, task_name, source.id)
        assert first.status_code == 200

        second = await _create(client, task_name, source.id)
        assert second.status_code == 200, "a name clash is a page with a message, not a 500"
        assert "уже существует" in second.text

        with tenant_scope(bypass=True):
            names = await AgentTask.objects.filter(name=task_name, tenant_id=tenant_id)
        assert len(names) == 1, "the repeat must not create a second task"
    finally:
        await _drop(user, tenant_id)


@pytest.mark.asyncio
async def test_a_task_can_still_be_saved_under_its_own_name(client):
    """Saving a task under the name it already has is not a clash."""
    user, tenant_id = await _register(client, "selfname")
    source = await _make_source(tenant_id)
    task_name = _name("same")


@pytest.mark.asyncio
async def test_an_exhausted_item_is_no_longer_offered_for_analysis(client):
    """A staged row the LLM cannot process stops being retried forever.

    A timeout stores no `content_hash`, so the row is never retired and was
    handed out on every run — each attempt costing a full request timeout. Once
    `analyze_attempts` reaches the row's ceiling, `for_source` skips it.
    """
    user, tenant_id = await _register(client, "exhaust")
    source = await _make_source(tenant_id)
    digest = secrets.token_hex(16)
    try:
        with tenant_scope(bypass=True):
            row = await CollectedItem.objects.create(
                source_id=source.id,
                content_hash=digest,
                text="something the model will never accept",
                tenant_id=tenant_id,
            )

            # Offered while it still has attempts left (the ceiling is 3).
            assert [r.id for r in await CollectedItem.objects.for_source(source.id)] == [row.id]

            async def fail_once():
                session = new_session()
                try:
                    async with session.begin():
                        await CollectedItem.objects.record_attempts(session, source.id, [digest])
                finally:
                    await session.close()

            # Two misses: still retried, because the ceiling has not been reached.
            await fail_once()
            await fail_once()
            assert [r.id for r in await CollectedItem.objects.for_source(source.id)] == [row.id]

            # Third miss exhausts it: no longer offered...
            await fail_once()
            assert await CollectedItem.objects.for_source(source.id) == []
            # ...but still readable and still on disk: it is the only copy.
            kept = await CollectedItem.objects.for_source(source.id, include_exhausted=True)
            assert [r.id for r in kept] == [row.id]
            assert await CollectedItem.objects.exhausted_count(source.id) == 1

            # The ceiling is per row, so one backlog can be widened without a
            # redeploy.
            await CollectedItem.objects.update_by_id(row.id, give_up_after_attempts=10)
            assert [r.id for r in await CollectedItem.objects.for_source(source.id)] == [row.id]
    finally:
        with tenant_scope(bypass=True):
            await CollectedItem.objects.delete_by_id(row.id)
        await _drop(user, tenant_id)


@pytest.mark.asyncio
async def test_a_partially_analysed_batch_still_counts_the_rows_it_missed(client):
    """A partial result must not exempt the failed rows from the retry ceiling.

    The counter used to be recorded only when nothing at all was saved. On a
    partial analysis the rows that failed kept `analyze_attempts = 0` forever, so
    `for_source` kept handing them out on every run — each one costing a full
    request timeout, which is exactly the loop the ceiling exists to stop.
    `record_attempts` is an UPDATE keyed on the hash, so retiring the rows that
    did succeed leaves only the failures to be counted.
    """
    user, tenant_id = await _register(client, "partial")
    source = await _make_source(tenant_id)
    good, bad = secrets.token_hex(16), secrets.token_hex(16)
    try:
        with tenant_scope(bypass=True):
            saved = await CollectedItem.objects.create(
                source_id=source.id, content_hash=good, text="analysable", tenant_id=tenant_id
            )
            missed = await CollectedItem.objects.create(
                source_id=source.id, content_hash=bad, text="unanalysable", tenant_id=tenant_id
            )

            session = new_session()
            try:
                async with session.begin():
                    # The analysis stored only `good`, so only it is retired...
                    assert await CollectedItem.objects.delete_hashes(session, source.id, [good]) == 1
                    # ...and counting the batch touches only the row still there.
                    assert await CollectedItem.objects.record_attempts(session, source.id, [good, bad]) == 1
            finally:
                await session.close()

            remaining = await CollectedItem.objects.for_source(source.id, include_exhausted=True)
            assert [r.id for r in remaining] == [missed.id]
            assert remaining[0].analyze_attempts == 1, "the row that failed must accrue the attempt"
    finally:
        with tenant_scope(bypass=True):
            for digest in (good, bad):
                await CollectedItem.objects.delete(content_hash=digest, source_id=source.id)


@pytest.mark.asyncio
async def test_the_source_page_says_how_much_content_the_model_could_not_analyse(client):
    """Content that gave up must be visible, or it just looks like it was never collected.

    The retry ceiling stops handing the same unanalysable row to the model, which
    is the point — but a day that silently disappears from the source reads as
    "nothing was ever collected here". The page states the count instead.
    """
    user, tenant_id = await _register(client, "givenup")
    source = await _make_source(tenant_id)
    digest = secrets.token_hex(16)
    try:
        with tenant_scope(bypass=True):
            row = await CollectedItem.objects.create(
                source_id=source.id, content_hash=digest, text="never analysed", tenant_id=tenant_id
            )
            await CollectedItem.objects.update_by_id(row.id, give_up_after_attempts=0)

        page = await client.get(f"/app/sources/{source.id}")
        assert page.status_code == 200
        assert "не удалось проанализировать" in page.text
        assert "1 запись" in page.text, "the count must read as a person writes it"
    finally:
        with tenant_scope(bypass=True):
            await CollectedItem.objects.delete_by_id(row.id)
        await _drop(user, tenant_id)


@pytest.mark.asyncio
async def test_a_healthy_source_shows_no_such_warning(client):
    """The notice is for an exception, not a permanent fixture on the page."""
    user, tenant_id = await _register(client, "healthy")
    source = await _make_source(tenant_id)
    try:
        page = await client.get(f"/app/sources/{source.id}")
        assert page.status_code == 200
        assert "не удалось проанализировать" not in page.text
    finally:
        await _drop(user, tenant_id)
        await _drop(user, tenant_id)
