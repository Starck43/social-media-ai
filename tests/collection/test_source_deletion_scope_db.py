"""Deleting a source is refused while an active task still reads it.

`agent_task_sources` is a plain m2m: an empty link set is not "no sources" but
"every active source of the workspace" (`_resolve_sources`). The first guard
against that silent widening auto-paused a task that lost its last explicit
source, but it stopped a schedule the operator never touched: the delete is
now refused outright while any linked task is active, and the source is
removed inside that task, where the scope is visible. These checks pin the
contract of the single delete path
(`app/services/collection/source_deletion.py`): a refusal writes nothing and
names the tasks standing in the way, an inactive task never blocks, and the
delete's locks (source row, then task rows) keep a concurrent edit or
activation from interleaving with the decision — an activation either lands
first and blocks the delete, or waits it out; it never slips inside.

Real workspace scope (no bypass) because the decision goes through the task
rows and the source-admission lock, both of which require the owning tenant.
"""

from __future__ import annotations

import secrets
import typing
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import service_permission_scope
from app.core.tenant_context import tenant_scope
from app.models import AgentTask, Job, Platform, Source, Tenant
from app.models.managers.job_manager import JobManager
from app.services.collection.source_deletion import (
    SourceDeleteBlocked,
    SourceDeletion,
    TaskRef,
    delete_source,
)

pytestmark = pytest.mark.tenancy

NOW = datetime.now(timezone.utc)


async def _tenant() -> Tenant:
    return await Tenant.objects.create(
        name="Source delete scope", slug=f"src-del-{secrets.token_hex(5)}", plan="business"
    )


async def _platform_id() -> int:
    platform = await Platform.objects.filter(is_active=True).first()
    assert platform is not None, "the test database seeds platforms"
    return platform.id


async def _source(tenant_id: int, name: str) -> Source:
    from app.types import SourceType

    platform_id = await _platform_id()
    with tenant_scope(tenant_id), service_permission_scope("source", "create"):
        return await Source.objects.create(
            platform_id=platform_id,
            name=name,
            external_id=f"del-{secrets.token_hex(6)}",
            source_type=SourceType.GROUP,
        )


async def _task(tenant_id: int, name: str, *, is_active: bool = True) -> AgentTask:
    with tenant_scope(tenant_id), service_permission_scope("agenttask", "create"):
        return await AgentTask.objects.create(
            name=f"{name}-{secrets.token_hex(4)}",
            job_type="collect",
            cron_expr="0 9 * * *",
            payload={},
            is_active=is_active,
        )


async def _link(task: AgentTask, source_ids: list[int]) -> None:
    from app.models.managers.agent_task_manager import AgentTaskManager

    with tenant_scope(task.tenant_id), service_permission_scope("agenttask", "update"):
        await AgentTaskManager().set_sources(task.id, source_ids)


async def _pause(tenant_id: int, task_id: int) -> None:
    """Stop a task the way the task form does — the operator's own move."""
    from app.models.managers.agent_task_manager import AgentTaskManager

    with tenant_scope(tenant_id), service_permission_scope("agenttask", "update"):
        await AgentTaskManager().update_by_id(task_id, is_active=False)


async def _activate(tenant_id: int, task_id: int) -> None:
    """Resume a task the way the task form does — the operator's own move."""
    from app.models.managers.agent_task_manager import AgentTaskManager

    with tenant_scope(tenant_id), service_permission_scope("agenttask", "update"):
        await AgentTaskManager().update_by_id(task_id, is_active=True)


async def _delete(tenant_id: int, source_id: int):
    session = async_session_maker()
    try:
        with tenant_scope(tenant_id):
            async with session.begin():
                return await delete_source(session, source_id=source_id, tenant_id=tenant_id)
    finally:
        await session.close()


async def _task_row(tenant_id: int, task_id: int) -> AgentTask | None:
    with tenant_scope(tenant_id):
        return await AgentTask.objects.get(id=task_id)


async def _links(tenant_id: int, task_id: int) -> list[int]:
    from app.models.managers.agent_task_manager import AgentTaskManager

    with tenant_scope(tenant_id):
        return await AgentTaskManager().get_sources(task_id)


