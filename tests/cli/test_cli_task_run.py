"""Operator task CLI — explicit workspace targeting and correct job execution."""

import secrets
from contextlib import nullcontext

import pytest
import typer

from app.core.permissions import has_permission, operator_permission_scope, permission_scope, service_permission_scope
from app.core.tenant_context import current_tenant_id, is_bypass, tenant_scope
from app.models import AgentTask, Job, Notification, Platform, Source, Tenant
from app.models.managers.job_manager import JobManager
from app.models.managers.tenant_manager import tenants
from app.types.enums.platform_types import SourceType
from cli.main import _add_task, _remove_task, _run_task

# These tests call private task use-case helpers under the trusted operator
# scope, then verify their explicit workspace targets and actual job effects.
pytestmark = pytest.mark.tenancy

RUN_ARGS = dict(
    job_type="collect",
    sources=None,
    tracked=None,
    scenario=None,
    period=None,
    tenant="owner",
)


async def _all(read):
    """Read across workspaces for test assertions."""
    with tenant_scope(bypass=True):
        return await read


async def _cli(coro_fn, **kwargs):
    """Run a task helper under the operator grant used by the CLI boundary."""
    with tenant_scope(bypass=True), operator_permission_scope():
        return await coro_fn(**kwargs)


def _uniq(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(4)}"


async def _make_task(tenant_id: int | None, name: str, cron: str = "0 9 * * *") -> AgentTask:
    """Create a task, in `tenant_id` or (None) in the bootstrap workspace."""
    scope = tenant_scope(bypass=True) if tenant_id is None else tenant_scope(tenant_id)
    with scope:
        # Arrange only: no grant around command execution or foreign-source checks.
        grant = service_permission_scope("agenttask", "create") if tenant_id is not None else nullcontext()
        with grant:
            return await AgentTask.objects.create(name=name, cron_expr=cron, job_type="collect", payload={})


async def _make_source(tenant_id: int) -> Source:
    platforms = await Platform.objects.all()
    with tenant_scope(tenant_id):
        with service_permission_scope("source", "create"):
            return await Source.objects.create(
                platform_id=platforms[0].id,
                name=_uniq("src-"),
                source_type=SourceType.PUBLIC,
                external_id=_uniq("ext-"),
            )


async def _ids(model) -> set[int]:
    rows = await model.objects.all()
    return {row.id for row in rows}


@pytest.fixture(autouse=True)
async def clean_created_rows():
    """Remove only rows this test added to the serialized test schema."""
    with tenant_scope(bypass=True):
        before = {
            Job: await _ids(Job),
            AgentTask: await _ids(AgentTask),
            Source: await _ids(Source),
            Notification: await _ids(Notification),
            Tenant: await _ids(Tenant),
        }
    yield
    with tenant_scope(bypass=True), operator_permission_scope():
        # Jobs can outlive tasks, and tasks can retain source references.
        for model in (Job, AgentTask, Source, Notification, Tenant):
            after_ids = await _ids(model)
            for row_id in after_ids - before[model]:
                if model is Tenant:
                    await tenants.delete_by_id(row_id)
                else:
                    await model.objects.delete_by_id(row_id)


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
    await _make_source(my_task.tenant_id)
    other = await tenants.create(name=_uniq("WS "), slug=_uniq("ws-"))
    with tenant_scope(bypass=True):
        foreign = await JobManager().enqueue(job_type="collect", payload={}, tenant_id=other.id)

    await _cli(_run_task, task=str(my_task.id), **RUN_ARGS)

    assert (await _all(Job.objects.get(id=foreign.id))).status == "pending"


async def test_task_run_records_the_trigger():
    task = await _make_task(None, _uniq("run-once-"), cron="@once")
    await _make_source(task.tenant_id)

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
    await _make_source(task.tenant_id)

    with pytest.raises(typer.Exit) as exit_info:
        await _cli(_run_task, task=str(task.id), **RUN_ARGS)

    assert exit_info.value.exit_code == 1


async def test_task_run_uses_the_workspace_of_the_task():
    other = await tenants.create(name=_uniq("WS "), slug=_uniq("ws-"))
    # A workspace task needs something to work on: the runtime snapshots the
    # sources it would collect, and an empty workspace has none.
    await _make_source(other.id)
    task = await _make_task(other.id, _uniq("run-scoped-"))

    await _cli(_run_task, task=str(task.id), **{**RUN_ARGS, "tenant": other.slug})

    with tenant_scope(bypass=True):
        jobs = await Job.objects.filter(agent_task_id=task.id)
    assert [j.tenant_id for j in jobs] == [other.id]


