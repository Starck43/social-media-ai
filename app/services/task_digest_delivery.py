"""Confirmed delivery of a frozen composed-task report using digest checkpoints.

The job owns the snapshot reference. Repeated requests reuse it; sent or uncertain
parts are never resent. No generation or model call happens during delivery.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

from sqlalchemy import select

from app.core.database import async_session_maker
from app.core.permissions import get_current_user, permission_scope, require_permission
from app.core.tenant_context import current_tenant_id, is_bypass
from app.services.digest.checkpoint_store import DeliveryAuthorizationError, locked_checkpoint
from app.services.digest.delivery_outcomes import summarize_delivery
from app.services.digest.snapshot_factory import create_snapshot_in_session
from app.types import ActionType


async def _fresh_delivery_actor():
    from app.models import Tenant, User
    from app.models.managers.tenant_manager import tenant_users

    actor_id = getattr(get_current_user(), "id", None)
    tenant_id = current_tenant_id()
    if is_bypass() or type(tenant_id) is not int or type(actor_id) is not int:
        raise PermissionError("An authenticated workspace member is required")
    user = await User.objects.get(id=actor_id, is_active=True)
    tenant = await Tenant.objects.get(id=tenant_id, is_active=True)
    memberships = await tenant_users.web_memberships(actor_id) if user and tenant else []
    member = next((m for m in memberships if m.tenant_id == tenant_id), None)
    if member is None:
        raise PermissionError("Active workspace membership required")
    grants = await tenant_users.member_grants(member)
    required = {
        ("digestrun", ActionType.CREATE),
        ("digestrun", ActionType.VIEW),
        ("agenttask", ActionType.VIEW),
        ("source", ActionType.VIEW),
        ("aianalytics", ActionType.VIEW),
    }
    if not required.issubset(grants):
        raise PermissionError("Report delivery is not permitted")
    return user, grants


def _report(job: Any) -> dict:
    result = job.result if isinstance(job.result, dict) else {}
    pipeline = result.get("pipeline") or {}
    stage = (pipeline.get("stages") or {}).get("digest") or {}
    report = stage.get("result") or {}
    if stage.get("state") != "completed" or not isinstance(report.get("text"), str) or not report["text"].strip():
        raise ValueError("Generated task digest unavailable")
    return report


async def _bind_report(task_id: int, job_id: int) -> dict:
    from app.models import AgentTask, Job

    async with async_session_maker() as session:
        async with session.begin():
            job = await session.scalar(
                select(Job)
                .where(
                    Job.id == job_id,
                    Job.agent_task_id == task_id,
                    Job.tenant_id == current_tenant_id(),
                    Job.status.in_(("done", "failed")),
                )
                .with_for_update()
            )
            task = await session.scalar(
                select(AgentTask.id).where(
                    AgentTask.id == task_id,
                    AgentTask.tenant_id == current_tenant_id(),
                )
            )
            if job is None or task is None:
                raise ValueError("Retained task run unavailable")
            report = _report(job)
            result = dict(job.result)
            reference = result.get("task_digest_delivery")
            if reference is None:
                # NULL schedule identity is intentional: job.result is the atomic
                # owner of this one report, independent of a mutable task schedule.
                ref = await create_snapshot_in_session(
                    session,
                    content=report["text"],
                    period="day",
                    period_start=date.fromisoformat(report["report_start"]),
                    period_end=date.fromisoformat(report["report_end"]),
                )
                reference = {"run_id": ref.run_id, "generation": ref.generation}
                result["task_digest_delivery"] = reference
                job.result = result
            if not isinstance(reference, dict) or set(reference) != {"run_id", "generation"}:
                raise ValueError("Task digest receipt requires review")
            return dict(reference)


@require_permission("digestrun", ActionType.CREATE)
async def send_task_digest(task_id: int, job_id: int) -> dict:
    """Execute only after the surface's explicit actor/payload-bound confirmation."""
    if any(type(value) is not int or value <= 0 for value in (task_id, job_id)):
        raise ValueError("Persisted task and run IDs required")
    actor, grants = await _fresh_delivery_actor()
    with permission_scope(actor, member_grants=grants):
        reference = await _bind_report(task_id, job_id)
        from app.channels.registry import get_channel

        async with locked_checkpoint(reference["run_id"], generation=reference["generation"]) as store:
            state, _ = await store.load()
            throttled = False
            for ti, target in enumerate(state["targets"]):
                if throttled:
                    break
                for pi, original in enumerate(target["parts"]):
                    if original["status"] == "sent":
                        continue
                    if original["status"] not in ("pending", "rejected"):
                        break
                    await _fresh_delivery_actor()
                    channel = get_channel(target["channel"])
                    if channel is None:
                        await store.record_outcome(ti, pi, "blocked")
                        break
                    try:
                        part = await store.begin_part(ti, pi)
                    except DeliveryAuthorizationError:
                        await store.record_outcome(ti, pi, "blocked")
                        break
                    try:
                        await _fresh_delivery_actor()
                    except PermissionError:
                        await store.record_outcome(ti, pi, "blocked")
                        raise
                    try:
                        response = await channel.send_part(part["destination_id"], part["text"], parse_mode="HTML")
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        response = {"outcome": "uncertain"}
                    if not isinstance(response, dict):
                        response = {"outcome": "uncertain"}
                    outcome = response.get("outcome")
                    if outcome not in ("sent", "rejected", "uncertain", "blocked"):
                        outcome = "uncertain"
                    message_id = response.get("message_id")
                    if outcome == "sent" and (
                        not isinstance(message_id, str) or not message_id.strip() or message_id != message_id.strip()
                    ):
                        outcome = "uncertain"
                    await store.record_outcome(ti, pi, outcome, message_id=message_id if outcome == "sent" else None)
                    await asyncio.sleep(0.6)
                    if outcome != "sent":
                        throttled = response.get("error_code") == "http_429"
                        break
            state, _ = await store.load()
            receipt = summarize_delivery(state)
            # Keep the ledger authoritative even if this projection fails.
            from app.models import DigestRun

            async with async_session_maker() as session:
                async with session.begin():
                    row = await session.scalar(
                        select(DigestRun)
                        .where(
                            DigestRun.id == reference["run_id"],
                            DigestRun.tenant_id == current_tenant_id(),
                        )
                        .with_for_update()
                    )
                    if row is not None:
                        row.status = "sent" if receipt["status"] == "sent" else "failed"
            return receipt


