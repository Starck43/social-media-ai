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

import asyncio
import re
import secrets
from html.parser import HTMLParser

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import new_session
from app.core.tenant_context import tenant_scope
from app.main import create_application
from app.models import AgentScenario, AgentTask, CollectedItem, Job, Platform, Source, User
from app.models.managers.tenant_manager import TenantUserManager, tenants
from app.types import SourceType

CSRF_RE = re.compile(r'name="_csrf" value="([^"]+)"')


class RunPanelParser(HTMLParser):
    """Read `#task-run-progress`: its status attributes, headline and run link."""

    def __init__(self):
        super().__init__()
        self.panel_attrs: dict[str, str] | None = None
        self.status_text = ""
        self.link_href = ""
        self.link_text = ""
        self._panel_depth = 0
        self._in_panel = False
        self._in_status = False
        self._status_parts: list[str] = []
        self._in_link = False
        self._link_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "div" and attributes.get("id") == "task-run-progress":
            self._in_panel = True
            self._panel_depth = 1
            self.panel_attrs = attributes
            return
        if not self._in_panel:
            return
        if tag == "div":
            self._panel_depth += 1
        elif tag == "p" and "font-medium" in attributes.get("class", "").split() and not self.status_text:
            self._in_status = True
            self._status_parts = []
        elif tag == "a" and attributes.get("href", "").startswith("/app/tasks?job_id="):
            self._in_link = True
            self.link_href = attributes["href"]
            self._link_parts = []

    def handle_endtag(self, tag):
        if not self._in_panel:
            return
        if tag == "a" and self._in_link:
            self._in_link = False
            self.link_text = " ".join("".join(self._link_parts).split())
        elif tag == "p" and self._in_status:
            self._in_status = False
            self.status_text = " ".join("".join(self._status_parts).split())
        elif tag == "div":
            self._panel_depth -= 1
            if self._panel_depth == 0:
                self._in_panel = False

    def handle_data(self, data):
        if self._in_status:
            self._status_parts.append(data)
        if self._in_link:
            self._link_parts.append(data)


class SourceStatsParser(HTMLParser):
    """Pair each big statistic value with the semantic label that follows it."""

    def __init__(self):
        super().__init__()
        self.in_stats = False
        self._in_heading = False
        self._heading_parts: list[str] = []
        self._in_value = False
        self._paragraph_class = ""
        self._paragraph_parts: list[str] = []
        self.paragraphs: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "h2":
            self._in_heading = True
            self._heading_parts = []
        elif tag == "p" and self.in_stats:
            self._in_value = True
            self._paragraph_class = attributes.get("class", "")
            self._paragraph_parts = []

    def handle_endtag(self, tag):
        if tag == "h2" and self._in_heading:
            self._in_heading = False
            self.in_stats = "Статистика источника" in " ".join(self._heading_parts)
        elif tag == "p" and self._in_value:
            self._in_value = False
            self.paragraphs.append((self._paragraph_class, " ".join("".join(self._paragraph_parts).split())))

    def handle_data(self, data):
        if self._in_heading:
            self._heading_parts.append(data)
        elif self._in_value:
            self._paragraph_parts.append(data)

    @property
    def metrics(self) -> dict[str, str]:
        values: dict[str, str] = {}
        for index, (classes, text) in enumerate(self.paragraphs):
            if "text-2xl" not in classes.split() or index + 1 >= len(self.paragraphs):
                continue
            values[self.paragraphs[index + 1][1]] = text
        return values


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


async def _ensure_default_scenario(tenant_id: int) -> None:
    """Provide the methodology required by unified web analysis fixtures."""
    with tenant_scope(bypass=True):
        existing = await AgentScenario.objects.get_default_scenario(tenant_id=tenant_id)
        if existing is None:
            await AgentScenario.objects.create(
                name=_name("default-scenario"),
                analysis_types=["themes"],
                is_active=True,
                is_default=True,
                tenant_id=tenant_id,
            )


async def _create(client: AsyncClient, task_name: str, source_id: int, run_now: bool = False, path="/app/tasks"):
    with tenant_scope(bypass=True):
        source = await Source.objects.get(id=source_id)
    assert source is not None
    await _ensure_default_scenario(source.tenant_id)
    data = {
        "name": task_name,
        "job_type": "analyze",
        "source_ids": str(source_id),
        "start_date": "2026-09-01",
        "_csrf": await _csrf(client, "/app/tasks"),
    }
    # The create form names the field `cron_custom`, the update form `cron_expr`.
    data["cron_custom" if path == "/app/tasks" else "cron_expr"] = "@once"
    if run_now:
        data["run_now"] = "on"
    return await client.post(path, data=data)