async def test_one_off_run_lands_in_the_requested_workspace():
    other = await tenants.create(name=_uniq("WS "), slug=_uniq("ws-"))
    await _make_source(other.id)

    await _cli(_run_task, task=None, **{**RUN_ARGS, "tenant": other.slug})

    with tenant_scope(other.id):
        one_off = await AgentTask.objects.filter(name__startswith="one-off-collect-")
    assert one_off, "the one-off task was not created in the requested workspace"
    # A disarmed one-shot: a still-armed one would be re-enqueued by the scheduler.
    assert all(not t.is_active for t in one_off)


async def test_repeated_one_off_runs_do_not_collide():
    """Two one-offs in the same workspace must not violate the unique name."""
    other = await tenants.create(name=_uniq("WS "), slug=_uniq("ws-"))
    await _make_source(other.id)

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
            tenant="owner",
        )

    with tenant_scope(bypass=True):
        assert await AgentTask.objects.filter(name=name) == []


async def test_task_add_persists_target_params_in_payload():
    """The CLI's `--brands/--competitors/…` land on the task payload.

    Targets (brands, competitors, hashtags, …) belong on the task, not on the
    shared scenario — the CLI stores them in `payload` so a scheduled analyze
    job can feed them into the prompt instruction.
    """
    name = _uniq("task-targets-")
    await _cli(
        _add_task,
        name=name,
        cron="@once",
        job_type="analyze",
        parsed_payload={"brands": ["Арт-Сервис"], "keywords_list": ["клей"]},
        parsed_source_ids=[],
        scenario_id=None,
        tenant="owner",
    )

    task = await _all(AgentTask.objects.get(name=name))
    payload = dict(task.payload or {})
    assert payload["brands"] == ["Арт-Сервис"]
    assert payload["keywords_list"] == ["клей"]


async def test_task_remove_only_targets_the_selected_workspace():
    name = _uniq("dup-")
    selected = await _make_task(None, name)
    other = await tenants.create(name=_uniq("WS "), slug=_uniq("ws-"))
    foreign = await _make_task(other.id, name)

    with tenant_scope(bypass=True):
        assert await _cli(_remove_task, name=name, tenant="owner") == 1
        assert await AgentTask.objects.get(id=selected.id) is None
        assert (await AgentTask.objects.get(id=foreign.id)).tenant_id == other.id


async def test_cli_helper_preserves_operator_authority_under_narrowing_and_restores_scope():
    """Mirror the real entrypoint without granting authority to the whole test."""

    async def probe():
        with tenant_scope(31):
            assert current_tenant_id() == 31
            assert not is_bypass()
            assert has_permission(None, "agenttask", "create")

    with tenant_scope(31), permission_scope(None):
        assert not has_permission(None, "agenttask", "create")
        await _cli(probe)
        assert current_tenant_id() == 31
        assert not is_bypass()
        assert not has_permission(None, "agenttask", "create")


async def test_task_add_normalizes_the_collection_block():
    """CLI saves the same canonical block the web editor and API write."""
    name = _uniq("task-collection-")
    await _cli(
        _add_task,
        name=name,
        cron="@once",
        job_type="analyze",
        parsed_payload={
            "collection": {
                "content_types": ["posts", "comments"],
                "engagement_types": ["likes"],
                "limit": 250,
                "queries": ["доставка"],
                "city": {"name": "Киров", "mode": "prefer"},
            }
        },
        parsed_source_ids=[],
        scenario_id=None,
        tenant="owner",
    )

    task = await _all(AgentTask.objects.get(name=name))
    block = (task.payload or {})["collection"]
    assert block["version"] == 1
    assert block["content_types"] == ["posts", "comments"]
    assert block["engagement_types"] == ["likes"]
    assert block["limit"] == 250
    assert block["queries"] == ["доставка"]
    assert block["city"] == {"name": "Киров", "mode": "prefer"}
    assert block["comments"] == {"max_per_post": 100, "total": 2000}


async def test_task_add_rejects_a_malformed_collection_without_creating():
    name = _uniq("task-bad-collection-")
    with pytest.raises(typer.Exit):
        await _cli(
            _add_task,
            name=name,
            cron="@once",
            job_type="analyze",
            parsed_payload={"collection": {"limit": 0}},
            parsed_source_ids=[],
            scenario_id=None,
            tenant="owner",
        )

    with tenant_scope(bypass=True):
        assert await AgentTask.objects.filter(name=name) == []


async def test_task_add_drops_a_collection_for_a_non_collecting_job():
    name = _uniq("task-digest-collection-")
    await _cli(
        _add_task,
        name=name,
        cron="@once",
        job_type="digest",
        parsed_payload={"collection": {"content_types": ["posts"]}},
        parsed_source_ids=[],
        scenario_id=None,
        tenant="owner",
    )

    task = await _all(AgentTask.objects.get(name=name))
    assert "collection" not in (task.payload or {})
