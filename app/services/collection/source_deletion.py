"""Delete a workspace source without widening the scope of the tasks using it.

A task that names its sources keeps them in the `agent_task_sources` m2m, and
an *empty* link set is not "no sources": `_resolve_sources` reads it as "all
active workspace sources". Deleting the last selected source therefore used to
turn a deliberately narrow task into a run over every source of the workspace —
the opposite of what the operator asked for.

This module is the single delete path for a workspace source, so the rule is
the same for the web, the API and the CLI. A source an *active* task still
reads is not deleted at all: the operator removes it inside that task, where
the scope is visible, and the receipt names the tasks standing in the way. The
same holds for the last explicitly linked source of an *inactive* task: a
resume would otherwise silently widen the task, so the delete waits until the
operator rebinds that task or removes it. An inactive task that keeps at least
one linked source never blocks a delete.

The caller owns the transaction and the permission check.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class TaskRef:
    """A task one deletion touched, by id and by the name the operator knows."""

    task_id: int
    name: str


class SourceDeleteScopeError(ValueError):
    """A source dependency could not be resolved safely in its tenant."""

    code = "source_task_scope_invalid"

    def __init__(self) -> None:
        super().__init__(self.code)


@dataclass(frozen=True)
class SourceDeleteBlocked:
    """The delete did not happen: these tasks still depend on the source.

    `active_tasks` are running readers, refused whatever else they link.
    `last_source_tasks` are paused tasks for which this is the only explicit
    source, refused because resuming them would widen their scope.
    """

    source_id: int
    name: str
    active_tasks: tuple[TaskRef, ...] = ()
    last_source_tasks: tuple[TaskRef, ...] = ()

    @property
    def reason(self) -> str:
        """Stable API reason, with active readers taking precedence."""
        return "source_in_active_task" if self.active_tasks else "source_is_last_task_source"

    @property
    def blocking_tasks(self) -> tuple[TaskRef, ...]:
        """Every task that refused the delete, active readers first."""
        seen: set[int] = set()
        blocking: list[TaskRef] = []
        for task in (*self.active_tasks, *self.last_source_tasks):
            if task.task_id in seen:
                continue
            seen.add(task.task_id)
            blocking.append(task)
        return tuple(blocking)


@dataclass(frozen=True)
class SourceDeletion:
    """What one source deletion did — the receipt the caller reports.

    `paused_tasks` stays at its empty default: the delete refuses a paused
    task's last source instead of pausing the task itself.
    """

    source_id: int
    name: str
    paused_tasks: tuple[TaskRef, ...] = ()


async def delete_source(
    session: AsyncSession, *, source_id: int, tenant_id: int
) -> SourceDeletion | SourceDeleteBlocked | None:
    """Delete one workspace source, unless a linked task still depends on it.

    Returns `None` when the source is not in this workspace (nothing written —
    the caller reports "not found" instead of inventing a delete) and
    `SourceDeleteBlocked` when an active task links it or an inactive task
    would lose its last explicit source (nothing written, the caller names the
    tasks). The task decision is made under the same locks as the deletion: the
    source row is locked, then the linked task rows, so a concurrent task edit
    or a second delete cannot interleave between "count the remaining links"
    and the refusal.
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

    # A task-owned query lane can be actively read before the next sync creates
    # its explicit m2m link. Include that owner in the same ordered lock set.
    task_ids = set(linked_ids)
    if source.owner_task_id is not None:
        task_ids.add(int(source.owner_task_id))

    # Lock the affected tasks before deciding: their activity and links are
    # what the refusal depends on, and a concurrent editor must not slip a
    # change in between. Tenant filtering also makes malformed cross-tenant
    # owner/link references fail closed without exposing another workspace.
    tasks = []
    if task_ids:
        tasks = list(
            (
                await session.execute(
                    select(AgentTask)
                    .where(AgentTask.id.in_(task_ids), AgentTask.tenant_id == tenant_id)
                    .order_by(AgentTask.id)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        found_task_ids = {int(task.id) for task in tasks}
        if found_task_ids != task_ids:
            # The source's tenant-local dependency set must resolve entirely
            # inside this workspace. Do not treat a foreign or missing task as
            # absent and silently delete the source underneath it.
            raise SourceDeleteScopeError()
    if linked_ids:
        counts = {
            int(task_id): int(total)
            for task_id, total in (
                await session.execute(
                    select(agent_task_sources.c.agent_task_id, func.count())
                    .join(AgentTask, AgentTask.id == agent_task_sources.c.agent_task_id)
                    .where(agent_task_sources.c.agent_task_id.in_(linked_ids))
                    .where(AgentTask.tenant_id == tenant_id)
                    .group_by(agent_task_sources.c.agent_task_id)
                )
            ).all()
        }
    else:
        counts = {}

    active: list[TaskRef] = []
    last_source: list[TaskRef] = []
    linked_task_ids = linked_ids
    for task in tasks:
        ref = TaskRef(task_id=int(task.id), name=str(task.name))
        if task.is_active:
            active.append(ref)
        elif int(task.id) in linked_task_ids and counts.get(int(task.id), 0) == 1:
            # The link set would become empty, which the resolver reads as
            # "every active source": resuming this task would silently widen it.
            last_source.append(ref)

    if active or last_source:
        # A running task reads this source on purpose, and a paused one would
        # lose its whole explicit scope. Nothing is written.
        return SourceDeleteBlocked(
            source_id=source_id,
            name=str(source.name),
            active_tasks=tuple(active),
            last_source_tasks=tuple(last_source),
        )

    name = source.name
    # The row goes; `sources.analytics` and the m2m rows are `passive_deletes`,
    # so PostgreSQL removes them by FK cascade rather than the ORM loading them.
    await session.delete(source)
    await session.flush()

    return SourceDeletion(source_id=source_id, name=name)