async def _drop(user: User | None, tenant_id: int | None) -> None:
    # Only fixture-owned data: the tenant cascade removes its attributed jobs
    # before the user RESTRICT FK is checked. Never clear actor IDs or delete by
    # actor across unrelated workspaces just to make teardown pass.
    if tenant_id is not None:
        await tenants.delete_by_id(tenant_id)
    if user is not None:
        await User.objects.delete_user(user.id)


@pytest.fixture
async def client():
    app = create_application()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", follow_redirects=True) as c:
        yield c


@pytest.mark.asyncio
async def test_saving_and_running_queues_the_job_instead_of_blocking(client, monkeypatch):
    """ "Сохранить и выполнить" enqueues the job and hands it to a background task.

    The handler used to execute inside the POST, so a long job froze the page for
    minutes and the operator's second click hit the unique-name constraint. The
    response now comes back as soon as the job row exists: the job is left
    `pending` and the request hands it to `launch_job_background` without running
    the handler synchronously. The handoff runs on the request's own event loop,
    so a manual run works without the `app.runtime` worker.
    """
    from app.jobs import dispatcher

    launches: list[tuple[int, bool]] = []

    def record_launch(job_id: int, *, allow_retry: bool = False) -> None:
        launches.append((job_id, allow_retry))

    monkeypatch.setattr(dispatcher, "launch_job_background", record_launch)

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
        # Nothing ran synchronously: the row is still queued for the background handoff.
        assert job.status == "pending", f"expected a queued job, got {job.status}"
        assert job.initiated_by_user_id == user.id, "manual enqueue must retain its authenticated actor"
        assert launches == [(job.id, False)], "the request must hand exactly this job to the background runner"
    finally:
        await _drop(user, tenant_id)


@pytest.mark.asyncio
async def test_source_admission_serializes_real_postgres_sessions_and_rolls_back(client, monkeypatch):
    from app.core.database import new_session
    from app.jobs.source_activity import SourceJobBusy
    from app.models.managers.job_manager import JobManager

    user, tenant_id = await _register(client, "admissionrace")
    source = await _make_source(tenant_id)
    first_locked = asyncio.Event()
    second_entered = asyncio.Event()
    release_first = asyncio.Event()
    calls = 0
    original_lock = JobManager.lock_source_admission

    async def held_lock(session, requested_tenant_id):
        nonlocal calls
        calls += 1
        if calls == 2:
            second_entered.set()
        await original_lock(session, requested_tenant_id)
        if calls == 1:
            first_locked.set()
            await release_first.wait()

    async def admit():
        with tenant_scope(tenant_id):
            session = new_session()
            try:
                async with session.begin():
                    return await Job.objects.admit_source_job("analyze", {"source_ids": [source.id]}, session=session)
            finally:
                await session.close()

    try:
        with tenant_scope(tenant_id):
            session = new_session()
            try:
                with pytest.raises(RuntimeError, match="abort after insert"):
                    async with session.begin():
                        await Job.objects.admit_source_job("analyze", {"source_ids": [source.id]}, session=session)
                        raise RuntimeError("abort after insert")
            finally:
                await session.close()
        with tenant_scope(tenant_id):
            assert await Job.objects.filter(job_type="analyze").count() == 0

        # Installed only after the rollback pre-check: that admission must take
        # the real lock and must not consume call #1, or it parks on
        # release_first before its RuntimeError can surface.
        monkeypatch.setattr(JobManager, "lock_source_admission", staticmethod(held_lock))

        first = asyncio.create_task(admit())
        await asyncio.wait_for(first_locked.wait(), timeout=5)
        second = asyncio.create_task(admit())
        await asyncio.wait_for(second_entered.wait(), timeout=5)
        release_first.set()
        results = await asyncio.wait_for(asyncio.gather(first, second, return_exceptions=True), timeout=10)
        assert sum(isinstance(value, SourceJobBusy) for value in results) == 1
        assert sum(isinstance(value, Job) for value in results) == 1
        with tenant_scope(tenant_id):
            assert await Job.objects.filter(job_type="analyze").count() == 1
    finally:
        release_first.set()
        await _drop(user, tenant_id)


