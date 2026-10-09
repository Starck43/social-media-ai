"""Atomic NEW digest snapshot creation; not yet wired into builder/jobs.

Never overwrite/resume an existing run or interpret NULL legacy receipts. The
caller supplies already-rendered content and its actual LLM cost. Job binding,
force-generation recovery and coordinated publication are separate integration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import isfinite
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.database import async_session_maker
from app.core.tenant_context import TenantContextError, current_tenant_id, is_bypass
from app.models import AgentTask, DigestRun, Tenant, TenantChannel
from app.services.digest.checkpoints import new_checkpoint
from app.services.digest.html_parts import SPLITTER_VERSION, split_digest_html


class SnapshotError(ValueError):
    """No new snapshot can be safely created from this request/configuration."""


class SnapshotConflict(SnapshotError):
    """Existing schedule/window needs explicit resume policy, never overwrite."""


@dataclass(frozen=True)
class SnapshotRef:
    run_id: int
    generation: str


async def create_snapshot(
    *,
    content: str,
    period: str,
    period_start: date,
    period_end: date,
    agent_task_id: int | None = None,
    llm_cost: float | None = None,
) -> SnapshotRef:
    """Commit content + frozen owned targets/parts + generation in ONE transaction.

    Dates are explicit inclusive bounds (current day digests have equal bounds),
    never recomputed from today's date here. Targets use stable binding-ID order
    and existing exact normalization; no env recipient or credential is consulted.
    Missing targets fail without persisting a run, rather than invent delivery.
    """
    tenant_id = current_tenant_id()
    if is_bypass() or type(tenant_id) is not int or tenant_id <= 0:
        raise TenantContextError("Snapshot creation requires an explicit non-bypass workspace")
    if (
        period not in ("day", "week", "month")
        or type(period_start) is not date
        or type(period_end) is not date
        or period_start > period_end
        or (agent_task_id is not None and (type(agent_task_id) is not int or agent_task_id <= 0))
    ):
        raise SnapshotError("Invalid digest snapshot identity/window")
    if llm_cost is not None and (type(llm_cost) not in (int, float) or not isfinite(llm_cost) or llm_cost < 0):
        raise SnapshotError("Invalid digest snapshot cost")
    parts = split_digest_html(content)
    generation = uuid4().hex
    try:
        async with async_session_maker() as session:
            async with session.begin():
                tenant = await session.scalar(
                    select(Tenant.id)
                    .where(Tenant.id == tenant_id, Tenant.is_active.is_(True))
                    .with_for_update(read=True)
                )
                if tenant is None:
                    raise TenantContextError("Snapshot workspace is missing or inactive")
                if agent_task_id is not None:
                    task = await session.scalar(
                        select(AgentTask.id)
                        .where(
                            AgentTask.id == agent_task_id,
                            AgentTask.tenant_id == tenant_id,
                            AgentTask.job_type == "digest",
                        )
                        .with_for_update(read=True)
                    )
                    if task is None:
                        raise TenantContextError("Snapshot schedule unavailable in this workspace")
                bindings = (
                    await session.scalars(
                        select(TenantChannel)
                        .where(
                            TenantChannel.tenant_id == tenant_id,
                            TenantChannel.is_active.is_(True),
                            TenantChannel.is_digest_target.is_(True),
                        )
                        .order_by(TenantChannel.id)
                        .with_for_update(read=True)
                    )
                ).all()
                targets, seen = [], set()
                for binding in bindings:
                    destination = binding.chat_id.strip()
                    if destination.startswith("@"):
                        destination = destination.lower()
                    if binding.channel not in ("telegram", "max") or not destination:
                        raise SnapshotError("Invalid configured digest destination")
                    key = (binding.channel, destination)
                    if key in seen:
                        continue
                    seen.add(key)
                    targets.append(
                        {
                            "binding_id": binding.id,
                            "channel": binding.channel,
                            "destination_id": destination,
                            "parts": parts,
                        }
                    )
                if not targets:
                    raise SnapshotError("No owned active digest destinations")
                run = DigestRun(
                    tenant_id=tenant_id,
                    agent_task_id=agent_task_id,
                    period=period,
                    period_start=period_start,
                    period_end=period_end,
                    channel="auto",
                    content=content,
                    status="pending",
                    llm_cost=llm_cost,
                )
                session.add(run)
                await session.flush()  # allocate ID; still invisible outside this transaction
                run.delivery_state = new_checkpoint(
                    run_id=run.id,
                    tenant_id=tenant_id,
                    generation=generation,
                    content=content,
                    splitter=SPLITTER_VERSION,
                    targets=targets,
                )
                ref = SnapshotRef(run_id=run.id, generation=generation)
            return ref  # transaction commit succeeded before returning
    except IntegrityError:
        raise SnapshotConflict("Snapshot integrity conflict requires explicit resume or repair") from None
