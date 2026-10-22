"""Delete a workspace source without widening the scope of the tasks using it.

A task that names its sources keeps them in the `agent_task_sources` m2m, and
an *empty* link set is not "no sources": `_resolve_sources` reads it as "all
active workspace sources". Deleting the last selected source therefore used to
turn a deliberately narrow task into a run over every source of the workspace —
the opposite of what the operator asked for.

This module is the single delete path for a workspace source. In one
transaction it removes the row (staged analytics follow through the database
cascade) and pauses the tasks that linked *only* that source, so their explicit
scope never degrades into the all-active fallback. Tasks that linked the source
along with others keep running with the remaining links, and a task that never
had links keeps its all-active contract untouched.

The caller owns the transaction and the permission check.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import service_permission_scope


@dataclass(frozen=True)
class PausedTask:
    """A task the deletion stopped, and the reason it had to stop."""

    task_id: int
    name: str


@dataclass(frozen=True)
class SourceDeletion:
    """What one source deletion did — the receipt the caller reports."""

    source_id: int
    name: str
    paused_tasks: tuple[PausedTask, ...] = ()


async def delete_source(session: AsyncSession, *, source_id: int, tenant_id: int) -> SourceDeletion | None:
    """Delete one workspace source; pause the tasks it was the last source of.

    Returns `None` when the source is not in this workspace (nothing written —
    the caller reports "not found" instead of inventing a delete). The task
    decision is made under the same locks as the deletion: the source row is
    locked, then the linked task rows, so a concurrent task edit or a second
    delete cannot interleave between "count the remaining links" and "pause".
    """
    if type(source_id) is not int or source_id <= 0 or type(tenant_id) is not int or tenant_id <= 0:
        raise ValueError("source_scope_invalid")

    from app.models import AgentTask, Source
    from app.models.agent_task import agent_task_sources
    from app.models.managers.job_manager import JobManager

    # Serialize with source-job admission: a collect/analyze occurrence admitted
    # for this source must be on one side of the delete, not in the middle of it.
    await JobManager.lock_source_admission(session, tenant_id)

    source = (
        await session.execute(
            select(Source).where(Source.id == source_id, Source.tenant_id == tenant_id).with_for_update()
        )
    ).scalar_one_or_none()
    if source is None:
        return None

    linked_ids = {
        int(task_id)
        for (task_id,) in (
            await session.execute(
                select(agent_task_sources.c.agent_task_id)
                .where(agent_task_sources.c.source_id == source_id)
                .order_by(agent_task_sources.c.agent_task_id)
            )
        ).all()
    }

    # Lock the affected tasks before deciding: their links are what the pause
    # depends on, and a concurrent editor must not slip a change in between.
    tasks = []
    if linked_ids:
        tasks = list(
            (
                await session.execute(
                    select(AgentTask).where(AgentTask.id.in_(linked_ids)).order_by(AgentTask.id).with_for_update()
                )
            )
            .scalars()
            .all()
        )
        counts = {
            int(task_id): int(total)
            for task_id, total in (
                await session.execute(
                    select(agent_task_sources.c.agent_task_id, func.count())
                    .where(agent_task_sources.c.agent_task_id.in_(linked_ids))
                    .group_by(agent_task_sources.c.agent_task_id)
                )
            ).all()
        }
    else:
        counts = {}

    name = source.name
    # The row goes; `sources.analytics` and the m2m rows are `passive_deletes`,
    # so PostgreSQL removes them by FK cascade rather than the ORM loading them.
    await session.delete(source)
    await session.flush()

    paused: list[PausedTask] = []
    for task in tasks:
        if counts.get(task.id, 0) > 1 or not task.is_active:
            # Either another explicit source survives (scope stays explicit and
            # narrower), or the task was already paused: nothing to widen.
            continue
        await _pause_task(session, task, source_name=name)
        paused.append(PausedTask(task_id=task.id, name=task.name))

    return SourceDeletion(source_id=source_id, name=name, paused_tasks=tuple(paused))


async def _pause_task(session: AsyncSession, task, *, source_name: str) -> None:
    """Stop one task for lack of sources, dropping its queued occurrence.

    Goes through the task manager so the pause carries the same bookkeeping as
    the task form (a `pending` occurrence taken under the old plan is dropped).
    The right is bookkeeping derived from the authorized source delete, hence
    the service grant; it never widens tenant data access. The note is written
    in the same update: a paused task with no stated reason is the same dead end
    this task set out to remove.
    """
    from app.models.managers.agent_task_manager import AgentTaskManager

    with service_permission_scope("agenttask", "update"):
        await AgentTaskManager().update_by_id(
            task.id,
            session=session,
            is_active=False,
            last_error=f"Источник «{source_name}» удалён: у задачи не осталось источников, выберите их заново",
        )