async def test_an_active_tasks_last_explicit_source_refuses_the_delete():
    """The only named source of a running task stays: the task keeps its scope.

    Letting the last explicit link disappear would hand the running task every
    active source of the workspace. The delete is refused instead: nothing is
    written, and the receipt names the task standing in the way.
    """
    tenant = await _tenant()
    try:
        task = await _task(tenant.id, "narrow")
        source = await _source(tenant.id, "only-source")
        await _link(task, [source.id])

        removal = await _delete(tenant.id, source.id)

        assert removal == SourceDeleteBlocked(
            source_id=source.id,
            name="only-source",
            active_tasks=(TaskRef(task_id=task.id, name=task.name),),
        )
        row = await _task_row(tenant.id, task.id)
        assert row is not None
        assert row.is_active is True
        assert row.last_error is None
        assert await _links(tenant.id, task.id) == [source.id]
        with tenant_scope(tenant.id):
            assert await Source.objects.get(id=source.id) is not None
    finally:
        await Tenant.objects.delete_by_id(tenant.id)


async def test_an_active_link_is_refused_even_when_the_task_keeps_another_source():
    """The refusal is per active reader, not only for a last-source task.

    A running task that also names a kept source still reads the doomed one,
    so nothing is written while it runs. Once the operator stops the task the
    delete goes through, the surviving link keeps the scope explicit, and the
    task is neither paused nor widened by the delete.
    """
    tenant = await _tenant()
    try:
        task = await _task(tenant.id, "two-sources")
        kept = await _source(tenant.id, "kept")
        doomed = await _source(tenant.id, "doomed")
        await _link(task, [kept.id, doomed.id])

        removal = await _delete(tenant.id, doomed.id)

        assert removal == SourceDeleteBlocked(
            source_id=doomed.id,
            name="doomed",
            active_tasks=(TaskRef(task_id=task.id, name=task.name),),
        )
        row = await _task_row(tenant.id, task.id)
        assert row is not None and row.is_active is True
        assert await _links(tenant.id, task.id) == [kept.id, doomed.id]
        with tenant_scope(tenant.id):
            assert await Source.objects.get(id=doomed.id) is not None

        # The operator stops the task first; then the delete goes through and
        # the remaining link keeps the scope explicit — no pause receipt, no
        # pause note on the task.
        await _pause(tenant.id, task.id)
        removal = await _delete(tenant.id, doomed.id)
        assert removal == SourceDeletion(source_id=doomed.id, name="doomed", paused_tasks=())
        row = await _task_row(tenant.id, task.id)
        assert row is not None and row.is_active is False
        assert row.last_error is None
        assert await _links(tenant.id, task.id) == [kept.id]
    finally:
        await Tenant.objects.delete_by_id(tenant.id)


async def test_all_active_task_is_untouched():
    """A task that never named sources has no explicit scope to preserve."""
    tenant = await _tenant()
    try:
        task = await _task(tenant.id, "all-active")
        source = await _source(tenant.id, "generic")
        assert await _links(tenant.id, task.id) == []

        removal = await _delete(tenant.id, source.id)

        assert removal is not None
        assert removal.paused_tasks == ()
        row = await _task_row(tenant.id, task.id)
        assert row is not None
        assert row.is_active is True
        assert row.last_error is None
    finally:
        await Tenant.objects.delete_by_id(tenant.id)


async def test_already_paused_task_is_not_rewritten():
    """A paused task is not a widening risk, so the delete leaves its receipt alone."""
    tenant = await _tenant()
    try:
        task = await _task(tenant.id, "paused", is_active=False)
        source = await _source(tenant.id, "paused-source")
        await _link(task, [source.id])

        removal = await _delete(tenant.id, source.id)

        assert removal is not None
        assert removal.paused_tasks == ()
        row = await _task_row(tenant.id, task.id)
        assert row is not None
        assert row.is_active is False
        assert row.last_error is None
    finally:
        await Tenant.objects.delete_by_id(tenant.id)


async def test_a_queued_occurrence_of_a_running_task_keeps_its_source():
    """A `pending` occurrence never runs against a deleted source.

    The occurrence was admitted for this source under the task's plan. With
    the task still active the delete is refused — nothing is written, so the
    queued row, the link and the source all stand until the operator stops
    the task (which is what drops the occurrence) before retiring the source.
    """
    tenant = await _tenant()
    try:
        task = await _task(tenant.id, "queued")
        source = await _source(tenant.id, "queued-source")
        await _link(task, [source.id])
        with tenant_scope(tenant.id):
            pending = await JobManager().enqueue("collect", agent_task_id=task.id, run_at=NOW, max_attempts=3)
        assert pending is not None

        removal = await _delete(tenant.id, source.id)

        assert removal == SourceDeleteBlocked(
            source_id=source.id,
            name="queued-source",
            active_tasks=(TaskRef(task_id=task.id, name=task.name),),
        )
        with tenant_scope(tenant.id):
            assert await Job.objects.get(id=pending.id) is not None
            assert await Source.objects.get(id=source.id) is not None
        row = await _task_row(tenant.id, task.id)
        assert row is not None and row.is_active is True
    finally:
        # The refusal kept the queued row: clear the tenant's queue before the
        # tenant delete, the same way the occurrence suite does.
        with tenant_scope(tenant.id):
            await Job.objects.delete()
        await Tenant.objects.delete_by_id(tenant.id)


