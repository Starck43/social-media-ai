"""Periodic maintenance for terminal job history.

The web process and the standalone worker both call this idempotent service.
The database predicates make overlapping maintenance passes safe.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_

from app.core.tenant_context import current_tenant_id, is_bypass, tenant_scope
from app.models import AgentTask, Job, Tenant

logger = logging.getLogger(__name__)
DEFAULT_SUCCESS_HOURS = 24
DEFAULT_FAILURE_HOURS = 24
MAX_RETENTION_HOURS = 8760
_REVIEW_KEYS = frozenset({"needs_review", "review_required", "operator_review", "delivery_review"})
_REVIEW_CODES = ("partial_delivery_requires_review", "digest_run_in_progress_requires_review")


def retention_policy(payload: object) -> tuple[int, int] | None:
    """Return valid job-history retention hours from a task payload."""
    if not isinstance(payload, dict) or payload.get("mode") != "job_history":
        return None
    success = payload.get("success_hours", DEFAULT_SUCCESS_HOURS)
    failure = payload.get("failure_hours", DEFAULT_FAILURE_HOURS)
    if any(type(value) is not int or not 1 <= value <= MAX_RETENTION_HOURS for value in (success, failure)):
        return None
    return success, failure


def retention_cutoff(now: datetime, status: str, policy: tuple[int, int] | None) -> datetime:
    success_hours, failure_hours = policy or (DEFAULT_SUCCESS_HOURS, DEFAULT_FAILURE_HOURS)
    if status == "done":
        return now - timedelta(hours=success_hours)
    if status == "failed":
        return now - timedelta(hours=failure_hours)
    raise ValueError("Only terminal Job statuses have a retention cutoff")


def _retention_delete_criteria(tenant_id: int, status: str, cutoff: datetime):
    """Shared safety predicates applied to both batch selection and DELETE."""
    from app.jobs.attempt_budget import ATTEMPT_BUDGET_STOP_PREFIX
    from app.jobs.recovery_policy import OPERATOR_CANCELLED_ERROR, OUTCOME_UNCONFIRMED_PREFIX

    safe_result = and_(
        *(
            or_(
                Job.result[key].as_string().is_(None),
                func.lower(Job.result[key].as_string()).notin_(("true", "1")),
            )
            for key in _REVIEW_KEYS
        ),
        *(
            or_(
                Job.result[key].as_string().is_(None),
                func.lower(Job.result[key].as_string()).notin_(_REVIEW_CODES),
            )
            for key in ("status", "error", "diagnostic_code", "delivery_status")
        ),
    )
    safe_error = or_(
        Job.error.is_(None),
        and_(
            Job.error != OPERATOR_CANCELLED_ERROR,
            Job.error != "Cancelled before start",
            ~Job.error.startswith(OUTCOME_UNCONFIRMED_PREFIX),
            ~Job.error.startswith(ATTEMPT_BUDGET_STOP_PREFIX),
            *(~func.lower(func.coalesce(Job.error, "")).contains(code) for code in _REVIEW_CODES),
        ),
    )
    return safe_error, safe_result, Job.tenant_id == tenant_id, Job.status == status, Job.finished_at < cutoff


async def cleanup_terminal_job_history(
    *, tenant_id: int | None = None, now: datetime | None = None, batch_size: int = 250
) -> int:
    """Remove expired, ordinary terminal jobs while retaining unresolved evidence.

    A valid active `prune` task with mode `job_history` configures that
    workspace's policy. The task's cron schedule does not control retention.
    """
    if tenant_id is None and not is_bypass():
        tenant_id = current_tenant_id()
        if type(tenant_id) is not int or tenant_id <= 0:
            raise RuntimeError("Job history cleanup requires a tenant scope or trusted maintenance bypass")
    if tenant_id is not None and (type(tenant_id) is not int or tenant_id <= 0):
        raise ValueError("Invalid tenant id for job history cleanup")
    if not is_bypass() and current_tenant_id() != tenant_id:
        raise RuntimeError("Job history cleanup cannot cross the active tenant scope")
    now = now or datetime.now(timezone.utc)
    deleted_total = 0
    with tenant_scope(bypass=True) if tenant_id is None else tenant_scope(tenant_id):
        tenants = (
            list(await Tenant.objects.filter(is_active=True))
            if tenant_id is None
            else [await Tenant.objects.get(id=tenant_id)]
        )
        for tenant in tenants:
            if tenant is None:
                continue
            with tenant_scope(tenant.id):
                policies = list(
                    await AgentTask.objects.filter(job_type="prune", is_active=True).order_by(
                        AgentTask.updated_at.desc(), AgentTask.id.desc()
                    )
                )
                policy = next(
                    (retention_policy(task.payload) for task in policies if retention_policy(task.payload)), None
                )
                for status in ("done", "failed"):
                    cutoff = retention_cutoff(now, status, policy)
                    while True:
                        criteria = _retention_delete_criteria(tenant.id, status, cutoff)
                        candidates = list(
                            await Job.objects.filter(
                                *criteria,
                            )
                            .order_by(Job.finished_at.asc())
                            .limit(batch_size)
                        )
                        if not candidates:
                            break
                        count = await Job.objects.filter(
                            *criteria,
                            id__in=[job.id for job in candidates],
                        ).delete()
                        deleted_total += count
                        if len(candidates) < batch_size:
                            break
                        if count == 0:
                            break
    return deleted_total


async def reap_stale_jobs() -> int:
    """Quarantine stale claims using the existing conservative recovery policy."""
    from app.models.managers.job_manager import JobManager

    with tenant_scope(bypass=True):
        return await JobManager().reap_stale()