@pytest.mark.asyncio
async def test_web_run_now_cannot_cross_workspace_scope(client):
    from app.core.permissions import service_permission_scope

    owner, owner_tenant_id = await _register(client, "admissionowner")
    other_client = AsyncClient(
        transport=ASGITransport(app=create_application()), base_url="http://testserver", follow_redirects=True
    )
    other = other_tenant_id = source = task = None
    try:
        other, other_tenant_id = await _register(other_client, "admissionother")
        source = await _make_source(owner_tenant_id)
        with tenant_scope(owner_tenant_id), service_permission_scope("agenttask", "create"):
            task = await AgentTask.objects.create(
                name=_name("private-task"),
                job_type="analyze",
                cron_expr="@once",
                payload={},
                is_active=False,
                tenant_id=owner_tenant_id,
            )
        with tenant_scope(owner_tenant_id), service_permission_scope("agenttask", "update"):
            from app.models.managers.agent_task_manager import AgentTaskManager

            await AgentTaskManager().add_sources(task.id, [source.id])

        response = await other_client.post(
            f"/app/tasks/{task.id}/run-now", data={"_csrf": await _csrf(other_client, "/app/tasks")}
        )
        assert response.status_code == 200
        assert task.name not in response.text
        with tenant_scope(owner_tenant_id):
            assert await Job.objects.filter(agent_task_id=task.id).count() == 0
    finally:
        await other_client.aclose()
        await _drop(other, other_tenant_id)
        await _drop(owner, owner_tenant_id)


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
    """Reopening and saving a task without renaming it updates that row."""
    user = None
    tenant_id = None
    try:
        user, tenant_id = await _register(client, "selfname")
        source = await _make_source(tenant_id)
        task_name = _name("same")

        created = await _create(client, task_name, source.id)
        assert created.status_code == 200
        with tenant_scope(bypass=True):
            task = await AgentTask.objects.filter(name=task_name, tenant_id=tenant_id).first()
        assert task is not None, f"no task named {task_name}"

        updated = await _create(client, task_name, source.id, path=f"/app/tasks/{task.id}")
        assert updated.status_code == 200
        # The client follows redirects, so inspect the rendered flash to detect
        # a duplicate-name refusal that also ends on a 200 list page.
        assert "уже существует" not in updated.text, "saving under its current name must not be refused"

        with tenant_scope(bypass=True):
            rows = await AgentTask.objects.filter(name=task_name, tenant_id=tenant_id)
        assert [row.id for row in rows] == [task.id], "the update must not create a second task"
        assert rows[0].name == task_name
    finally:
        await _drop(user, tenant_id)


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
    "nothing was ever collected here". The page states the numeric
    exhausted-attempt count instead (`попытки приостановлены`).
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
        parser = SourceStatsParser()
        parser.feed(page.text)
        metrics = parser.metrics
        assert metrics.get("попытки приостановлены") == "1", "the stopped-attempt count must be visible"
        assert metrics.get("сырых записей ждут анализа") == "0", "the stopped row is not waiting for analysis"
    finally:
        with tenant_scope(bypass=True):
            await CollectedItem.objects.delete_by_id(row.id)
        await _drop(user, tenant_id)


@pytest.mark.asyncio
async def test_a_healthy_source_shows_no_such_warning(client):
    """The stopped-attempt figure is a numeric metric, not an optional notice.

    The page always renders `попытки приостановлены` as a number, so a healthy
    source must show `0` — a missing value would mean the metric itself is
    broken, and the operator could not tell "nothing gave up" from "not
    reported here".
    """
    user, tenant_id = await _register(client, "healthy")
    source = await _make_source(tenant_id)
    try:
        page = await client.get(f"/app/sources/{source.id}")
        assert page.status_code == 200
        parser = SourceStatsParser()
        parser.feed(page.text)
        metrics = parser.metrics
        stopped = metrics.get("попытки приостановлены")
        assert stopped == "0", f"a healthy source must report a zero stopped-attempt count, got {stopped!r}"
    finally:
        await _drop(user, tenant_id)


@pytest.mark.asyncio
async def test_the_task_form_source_names_link_to_the_source_page(client):
    """The task editor names sources as links, so one click opens the source."""
    user, tenant_id = await _register(client, "srclink")
    source = await _make_source(tenant_id)
    try:
        page = await client.get("/app/tasks")
        assert page.status_code == 200
        assert f'href="/app/sources/{source.id}"' in page.text
        assert source.name in page.text
    finally:
        await _drop(user, tenant_id)


