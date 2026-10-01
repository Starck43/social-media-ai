"""`cli.main task run` — whose workflow runs, and whose job actually executes.

The CLI is developer mode: it bypasses the tenant guard and may touch every
workspace. What it must never do is *run the wrong thing* — an earlier queued job
of another workspace has to survive a manual run untouched, and a task has to run
in its own workspace.
"""

import secrets

import pytest
import typer

from app.core.tenant_context import tenant_scope
from app.models import AgentTask, Job, Platform, Source
from app.models.managers.job_manager import JobManager
from app.models.managers.tenant_manager import tenants
from app.types.enums.platform_types import SourceType
from cli.main import _add_task, _remove_task, _run_task

# The CLI is developer mode: these tests drive the real guard, opening
# `tenant_scope(bypass=True)` explicitly where the CLI would.
pytestmark = pytest.mark.tenancy

RUN_ARGS = dict(
    job_type="collect",
    sources=None,
    monitored=None,
    excluded=None,
    scenario=None,
    period=None,
    tenant=None,
)


async def _all(read):
    """Read across every workspace, the way the CLI does."""
    with tenant_scope(bypass=True):
        return await read


async def _cli(coro_fn, **kwargs):
    """Call a CLI command the way `cli.main._run_platform` does."""
    with tenant_scope(bypass=True):
        return await coro_fn(**kwargs)


def _uniq(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(4)}"


async def _make_task(tenant_id: int | None, name: str, cron: str = "0 9 * * *") -> AgentTask:
    """Create a task, in `tenant_id` or (None) in the bootstrap workspace."""
    scope = tenant_scope(bypass=True) if tenant_id is None else tenant_scope(tenant_id)
    with scope:
        return await AgentTask.objects.create(name=name, cron_expr=cron, timezone="UTC", job_type="collect", payload={})


async def _make_source(tenant_id: int) -> Source:
    platforms = await Platform.objects.all()
    with tenant_scope(tenant_id):
        return await Source.objects.create(
            platform_id=platforms[0].id,
            name=_uniq("src-"),
            source_type=SourceType.PUBLIC,
            external_id=_uniq("ext-"),
        )


@pytest.fixture(autouse=True)
def stub_handlers(monkeypatch):
    """Replace the real handlers: these tests are about *which* job runs."""

    async def fake_collect(payload):
        return {"ok": True}

    import app.jobs.dispatcher as dispatcher

    monkeypatch.setattr(dispatcher, "HANDLERS", {"collect": fake_collect})


async def test_task_run_executes_only_the_requested_job():
    """A manual run must not drain somebody else's older pending job."""
    my_task = await _make_task(None, _uniq("run-mine-"))
    other = await tenants.create(name=_uniq("WS "), slug=_uniq("ws-"))
    with tenant_scope(bypass=True):
        foreign = await JobManager().enqueue(job_type="collect", payload={}, tenant_id=other.id)

    await _cli(_run_task, task=str(my_task.id), **RUN_ARGS)

    assert (await _all(Job.objects.get(id=foreign.id))).status == "pending"


async def test_task_run_records_the_trigger():
    task = await _make_task(None, _uniq("run-once-"), cron="@once")

    await _cli(_run_task, task=str(task.id), **RUN_ARGS)

    fresh = await _all(AgentTask.objects.get(id=task.id))
    assert fresh.last_run_at is not None
    assert fresh.is_active is False  # a one-shot is disarmed by running it


async def test_task_run_reports_a_failing_job():
    import app.jobs.dispatcher as dispatcher

    async def boom(payload):
        raise RuntimeError("handler exploded")

    dispatcher.HANDLERS = {"collect": boom}
    task = await _make_task(None, _uniq("run-fail-"))

    with pytest.raises(typer.Exit) as exit_info:
        await _cli(_run_task, task=str(task.id), **RUN_ARGS)

    assert exit_info.value.exit_code == 1


async def test_task_run_uses_the_workspace_of_the_task():
    other = await tenants.create(name=_uniq("WS "), slug=_uniq("ws-"))
    task = await _make_task(other.id, _uniq("run-scoped-"))

    await _cli(_run_task, task=str(task.id), **RUN_ARGS)

    with tenant_scope(bypass=True):
        jobs = await Job.objects.filter(agent_task_id=task.id)
    assert [j.tenant_id for j in jobs] == [other.id]


async def test_one_off_run_lands_in_the_requested_workspace():
    other = await tenants.create(name=_uniq("WS "), slug=_uniq("ws-"))

    await _cli(_run_task, task=None, **{**RUN_ARGS, "tenant": other.slug})

    with tenant_scope(other.id):
        one_off = await AgentTask.objects.filter(name__startswith="one-off-collect-")
    assert one_off, "the one-off task was not created in the requested workspace"
    # A disarmed one-shot: a still-armed one would be re-enqueued by the scheduler.
    assert all(not t.is_active for t in one_off)


async def test_repeated_one_off_runs_do_not_collide():
    """Two one-offs in the same workspace must not violate the unique name."""
    other = await tenants.create(name=_uniq("WS "), slug=_uniq("ws-"))

    for _ in range(2):
        await _cli(_run_task, task=None, **{**RUN_ARGS, "tenant": other.slug})

    with tenant_scope(other.id):
        assert len(await AgentTask.objects.filter(name__startswith="one-off-collect-")) == 2


async def test_task_add_rejects_a_foreign_source():
    other = await tenants.create(name=_uniq("WS "), slug=_uniq("ws-"))
    foreign = await _make_source(other.id)
    name = _uniq("task-")

    with pytest.raises(typer.Exit):
        await _cli(
            _add_task,
            name=name,
            cron="@once",
            job_type="collect",
            parsed_payload={},
            parsed_source_ids=[foreign.id],
            scenario_id=None,
            tenant=None,
        )

    with tenant_scope(bypass=True):
        assert await AgentTask.objects.filter(name=name) == []


async def test_task_remove_covers_every_workspace_with_that_name():
    name = _uniq("dup-")
    await _make_task(None, name)
    other = await tenants.create(name=_uniq("WS "), slug=_uniq("ws-"))
    await _make_task(other.id, name)

    with tenant_scope(bypass=True):
        assert await _cli(_remove_task, name=name) == 2
        assert await AgentTask.objects.filter(name=name) == []
