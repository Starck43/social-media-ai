"""New snapshot transaction tests against isolated PostgreSQL; no HTTP/LLM."""

import asyncio
from datetime import date
from uuid import uuid4

import pytest

from app.core.tenant_context import TenantContextError, tenant_scope
from app.models import AgentTask, DigestRun, Tenant, TenantChannel
from app.services.digest import snapshot_factory as module
from app.services.digest.checkpoint_store import locked_checkpoint
from app.services.digest.checkpoints import next_parts
from app.services.digest.snapshot_factory import SnapshotConflict, SnapshotError, create_snapshot

pytestmark = pytest.mark.tenancy


@pytest.fixture
async def workspace():
    tenant = await Tenant.objects.create(slug=f"snapshot-{uuid4().hex}", name="Snapshot", plan="business")
    try:
        yield tenant
    finally:
        await Tenant.objects.delete_by_id(tenant.id)


async def binding(tenant, **kwargs):
    return await TenantChannel.objects.create(
        tenant_id=tenant.id,
        channel=kwargs.get("channel", "telegram"),
        chat_id=kwargs.get("chat_id", f"snapshot-{uuid4().hex}"),
        is_active=kwargs.get("is_active", True),
        is_digest_target=kwargs.get("is_digest_target", True),
    )


def args(**changes):
    values = dict(
        content="<b>Frozen 😀 &amp; report</b>",
        period="day",
        period_start=date(2026, 10, 8),
        period_end=date(2026, 10, 8),
    )
    return {**values, **changes}


async def test_committed_snapshot_is_complete_and_usable_by_locked_store(workspace):
    target = await binding(workspace)
    with tenant_scope(workspace.id):
        ref = await create_snapshot(**args(llm_cost=0.12))
        row = await DigestRun.objects.get(id=ref.run_id)
        assert row.content == args()["content"] and row.llm_cost == 0.12
        assert row.period_start == row.period_end == date(2026, 10, 8)
        assert row.delivery_state["generation"] == ref.generation
        assert row.delivery_state["targets"][0]["binding_id"] == target.id
        async with locked_checkpoint(ref.run_id, generation=ref.generation) as store:
            state, parts = await store.load()
            assert next_parts(state) == [(0, 0)] and parts == [args()["content"]]


