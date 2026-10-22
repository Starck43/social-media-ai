"""Deleting a source must not widen a task's explicit scope to "all active".

`agent_task_sources` is a plain m2m: an empty link set is not "no sources" but
"every active source of the workspace" (`_resolve_sources`). So deleting the
last source a task named used to turn a narrow task into a workspace-wide one.
These checks pin the contract of the single delete path
(`app/services/collection/source_deletion.py`): the last-source task is paused
in the same transaction, a task with other links keeps running over them, and a
task that never named sources keeps its all-active behaviour.

Real workspace scope (no bypass) because the pause goes through the task
manager and the source-admission lock, both of which require the owning tenant.
"""

from __future__ import annotations

import secrets
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
from app.services.collection.source_deletion import delete_source

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


async def test_last_explicit_source_pauses_the_task_instead_of_widening_it():
    """The only named source goes away: the task stops, it does not inherit "all"."""
    tenant = await _tenant()
    try:
        task = await _task(tenant.id, "narrow")
        source = await _source(tenant.id, "only-source")
        await _link(task, [source.id])

        removal = await _delete(tenant.id, source.id)

        assert removal is not None
        assert removal.name == "only-source"
        assert [paused.task_id for paused in removal.paused_tasks] == [task.id]

        row = await _task_row(tenant.id, task.id)
        assert row is not None
        assert row.is_active is False
        assert "удалён" in (row.last_error or "")
        assert await _links(tenant.id, task.id) == []
        with tenant_scope(tenant.id):
            assert await Source.objects.get(id=source.id) is None
    finally:
        await Tenant.objects.delete_by_id(tenant.id)


async def test_task_with_another_source_keeps_running_on_the_remaining_link():
    """Two named sources, one deleted: explicit scope stays explicit and narrower."""
    tenant = await _tenant()
    try:
        task = await _task(tenant.id, "two-sources")
        kept = await _source(tenant.id, "kept")
        doomed = await _source(tenant.id, "doomed")
        await _link(task, [kept.id, doomed.id])

        removal = await _delete(tenant.id, doomed.id)

        assert removal is not None
        assert removal.paused_tasks == ()
        row = await _task_row(tenant.id, task.id)
        assert row is not None and row.is_active is True
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


async def test_pausing_drops_the_queued_occurrence_of_the_old_plan():
    """A `pending` occurrence admitted for the deleted source must not run."""
    tenant = await _tenant()
    try:
        task = await _task(tenant.id, "queued")
        source = await _source(tenant.id, "queued-source")
        await _link(task, [source.id])
        with tenant_scope(tenant.id):
            pending = await JobManager().enqueue("collect", agent_task_id=task.id, run_at=NOW, max_attempts=3)
        assert pending is not None

        await _delete(tenant.id, source.id)

        with tenant_scope(tenant.id):
            assert await Job.objects.get(id=pending.id) is None
    finally:
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


async def test_a_concurrent_task_edit_serializes_on_the_task_row():
    """The pause decision holds the task row, so an editor cannot slip between.

    `SELECT ... FOR UPDATE NOWAIT` from a second session is the editor's lock
    attempt: while the delete transaction is open it must be refused, and after
    it commits the same read must succeed. Proven against PostgreSQL, not a
    simulated lock.
    """
    tenant = await _tenant()
    try:
        task = await _task(tenant.id, "contended")
        source = await _source(tenant.id, "contended-source")
        await _link(task, [source.id])

        deleting = async_session_maker()
        try:
            with tenant_scope(tenant.id):
                transaction = await deleting.begin()
                removal = await delete_source(deleting, source_id=source.id, tenant_id=tenant.id)
                assert removal is not None

                contender = async_session_maker()
                try:
                    with tenant_scope(tenant.id):
                        # Schema-qualified: the raw statement resolves the table
                        # without the ORM's schema, and an unqualified name here
                        # silently checks a different schema's empty table.
                        with pytest.raises(DBAPIError):
                            await contender.execute(
                                text(
                                    f"SELECT id FROM {settings.DB_SCHEMA}.agent_tasks "
                                    "WHERE id = :task_id FOR UPDATE NOWAIT"
                                ),
                                {"task_id": task.id},
                            )
                finally:
                    await contender.rollback()
                    await contender.close()

                await transaction.commit()
        finally:
            await deleting.close()

        row = await _task_row(tenant.id, task.id)
        assert row is not None and row.is_active is False
    finally:
        await Tenant.objects.delete_by_id(tenant.id)