@pytest.mark.asyncio
async def test_task_source_link_escapes_names_and_keeps_checkbox_hit_area(client):
    from html import escape
    from html.parser import HTMLParser

    from app.core.permissions import service_permission_scope

    class SourceLinkParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.links = []

        def handle_starttag(self, tag, attrs):
            if tag == "a":
                self.links.append(dict(attrs))

    user, tenant_id = await _register(client, "srcsafe")
    try:
        source = await _make_source(tenant_id)
        hostile_name = "<script>alert(1)</script>"
        with tenant_scope(tenant_id), service_permission_scope("source", "update"):
            await Source.objects.update_by_id(source.id, name=hostile_name)
        page = await client.get("/app/tasks")
        assert page.status_code == 200
        parser = SourceLinkParser()
        parser.feed(page.text)
        link = next(link for link in parser.links if link.get("href") == f"/app/sources/{source.id}")
        assert {"pointer-events-auto", "relative", "z-10"} <= set(
            link["class"].split()
        ), "the source link must be clickable above the card's checkbox label"
        assert hostile_name not in page.text
        assert escape(hostile_name) in page.text
        assert f'id="add-src-{source.id}" name="source_ids" value="{source.id}"' in page.text
        assert f'<label for="add-src-{source.id}" class="absolute inset-0 cursor-pointer"></label>' in page.text
    finally:
        await _drop(user, tenant_id)


@pytest.mark.asyncio
async def test_checking_a_deactivated_source_activates_it_and_the_task(client):
    """A ticked-but-off source becomes active with the save.

    The operator checked it for a reason — it is the source this task should
    collect from — so it must not stay off, which would leave the task
    unactivatable and produce a "deactivated sources" warning right after the
    form flipped the exact thing that caused it.
    """
    user, tenant_id = await _register(client, "autoon")
    source = await _make_source(tenant_id)
    with tenant_scope(bypass=True):
        await Source.objects.update_by_id(source.id, is_active=False)
    task_name = _name("auto")
    try:
        resp = await _create(client, task_name, source.id)
        assert resp.status_code == 200
        assert "деактивированы" not in resp.text, "auto-activation must not leave a contradiction warning"

        with tenant_scope(bypass=True):
            fresh = await Source.objects.get(id=source.id)
            assert fresh.is_active, "the checked source must be active after the save"
            task = await AgentTask.objects.filter(name=task_name).first()
            assert task is not None and task.is_active, "the task must be activatable now"
    finally:
        await _drop(user, tenant_id)