async def read_task_digest_delivery(reference: dict) -> dict:
    """Permission-filtered ledger projection; never exposes target identities."""
    from app.core.permissions import has_permission
    from app.models import DigestRun
    from app.services.digest.checkpoints import load_checkpoint

    if is_bypass() or type(current_tenant_id()) is not int:
        raise PermissionError("Concrete workspace required")
    actor = get_current_user()
    if not all(has_permission(actor, model, ActionType.VIEW) for model in ("digestrun", "source", "aianalytics")):
        raise PermissionError("Report access required")
    if not isinstance(reference, dict) or type(reference.get("run_id")) is not int:
        return {"status": "unavailable"}
    async with async_session_maker() as session:
        row = await session.scalar(
            select(DigestRun).where(
                DigestRun.id == reference["run_id"],
                DigestRun.tenant_id == current_tenant_id(),
            )
        )
        if row is None or not isinstance(row.delivery_state, dict):
            return {"status": "unavailable"}
        try:
            state = load_checkpoint(
                row.delivery_state,
                run_id=row.id,
                tenant_id=current_tenant_id(),
                generation=reference.get("generation"),
                content=row.content,
                splitter=row.delivery_state.get("splitter"),
            )
            return summarize_delivery(state)
        except (TypeError, ValueError):
            return {"status": "requires_review"}