async def test_another_workspaces_source_is_reported_missing_and_survives():
    """The workspace boundary is part of the service, not just the web route."""
    owner = await _tenant()
    other = await _tenant()
    try:
        source = await _source(owner.id, "private")
        task = await _task(owner.id, "owner-task")
        await _link(task, [source.id])

        assert await _delete(other.id, source.id) is None

        with tenant_scope(owner.id):
            assert await Source.objects.get(id=source.id) is not None
        row = await _task_row(owner.id, task.id)
        assert row is not None and row.is_active is True
    finally:
        await Tenant.objects.delete_by_id(owner.id)
        await Tenant.objects.delete_by_id(other.id)


async def test_a_refused_delete_still_serializes_editors_on_the_task_row():
    """The refusal decision holds the task row, so an editor cannot slip between.

    `SELECT ... FOR UPDATE NOWAIT` from a second session is the editor's lock
    attempt: while the delete transaction is open — refusing, writing nothing —
    it must be refused, and once that transaction has committed the same read
    must succeed. An activation or link edit therefore lands wholly before or
    wholly after the delete's view of the links, never between them. Proven
    against PostgreSQL, not a simulated lock.
    """
    tenant = await _tenant()
    try:
        task = await _task(tenant.id, "contended")
        source = await _source(tenant.id, "contended-source")
        await _link(task, [source.id])

        # Schema-qualified: the raw statement resolves the table without the
        # ORM's schema, and an unqualified name here silently checks a
        # different schema's empty table.
        lock_task = text(f"SELECT id FROM {settings.DB_SCHEMA}.agent_tasks WHERE id = :task_id FOR UPDATE NOWAIT")

        deleting = async_session_maker()
        try:
            with tenant_scope(tenant.id):
                transaction = await deleting.begin()
                removal = await delete_source(deleting, source_id=source.id, tenant_id=tenant.id)
                assert isinstance(removal, SourceDeleteBlocked)

                contender = async_session_maker()
                try:
                    with tenant_scope(tenant.id):
                        with pytest.raises(DBAPIError):
                            await contender.execute(lock_task, {"task_id": task.id})
                finally:
                    await contender.rollback()
                    await contender.close()

                await transaction.commit()
        finally:
            await deleting.close()

        # The refusal committed without writing a row: the same lock attempt
        # now succeeds — the editor acts after the decision, never inside it.
        after = async_session_maker()
        try:
            with tenant_scope(tenant.id):
                await after.execute(lock_task, {"task_id": task.id})
        finally:
            await after.rollback()
            await after.close()

        row = await _task_row(tenant.id, task.id)
        assert row is not None and row.is_active is True
        with tenant_scope(tenant.id):
            assert await Source.objects.get(id=source.id) is not None
    finally:
        await Tenant.objects.delete_by_id(tenant.id)


async def _wait_until_a_lock_request_is_queued(timeout: float = 8.0) -> bool:
    """Wait until some session is blocked waiting for a lock; False on timeout."""
    import asyncio

    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        session = async_session_maker()
        try:
            waiting = (await session.execute(text("SELECT count(*) FROM pg_locks WHERE NOT granted"))).scalar_one()
        finally:
            await session.close()
        if waiting:
            return True
        await asyncio.sleep(0.05)
    return False