@pytest.mark.asyncio
async def test_toggling_a_task_with_only_deactivated_sources_names_them(client):
    """The row power switch says *which* sources block activation, not a vague reason."""
    user, tenant_id = await _register(client, "toggle")
    source = await _make_source(tenant_id)
    task_name = _name("tog")
    try:
        await _create(client, task_name, source.id)
        with tenant_scope(bypass=True):
            task = await AgentTask.objects.filter(name=task_name).first()
            assert task is not None
            await AgentTask.objects.update_by_id(task.id, is_active=False)
            await Source.objects.update_by_id(source.id, is_active=False)

        token = await _csrf(client, "/app/tasks")
        resp = await client.post(f"/app/tasks/{task.id}/toggle", data={"_csrf": token})
        assert resp.status_code == 200
        assert "не активирована" in resp.text
        assert "все привязанные источники деактивированы" in resp.text
        assert source.name in resp.text, "the message must name the source that blocks activation"
    finally:
        await _drop(user, tenant_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("branch", ["owner-detail-link", "viewer-denied"])
async def test_task_list_escapes_stored_names_for_owner_and_denies_viewer(client, branch):
    """A stored hostile name is escaped for the owner and never reaches a denied viewer.

    The owner may open the task, so the name cell carries an escaped detail link
    (`/app/tasks/{id}`) — never the editor link, which the list does not offer.
    A viewer holds no `scheduling.task` rights, so `/app/tasks` answers 403 and
    the hostile name appears neither raw nor escaped.
    """
    from html import escape
    from html.parser import HTMLParser

    from app.core.permissions import service_permission_scope
    from app.models import Role
    from app.types import PlatformRoleType

    class TaskCellParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.in_name = False
            self.parts = []
            self.names = []
            self.links = []
            self.unsafe = []

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            if tag == "td" and "font-medium" in attributes.get("class", "").split():
                self.in_name = True
                self.parts = []
            if self.in_name:
                if tag == "a":
                    self.links.append(attributes.get("href"))
                if tag in {"script", "img", "iframe", "svg"} or any(key.startswith("on") for key in attributes):
                    self.unsafe.append((tag, attributes))

        def handle_endtag(self, tag):
            if tag == "td" and self.in_name:
                self.names.append("".join(self.parts).strip())
                self.in_name = False

        def handle_data(self, data):
            if self.in_name:
                self.parts.append(data)

    owner = viewer = None
    tenant_id = None
    hostile_name = 'Task <img src=x onerror=alert(1)> & "quoted"'
    try:
        owner, tenant_id = await _register(client, "taskname")
        with tenant_scope(tenant_id), service_permission_scope("agenttask", "create"):
            task = await AgentTask.objects.create(
                name=hostile_name,
                job_type="digest",
                cron_expr="@once",
                payload={},
                is_active=False,
                tenant_id=tenant_id,
            )
        if branch == "viewer-denied":
            role = await Role.objects.filter(codename=PlatformRoleType.USER.value).first()
            assert role is not None, "USER reference role must be seeded"
            username = _name("nameviewer")
            viewer = await User.objects.create_user(
                username=username,
                email=f"{username}@example.com",
                password="secret-password-1",
                role_id=role.id,
            )
            await TenantUserManager().add_web_member(tenant_id=tenant_id, user_id=viewer.id, role="viewer")
            async with AsyncClient(
                transport=ASGITransport(app=create_application()),
                base_url="http://testserver",
                follow_redirects=True,
            ) as viewer_client:
                token = await _csrf(viewer_client, "/app/login")
                login = await viewer_client.post(
                    "/app/login",
                    data={"username": username, "password": "secret-password-1", "_csrf": token},
                )
                assert login.status_code == 200
                page = await viewer_client.get("/app/tasks")
            assert page.status_code == 403, "a viewer without scheduling.task must not open the task list"
            assert hostile_name not in page.text
            assert escape(hostile_name) not in page.text
        else:
            page = await client.get("/app/tasks")
            assert page.status_code == 200
            parser = TaskCellParser()
            parser.feed(page.text)
            assert parser.names == [hostile_name], "escaping must preserve the exact stored name as readable text"
            assert not parser.unsafe, "stored task names must not produce executable tags or event attributes"
            assert hostile_name not in page.text
            assert "&lt;img" in page.text and "&amp;" in page.text
            assert f"/app/tasks/{task.id}" in parser.links, "the owner's name must link to the task detail page"
            assert f"/app/tasks/{task.id}/edit" not in parser.links, "the list is not the editor"

        with tenant_scope(tenant_id):
            stored = await AgentTask.objects.get(id=task.id)
            assert stored.name == hostile_name and not stored.is_active
            assert not await Job.objects.filter(agent_task_id=task.id), "rendering must not run the inert task"
    finally:
        if viewer is not None:
            await User.objects.delete_user(viewer.id)
        await _drop(owner, tenant_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "running"])