async def test_incomplete_flushed_row_is_never_visible_before_commit(workspace, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession

    await binding(workspace)
    real_flush, observed = AsyncSession.flush, []

    async def inspected_flush(session, *a, **kw):
        await real_flush(session, *a, **kw)
        for row in list(session.identity_map.values()):
            if isinstance(row, DigestRun):
                observed.append(row.id)
                assert await DigestRun.objects.get(id=row.id) is None

    monkeypatch.setattr(AsyncSession, "flush", inspected_flush)
    with tenant_scope(workspace.id):
        ref = await create_snapshot(**args())
        assert observed == [ref.run_id]
        assert (await DigestRun.objects.get(id=ref.run_id)).delivery_state is not None


async def test_failure_after_id_allocation_rolls_back_entire_snapshot(workspace, monkeypatch):
    await binding(workspace)

    def fail(**kwargs):
        raise RuntimeError("simulated checkpoint validation failure")

    monkeypatch.setattr(module, "new_checkpoint", fail)
    with tenant_scope(workspace.id):
        with pytest.raises(RuntimeError):
            await create_snapshot(**args())
        assert await DigestRun.objects.filter() == []


async def test_only_current_owned_active_targets_and_normalized_dedup(workspace):
    first = await binding(workspace, chat_id="@MixedCase")
    await binding(workspace, chat_id="@mixedcase")
    await binding(workspace, is_active=False)
    await binding(workspace, is_digest_target=False)
    other = await Tenant.objects.create(slug=f"foreign-snapshot-{uuid4().hex}", name="Other", plan="business")
    try:
        await binding(other)
        with tenant_scope(workspace.id):
            ref = await create_snapshot(**args())
            targets = (await DigestRun.objects.get(id=ref.run_id)).delivery_state["targets"]
            assert len(targets) == 1 and targets[0]["binding_id"] == first.id
            assert targets[0]["destination_id"] == "@mixedcase"
    finally:
        await Tenant.objects.delete_by_id(other.id)


async def test_no_targets_or_inactive_workspace_creates_no_run(workspace):
    with tenant_scope(workspace.id):
        with pytest.raises(SnapshotError, match="No owned"):
            await create_snapshot(**args())
        await binding(workspace)
        await Tenant.objects.update_by_id(workspace.id, is_active=False)
        with pytest.raises(TenantContextError):
            await create_snapshot(**args())
        assert await DigestRun.objects.filter() == []


async def test_unscoped_and_bypass_contexts_fail_closed(workspace):
    for scope in [dict(), dict(bypass=True), dict(tenant_id=workspace.id, bypass=True)]:
        with tenant_scope(**scope):
            with pytest.raises(TenantContextError):
                await create_snapshot(**args())


@pytest.mark.parametrize(
    "change",
    [
        {"period": "bad"},
        {"period_start": date(2026, 10, 9)},
        {"agent_task_id": True},
        {"llm_cost": -1},
        {"llm_cost": float("nan")},
        {"llm_cost": True},
    ],
)
async def test_bad_identity_window_or_cost_creates_no_run(workspace, change):
    await binding(workspace)
    with tenant_scope(workspace.id):
        with pytest.raises(SnapshotError):
            await create_snapshot(**args(**change))
        assert await DigestRun.objects.filter() == []


async def test_schedule_must_be_owned_and_digest_type(workspace):
    await binding(workspace)
    other = await Tenant.objects.create(slug=f"foreign-task-{uuid4().hex}", name="Other", plan="business")
    try:
        with tenant_scope(other.id):
            foreign = await AgentTask.objects.create(name="snapshot-task", cron_expr="0 9 * * *", job_type="digest")
        with tenant_scope(workspace.id):
            wrong = await AgentTask.objects.create(
                name="wrong-snapshot-task", cron_expr="0 9 * * *", job_type="collect"
            )
            for task_id in [foreign.id, wrong.id, 2**31 - 1]:
                with pytest.raises(TenantContextError):
                    await create_snapshot(**args(agent_task_id=task_id))
            assert await DigestRun.objects.filter() == []
    finally:
        await Tenant.objects.delete_by_id(other.id)


async def test_existing_schedule_window_is_never_overwritten_or_guessed_unsent(workspace):
    await binding(workspace)
    with tenant_scope(workspace.id):
        task = await AgentTask.objects.create(name="existing-snapshot", cron_expr="0 9 * * *", job_type="digest")
        old = await DigestRun.objects.create(
            agent_task_id=task.id,
            period="day",
            period_start=date(2026, 10, 8),
            period_end=date(2026, 10, 8),
            channel="auto",
            content="legacy",
            status="failed",
        )
        with pytest.raises(SnapshotConflict):
            await create_snapshot(**args(agent_task_id=task.id))
        row = await DigestRun.objects.get(id=old.id)
        assert row.content == "legacy" and row.delivery_state is None and row.status == "failed"
        assert len(await DigestRun.objects.filter()) == 1


async def test_competing_schedule_factories_create_one_complete_snapshot(workspace):
    await binding(workspace)
    with tenant_scope(workspace.id):
        task = await AgentTask.objects.create(name="parallel-snapshot", cron_expr="0 9 * * *", job_type="digest")
        results = await asyncio.wait_for(
            asyncio.gather(
                create_snapshot(**args(agent_task_id=task.id)),
                create_snapshot(**args(agent_task_id=task.id)),
                return_exceptions=True,
            ),
            timeout=5,
        )
        assert sum(isinstance(r, SnapshotConflict) for r in results) == 1
        rows = await DigestRun.objects.filter(agent_task_id=task.id)
        assert len(rows) == 1 and rows[0].delivery_state is not None


async def test_distinct_manual_calls_are_distinct_intentional_runs(workspace):
    await binding(workspace)
    with tenant_scope(workspace.id):
        first, second = await create_snapshot(**args()), await create_snapshot(**args())
        assert first.run_id != second.run_id and first.generation != second.generation