async def test_a_competing_source_link_edit_waits_out_the_refusal():
    """A real editor — not a raw probe — participates in the same lock order.

    The refusal still holds the workspace admission gate and the task row
    while it decides. A competing `set_sources` from another session must wait
    for it, then land with the source still in place and the task still
    running: the editor's links replace the old set wholesale, on one side of
    the decision. Only once the live link is gone does the same delete go
    through — never in between, under a task that still reads the source.
    """
    import asyncio

    from app.models.managers.agent_task_manager import AgentTaskManager

    tenant = await _tenant()
    try:
        task = await _task(tenant.id, "edited-while-deleting")
        doomed = await _source(tenant.id, "doomed")
        replacement = await _source(tenant.id, "replacement")
        await _link(task, [doomed.id])

        async def _edit_links() -> None:
            with tenant_scope(tenant.id), service_permission_scope("agenttask", "update"):
                await AgentTaskManager().set_sources(task.id, [replacement.id])

        deleting = async_session_maker()
        try:
            with tenant_scope(tenant.id):
                transaction = await deleting.begin()
                removal = await delete_source(deleting, source_id=doomed.id, tenant_id=tenant.id)
                assert isinstance(removal, SourceDeleteBlocked)

                editing = asyncio.create_task(_edit_links())
                try:
                    assert await _wait_until_a_lock_request_is_queued(), "the competing edit never waited"
                    assert not editing.done(), "the edit committed while the refusal was still open"
                finally:
                    await transaction.commit()
        finally:
            await deleting.close()

        await asyncio.wait_for(editing, timeout=10)
        row = await _task_row(tenant.id, task.id)
        assert row is not None
        assert row.is_active is True, "neither the refusal nor the later edit may stop the task"
        assert await _links(tenant.id, task.id) == [replacement.id]
        with tenant_scope(tenant.id):
            assert await Source.objects.get(id=doomed.id) is not None, "the refusal kept the source"

        # The edit removed the last live link, so the delete now succeeds; the
        # task keeps running on exactly what the editor wrote.
        removal = await _delete(tenant.id, doomed.id)
        assert removal == SourceDeletion(source_id=doomed.id, name="doomed", paused_tasks=())
        row = await _task_row(tenant.id, task.id)
        assert row is not None and row.is_active is True
        assert await _links(tenant.id, task.id) == [replacement.id]
    finally:
        await Tenant.objects.delete_by_id(tenant.id)


async def test_a_concurrent_activation_waits_out_the_deleting_transaction():
    """An activation cannot slip inside the delete's decision window.

    The task starts paused, so this delete may go through. A concurrent
    activation must wait for the deleting transaction: it lands only after the
    source — and, by cascade, the link — is gone, so the running task never
    reads a deleted source. Had the activation committed first, the delete
    would have refused instead: one side or the other, never in between.
    """
    import asyncio

    tenant = await _tenant()
    try:
        task = await _task(tenant.id, "activating", is_active=False)
        source = await _source(tenant.id, "activating-source")
        await _link(task, [source.id])

        deleting = async_session_maker()
        try:
            with tenant_scope(tenant.id):
                transaction = await deleting.begin()
                removal = await delete_source(deleting, source_id=source.id, tenant_id=tenant.id)
                assert isinstance(removal, SourceDeletion)
                assert removal.paused_tasks == ()

                activating = asyncio.create_task(_activate(tenant.id, task.id))
                try:
                    assert await _wait_until_a_lock_request_is_queued(), "the activation never waited"
                    assert not activating.done(), "the activation landed while the delete was still open"
                finally:
                    await transaction.commit()
        finally:
            await deleting.close()

        await asyncio.wait_for(activating, timeout=10)
        row = await _task_row(tenant.id, task.id)
        assert row is not None and row.is_active is True
        assert await _links(tenant.id, task.id) == [], "the link went with the row, not into a deleted source"
        with tenant_scope(tenant.id):
            assert await Source.objects.get(id=source.id) is None
    finally:
        await Tenant.objects.delete_by_id(tenant.id)


def test_both_receipts_name_tasks_as_task_ref():
    """`TaskRef` is the single task reference both receipts carry.

    The pause-era `PausedTask` class is gone from the service: a blocked delete
    names its active readers, a completed one reports what it paused — always
    nothing now that the refusal comes first — and both fields are tuples of
    `TaskRef(task_id, name)`.
    """
    import app.services.collection.source_deletion as service_module

    assert not hasattr(service_module, "PausedTask"), "the pause-era receipt class must stay gone"
    blocked_hints = typing.get_type_hints(service_module.SourceDeleteBlocked)
    assert blocked_hints["active_tasks"] == tuple[service_module.TaskRef, ...]
    deletion_hints = typing.get_type_hints(service_module.SourceDeletion)
    assert deletion_hints["paused_tasks"] == tuple[service_module.TaskRef, ...]
    # The completed receipt defaults to "nothing was paused": through the
    # service no active task can reach the pause fallback.
    assert service_module.SourceDeletion(source_id=1, name="s").paused_tasks == ()