@pytest.mark.parametrize("fallback", [False, True], ids=["recent", "outside-recent-ten"])
async def test_task_detail_renders_active_job_and_sources_inside_authorized_scope(client, status, fallback):
    from datetime import datetime, timedelta, timezone

    from app.core.permissions import service_permission_scope

    owner = None
    tenant_id = None
    try:
        owner, tenant_id = await _register(client, "detailtarget")
        platform = await Platform.objects.filter(is_active=True).first()
        assert platform is not None
        with tenant_scope(tenant_id), service_permission_scope("source", "create"):
            source = await Source.objects.create(
                name=_name("detail-source"),
                platform_id=platform.id,
                external_id=_name("detail-ext"),
                source_type=SourceType.USER,
                is_active=True,
                params={},
                tenant_id=tenant_id,
            )
        with tenant_scope(tenant_id), service_permission_scope("agenttask", "create"):
            task = await AgentTask.objects.create(
                name=_name("detail-task"),
                job_type="collect",
                cron_expr="@once",
                payload={},
                is_active=False,
                tenant_id=tenant_id,
            )
        with tenant_scope(tenant_id), service_permission_scope("agenttask", "update"):
            await AgentTask.objects.set_sources(task.id, [source.id])
        now = datetime.now(timezone.utc)
        with tenant_scope(tenant_id):
            active_job = await Job.objects.create(
                agent_task_id=task.id,
                job_type=task.job_type,
                payload={},
                status=status,
                run_at=now - timedelta(minutes=10),
                created_at=now - timedelta(minutes=10),
                started_at=now - timedelta(minutes=10) if status == "running" else None,
                locked_at=now if status == "running" else None,
            )
            if fallback:
                for offset in range(10):
                    await Job.objects.create(
                        agent_task_id=task.id,
                        job_type=task.job_type,
                        payload={},
                        status="done",
                        run_at=now - timedelta(minutes=9),
                        created_at=now - timedelta(minutes=9) + timedelta(seconds=offset),
                    )
            recent = await Job.objects.filter(agent_task_id=task.id).order_by(Job.created_at.desc()).limit(10)
            assert (active_job.id not in {job.id for job in recent}) == fallback
        page = await client.get(f"/app/tasks/{task.id}")
        assert page.status_code == 200
        assert task.name in page.text
        assert f'href="/app/sources/{source.id}"' in page.text and source.name in page.text

        panel = RunPanelParser()
        panel.feed(page.text)
        assert panel.panel_attrs is not None, "the detail page must render #task-run-progress"
        assert panel.panel_attrs.get("data-task-busy") == "true"
        assert (
            panel.link_href == f"/app/tasks?job_id={active_job.id}"
        ), "recent and fallback paths must link the same active job"
        assert panel.link_text.replace("→", "").strip() == "Управление запуском"
        if status == "pending":
            assert panel.panel_attrs.get("data-task-status") == "pending"
            assert panel.status_text.startswith("Запуск в очереди")
        else:
            # A fresh worker heartbeat confirms execution, so the panel reports
            # the running state rather than the unconfirmed fallback.
            assert panel.panel_attrs.get("data-task-status") == "running"
            assert panel.status_text.startswith("Задача выполняется")
        with tenant_scope(tenant_id):
            unchanged = await Job.objects.get(id=active_job.id)
            assert unchanged.status == status and unchanged.started_at == active_job.started_at
            assert unchanged.locked_at == active_job.locked_at
            assert await Job.objects.filter(agent_task_id=task.id).count() == (11 if fallback else 1)
    finally:
        await _drop(owner, tenant_id)


@pytest.mark.asyncio
async def test_manual_rerun_replaces_unfinished_jobs(client, monkeypatch):
    """A second manual run replaces this task's unfinished jobs.

    One-off tasks (digest/prune and `@once` work) used to accumulate a new
    pending row on every press while the previous row was still pending or
    running. The replacement happens inside the admission transaction: the
    old rows are deleted before the new one is inserted, so the queue holds
    exactly one run of this task.
    """
    from datetime import datetime, timezone

    from app.jobs import dispatcher

    monkeypatch.setattr(dispatcher, "launch_job_background", lambda *args, **kwargs: None)

    user, tenant_id = await _register(client, "rerun")
    try:
        now = datetime.now(timezone.utc)
        with tenant_scope(bypass=True):
            task = await AgentTask.objects.create(
                name=_name("rerun-digest"),
                job_type="digest",
                cron_expr="@once",
                payload={"digest_period": "day"},
                is_active=False,
                tenant_id=tenant_id,
            )
            old_pending = await Job.objects.create(
                agent_task_id=task.id,
                tenant_id=tenant_id,
                job_type="digest",
                payload={},
                status="pending",
                run_at=now,
            )
            old_running = await Job.objects.create(
                agent_task_id=task.id,
                tenant_id=tenant_id,
                job_type="digest",
                payload={},
                status="running",
                run_at=now,
                attempts=1,
                locked_at=now,
                started_at=now,
            )

        token = await _csrf(client, "/app/tasks")
        response = await client.post(f"/app/tasks/{task.id}/run-now", data={"_csrf": token})
        assert response.status_code == 200

        with tenant_scope(bypass=True):
            jobs = await Job.objects.filter(agent_task_id=task.id)
        remaining = {job.id for job in jobs}
        assert old_pending.id not in remaining
        assert old_running.id not in remaining
        assert len(jobs) == 1, "the rerun must replace, not add to, unfinished jobs"
        assert jobs[0].status == "pending"
    finally:
        await _drop(user, tenant_id)
