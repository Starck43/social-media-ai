"""Disabled-by-default checkpoint delivery for authoritative claimed Job rows.

One immutable job/window reference, atomic snapshot binding, no delivery-only
LLM rebuild and no uncertain replay. All workers must use the same rollout flag.
Force and legacy histories fail closed instead of overwriting delivery evidence.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager, suppress
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from math import isfinite
from typing import Any
from uuid import uuid4

from sqlalchemy import select, text, update

from app.core.config import settings
from app.core.database import async_engine, async_session_maker
from app.core.tenant_context import current_tenant_id, is_bypass
from app.models import AgentTask, DigestRun, Job, Source, Tenant
from app.services.digest.checkpoint_store import DeliveryAuthorizationError, locked_checkpoint
from app.services.digest.checkpoints import load_checkpoint
from app.services.digest.delivery_outcomes import DeliveryFailure, summarize_delivery
from app.services.digest.html_parts import SPLITTER_VERSION
from app.services.digest.snapshot_factory import create_snapshot_in_session

REFERENCE_KEY = "digest_delivery"


def enabled() -> bool:
    return os.environ.get("DIGEST_CHECKPOINT_DELIVERY_ENABLED", "0") == "1"


def _scope() -> int:
    tenant_id = current_tenant_id()
    if is_bypass() or type(tenant_id) is not int or tenant_id <= 0:
        raise DeliveryFailure("explicit_workspace_required")
    return tenant_id


def _reference(result: Any) -> dict[str, Any] | None:
    if result is None:
        return None
    if not isinstance(result, dict):
        raise DeliveryFailure("invalid_job_result")
    if REFERENCE_KEY not in result:
        return None
    ref = result[REFERENCE_KEY]
    keys = {"version", "phase", "token", "period", "start", "end", "task_id", "request", "run_id", "generation", "build_started"}
    if not isinstance(ref, dict) or set(ref) != keys or type(ref["version"]) is not int or ref["version"] != 1:
        raise DeliveryFailure("invalid_job_reference")
    if ref["phase"] not in ("reserved", "bound") or ref["period"] not in ("day", "week", "month"):
        raise DeliveryFailure("invalid_job_reference")
    try:
        start, end = date.fromisoformat(ref["start"]), date.fromisoformat(ref["end"])
    except (ValueError, TypeError):
        raise DeliveryFailure("invalid_job_window") from None
    if start > end or ref["start"] != start.isoformat() or ref["end"] != end.isoformat():
        raise DeliveryFailure("invalid_job_window")
    if not isinstance(ref["token"], str) or len(ref["token"]) != 32 or type(ref["build_started"]) is not bool:
        raise DeliveryFailure("invalid_job_reference")
    if not isinstance(ref["request"], dict) or ref["request"].get("period") != ref["period"]:
        raise DeliveryFailure("invalid_job_request")
    if ref["task_id"] is not None and (type(ref["task_id"]) is not int or ref["task_id"] <= 0):
        raise DeliveryFailure("invalid_job_reference")
    if ref["phase"] == "bound":
        if ref["build_started"] is not True:
            raise DeliveryFailure("invalid_job_reference")
        if type(ref["run_id"]) is not int or ref["run_id"] <= 0 or not isinstance(ref["generation"], str) or not ref["generation"]:
            raise DeliveryFailure("invalid_job_reference")
    elif ref["run_id"] is not None or ref["generation"] is not None:
        raise DeliveryFailure("invalid_job_reference")
    return deepcopy(ref)


def _check_claim(row: Job | None, job: Any) -> None:
    if (
        row is None or row.tenant_id != _scope() or row.job_type != "digest" or row.status != "running"
        or row.agent_task_id != job.agent_task_id or row.attempts != job.attempts
        or row.started_at != job.started_at or row.started_at is None
    ):
        raise DeliveryFailure("job_claim_lost")


async def _owned_job(session, job: Any, *, lock: bool = False) -> Job:
    stmt = select(Job).where(Job.id == job.id, Job.tenant_id == _scope())
    if lock:
        stmt = stmt.with_for_update()
    row = await session.scalar(stmt)
    _check_claim(row, job)
    active = await session.scalar(select(Tenant.id).where(Tenant.id == row.tenant_id, Tenant.is_active.is_(True)))
    if active is None:
        raise DeliveryFailure("workspace_inactive")
    if row.agent_task_id is not None:
        task = await session.scalar(select(AgentTask.id).where(
            AgentTask.id == row.agent_task_id, AgentTask.tenant_id == row.tenant_id,
            AgentTask.job_type == "digest", AgentTask.is_active.is_(True),
        ))
        if task is None:
            raise DeliveryFailure("schedule_unavailable")
    return row


async def assert_legacy_allowed(*, agent_task_id: int | None, period: str) -> None:
    """Never let legacy builder reset/send a run already managed by checkpoints."""
    if agent_task_id is None:
        return
    from app.services.digest.builder import period_bounds
    start, end = period_bounds(period)
    async with async_session_maker() as session:
        protected = await session.scalar(select(DigestRun.id).where(
            DigestRun.tenant_id == _scope(), DigestRun.agent_task_id == agent_task_id,
            DigestRun.period_start == start, DigestRun.period_end == end,
            DigestRun.delivery_state.is_not(None),
        ))
        if protected is not None:
            raise DeliveryFailure("checkpoint_run_requires_job_resume")
        results = await session.scalars(select(Job.result).where(
            Job.tenant_id == _scope(), Job.agent_task_id == agent_task_id,
            Job.job_type == "digest", Job.result.is_not(None),
        ))
        for result in results:
            ref = _reference(result)
            if ref and (ref["start"], ref["end"]) == (start.isoformat(), end.isoformat()):
                raise DeliveryFailure("checkpoint_reservation_requires_original_job")


@asynccontextmanager
async def _job_lock(job: Any):
    """Session lock spans builds/commits; lease checks fence stale workers."""
    # Serialize different jobs of one schedule BEFORE charging for a summary.
    # Manual jobs deliberately have distinct identities.
    slot = f"task:{job.agent_task_id}" if job.agent_task_id is not None else f"job:{job.id}"
    raw = f"digest-build:{settings.DB_SCHEMA}:{_scope()}:{slot}".encode()
    key = int.from_bytes(sha256(raw).digest()[:8], "big", signed=True)
    async with async_engine.connect() as connection:
        attempted, resolved, acquired = False, False, False
        try:
            attempted = True
            acquired, pid = (await connection.execute(text(
                "SELECT pg_catalog.pg_try_advisory_lock(:key), pg_catalog.pg_backend_pid()"
            ), {"key": key})).one()
            resolved = True
            await connection.commit()
            if not acquired:
                raise DeliveryFailure("digest_job_busy", retryable=True)

            async def alive():
                if connection.closed or connection.invalidated:
                    raise DeliveryFailure("job_lock_lost")
                current_pid = await connection.scalar(text("SELECT pg_catalog.pg_backend_pid()"))
                await connection.commit()
                if current_pid != pid:
                    raise DeliveryFailure("job_lock_lost")
                async with async_session_maker() as session:
                    await _owned_job(session, job)

            yield alive
        finally:
            if attempted and not resolved:
                await connection.invalidate()
            elif acquired:
                try:
                    await connection.rollback()
                    if connection.invalidated:
                        raise DeliveryFailure("job_lock_lost")
                    released = await connection.scalar(text("SELECT pg_catalog.pg_advisory_unlock(:key)"), {"key": key})
                    await connection.commit()
                    if released is not True:
                        raise DeliveryFailure("job_lock_lost")
                except BaseException:
                    await connection.invalidate()
                    raise


async def _heartbeat(job: Any):
    while True:
        await asyncio.sleep(20)
        async with async_session_maker() as session:
            async with session.begin():
                changed = await session.execute(update(Job).where(
                    Job.id == job.id, Job.tenant_id == _scope(), Job.status == "running",
                    Job.attempts == job.attempts, Job.started_at == job.started_at,
                ).values(locked_at=datetime.now(timezone.utc)))
                if changed.rowcount != 1:
                    raise DeliveryFailure("job_claim_lost")


async def _request(job: Any, payload: dict[str, Any]) -> dict[str, Any]:
    from app.jobs.handlers import _load_task
    task = await _load_task(job.agent_task_id)
    if job.agent_task_id is not None and task is None:
        raise DeliveryFailure("schedule_unavailable")
    params = dict(task.payload or {}) if task is not None else {}
    if params.get("force_refresh") or payload.get("force_refresh"):
        raise DeliveryFailure("force_requires_reviewed_new_generation")
    period = params.get("period", payload.get("period", "day"))
    if period not in ("day", "week", "month"):
        raise DeliveryFailure("invalid_period")
    source_ids = sorted(s.id for s in (task.sources or [])) if task is not None else None
    if source_ids:
        owned = await Source.objects.filter(id__in=source_ids, is_active=True)
        if sorted(s.id for s in owned) != source_ids:
            raise DeliveryFailure("source_scope_changed")
    scenario_id = params.get("scenario_id") or payload.get("scenario_id")
    if not scenario_id and task is not None:
        scenario_id = task.agent_scenario_id
    if scenario_id is not None:
        from app.models import AgentScenario
        if type(scenario_id) is not int or await AgentScenario.objects.get(id=scenario_id, is_active=True) is None:
            raise DeliveryFailure("scenario_unavailable")
    return {
        "period": period, "source_ids": source_ids,
        "group_by": params.get("group_by") or payload.get("group_by") or "themes",
        "time_breakdown": bool(params.get("time_breakdown") or payload.get("time_breakdown")),
        "scenario_id": scenario_id,
    }


async def _reserve(job: Any, request: dict[str, Any]) -> dict[str, Any]:
    from app.services.digest.builder import period_bounds
    async with async_session_maker() as session:
        async with session.begin():
            row = await _owned_job(session, job, lock=True)
            ref = _reference(row.result)
            if ref is not None:
                if ref["task_id"] != row.agent_task_id or ref["request"] != request:
                    raise DeliveryFailure("original_request_changed")
                return ref
            if row.attempts != 1:
                raise DeliveryFailure("legacy_retry_has_no_original_reference")
            start, end = period_bounds(request["period"])
            if row.agent_task_id is not None:
                existing = await session.scalar(select(DigestRun.id).where(
                    DigestRun.tenant_id == _scope(), DigestRun.agent_task_id == row.agent_task_id,
                    DigestRun.period_start == start, DigestRun.period_end == end,
                ))
                if existing is not None:
                    raise DeliveryFailure("existing_window_requires_original_job")
            ref = {
                "version": 1, "phase": "reserved", "token": uuid4().hex,
                "period": request["period"], "start": start.isoformat(), "end": end.isoformat(),
                "task_id": row.agent_task_id, "request": request,
                "run_id": None, "generation": None, "build_started": False,
            }
            row.result = {**(row.result or {}), REFERENCE_KEY: ref}
            return deepcopy(ref)


async def _start_summary(job: Any, ref: dict[str, Any]) -> dict[str, Any]:
    async with async_session_maker() as session:
        async with session.begin():
            row = await _owned_job(session, job, lock=True)
            if _reference(row.result) != ref or ref["build_started"]:
                raise DeliveryFailure("summary_build_requires_reconciliation")
            ref = {**ref, "build_started": True}
            row.result = {**(row.result or {}), REFERENCE_KEY: ref}
            return ref


async def _retain_cost(job: Any, ref: dict[str, Any], cost: float | None) -> None:
    """Retain known incurred cost even if snapshot creation is later rejected."""
    async with async_session_maker() as session:
        async with session.begin():
            row = await session.scalar(select(Job).where(Job.id == job.id, Job.tenant_id == _scope()).with_for_update())
            if row is None or _reference(row.result) != ref:
                raise DeliveryFailure("build_cost_requires_reconciliation")
            row.llm_cost = cost
            row.result = {**(row.result or {}), "digest_build_cost_known": cost is not None}


async def _bind(job: Any, ref: dict[str, Any], content: str, cost: float | None) -> dict[str, Any]:
    async with async_session_maker() as session:
        async with session.begin():
            row = await _owned_job(session, job, lock=True)
            if _reference(row.result) != ref:
                raise DeliveryFailure("original_reference_changed")
            snapshot = await create_snapshot_in_session(
                session, content=content, period=ref["period"],
                period_start=date.fromisoformat(ref["start"]), period_end=date.fromisoformat(ref["end"]),
                agent_task_id=row.agent_task_id, llm_cost=cost,
            )
            bound = {**ref, "phase": "bound", "run_id": snapshot.run_id, "generation": snapshot.generation}
            row.result = {**(row.result or {}), REFERENCE_KEY: bound}
            # Transfer, not duplicate, the counted cost to the committed run.
            row.llm_cost = None
        return bound


async def _validate_bound(job: Any, ref: dict[str, Any]) -> None:
    async with async_session_maker() as session:
        await _owned_job(session, job)
        run = await session.scalar(select(DigestRun).where(DigestRun.id == ref["run_id"], DigestRun.tenant_id == _scope()))
        if (run is None or run.agent_task_id != job.agent_task_id or run.period != ref["period"]
            or run.period_start.isoformat() != ref["start"] or run.period_end.isoformat() != ref["end"]):
            raise DeliveryFailure("original_run_mismatch")
        load_checkpoint(run.delivery_state, run_id=run.id, tenant_id=_scope(), generation=ref["generation"],
                        content=run.content, splitter=SPLITTER_VERSION)


async def _finish(job: Any, ref: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    result = {**result, REFERENCE_KEY: ref}
    async with async_session_maker() as session:
        async with session.begin():
            row = await _owned_job(session, job, lock=True)
            if _reference(row.result) != ref:
                raise DeliveryFailure("original_reference_changed")
            run = await session.scalar(select(DigestRun).where(
                DigestRun.id == ref["run_id"], DigestRun.tenant_id == _scope(),
            ).with_for_update())
            if run is None:
                raise DeliveryFailure("original_run_missing")
            run.status = "sent" if result["status"] == "sent" else "failed"
            run.error = None if run.status == "sent" else f"checkpoint_{result['status']}"
            row.result = {**(row.result or {}), **result}
            result = deepcopy(row.result)
    if result["status"] != "sent":
        raise DeliveryFailure(f"checkpoint_{result['status']}", result=result, retryable=result["retryable"])
    return result


async def _publish(job: Any, ref: dict[str, Any], alive) -> dict[str, Any]:
    from app.channels.registry import get_channel
    rate_limited = False
    async with locked_checkpoint(ref["run_id"], generation=ref["generation"]) as store:
        state, _ = await store.load()
        for ti, target in enumerate(state["targets"]):
            if rate_limited:
                break  # scope of provider throttling is unknown: stop this publisher
            for pi, original in enumerate(target["parts"]):
                if original["status"] == "sent":
                    continue
                if original["status"] not in ("pending", "rejected"):
                    break  # unknown acceptance is never replayed
                await alive()
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
                    await alive()
                except DeliveryFailure:
                    await store.record_outcome(ti, pi, "blocked")
                    raise  # proven pre-HTTP refusal, not an attempted message
                try:
                    response = await channel.send_part(part["destination_id"], part["text"], parse_mode="HTML")
                except asyncio.CancelledError:
                    raise  # persisted in-flight remains uncertain, never reset
                except Exception:
                    response = {"outcome": "uncertain"}
                if not isinstance(response, dict):
                    response = {"outcome": "uncertain"}
                outcome = response.get("outcome")
                if outcome not in ("sent", "rejected", "uncertain", "blocked"):
                    outcome = "uncertain"
                message_id = response.get("message_id") if isinstance(response, dict) else None
                if outcome == "sent" and (not isinstance(message_id, str) or not message_id.strip() or message_id != message_id.strip()):
                    outcome = "uncertain"
                await store.record_outcome(ti, pi, outcome, message_id=message_id if outcome == "sent" else None)
                await asyncio.sleep(0.6)
                if outcome != "sent":
                    rate_limited = rate_limited or response.get("error_code") == "http_429"
                    break  # at most one rejected attempt per target per job attempt
        state, _ = await store.load()
    result = summarize_delivery(state)
    if rate_limited:
        # The transport contract does not yet expose verified Retry-After.
        # Do not guess a delay and hammer the provider automatically.
        result.update(retryable=False, reason="rate_limit_requires_operator_delay")
    return await _finish(job, ref, result)


async def _execute_digest_job(job: Any, payload: dict[str, Any], legacy_handler) -> dict[str, Any]:
    """Called by dispatcher with its claimed Job; client payload IDs are ignored."""
    if not enabled():
        if _reference(getattr(job, "result", None)) is not None:
            raise DeliveryFailure("checkpoint_delivery_disabled_no_legacy_fallback")
        return await legacy_handler(payload)
    async with async_session_maker() as session:
        await _owned_job(session, job)
    async with _job_lock(job) as alive:
        heartbeat = asyncio.create_task(_heartbeat(job))
        try:
            request = await _request(job, payload)
            ref = await _reserve(job, request)
            if ref["phase"] == "reserved":
                if ref["build_started"]:
                    raise DeliveryFailure("summary_build_requires_reconciliation")
                from app.services.digest.builder import _summarize, aggregate, period_bounds
                from app.services.digest.render import render_digest
                data, start, end = await aggregate(**request)
                window = (ref["start"], ref["end"])
                current_window = tuple(d.isoformat() for d in period_bounds(ref["period"]))
                if (start.isoformat(), end.isoformat()) != window or current_window != window:
                    raise DeliveryFailure("reserved_window_changed_before_summary")
                await alive()
                ref = await _start_summary(job, ref)
                summary, info = await _summarize(data)
                raw_cost = info.get("cost")
                cost = float(raw_cost) if type(raw_cost) in (int, float) and isfinite(raw_cost) and raw_cost >= 0 else None
                await _retain_cost(job, ref, cost)
                data["llm"] = {**(data.get("llm") or {}), "model": info.get("model")}
                content = render_digest(data, summary=summary)
                await alive()
                ref = await _bind(job, ref, content, cost)
            if heartbeat.done():
                heartbeat.result()
            await alive()
            await _validate_bound(job, ref)
            return await _publish(job, ref, alive)
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await heartbeat


async def execute_digest_job(job: Any, payload: dict[str, Any], legacy_handler) -> dict[str, Any]:
    if not enabled():
        return await _execute_digest_job(job, payload, legacy_handler)
    try:
        return await _execute_digest_job(job, payload, legacy_handler)
    except DeliveryFailure:
        raise
    except Exception:
        raise DeliveryFailure("checkpoint_execution_requires_review") from None


async def finalize_digest_job(job: Any, *, result=None, failure=None, allow_retry=True) -> bool | None:
    """Claim-fenced terminal/retry update. None means another worker owns it.

    Unlike generic JobManager writes, this cannot complete/fail a newer claim.
    Persisted checkpoint metadata survives every retry and terminal failure.
    """
    retry = False
    async with async_session_maker() as session:
        async with session.begin():
            row = await session.scalar(select(Job).where(
                Job.id == job.id, Job.tenant_id == _scope(),
            ).with_for_update())
            try:
                _check_claim(row, job)
            except DeliveryFailure:
                return None
            now = datetime.now(timezone.utc)
            if failure is None:
                ref = _reference(row.result)
                if result.get("status") != "sent" or result.get(REFERENCE_KEY) != ref or ref is None:
                    raise DeliveryFailure("invalid_checkpoint_completion")
                row.result = {**(row.result or {}), **result}
                row.status, row.error, row.finished_at = "done", None, now
            else:
                safe = failure if isinstance(failure, DeliveryFailure) else DeliveryFailure("checkpoint_execution_requires_review")
                # Only server-owned persisted identity is authoritative.
                details = {k: v for k, v in safe.result.items() if k != REFERENCE_KEY}
                row.result = {**(row.result or {}), **details}
                retry = bool(allow_retry and safe.retryable and row.attempts < row.max_attempts)
                row.error = str(safe)
                row.status = "pending" if retry else "failed"
                if retry:
                    delay = settings.JOB_RETRY_BACKOFF_SECONDS * 2 ** (row.attempts - 1)
                    row.run_at = now + timedelta(seconds=delay)
                    row.locked_at = None
                else:
                    row.finished_at = now
            finished = row
    if not retry:
        from app.models.managers.job_manager import JobManager
        await JobManager()._record_task_result(
            finished, status="ok" if failure is None else "failed", error=finished.error,
        )
    return retry
