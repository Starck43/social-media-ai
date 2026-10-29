"""Prune job implementation with explicit facade dependencies."""

from typing import Any


async def handle_prune(payload: dict[str, Any], *, logger, log_id, error_kind) -> dict[str, Any]:
    """Retention: trim old finished jobs (default: older than 7 days).

    Analysis results older than `DATA_RETENTION_MONTHS` calendar months (by
    last write) are deleted in the current workspace, except rows a bot action
    still references; unanalysed raw content never outlives that same cutoff.

    Also sweeps raw content nobody ever analysed. `collected_items` is a
    write-ahead copy, so a collection whose analysis keeps failing (or a run
    with no job behind it, whose `run_id` is NULL) would keep its rows for
    ever; this is the ceiling on that. The sweep the table's own docstrings
    promise was never wired to anything — without it, "a failed analysis keeps
    the content" quietly becomes "keeps it until the disk fills".
    """
    from datetime import datetime, timedelta, timezone

    from app.core.config import settings
    from app.core.database import new_session
    from app.models import ActionAuditEvent, AIAnalytics, CollectedItem
    from app.utils.retention import months_before

    now = datetime.now(timezone.utc)
    action_audit_deleted = 0
    action_audit_retention_failed = False
    try:
        action_audit_deleted = await ActionAuditEvent.objects.delete_expired(now=now)
    except Exception as error:  # noqa: BLE001 - report a safe lifecycle failure
        action_audit_retention_failed = True
        from app.core.tenant_context import current_tenant_id

        logger.warning(
            "action_audit_retention_failed tenant_id=%s job_id=%s error_code=%s",
            log_id(current_tenant_id()),
            log_id(payload.get("job_id")),
            error_kind(error),
        )
    if payload.get("mode") == "job_history":
        # The active payload configures built-in retention independently of
        # cron; an explicit run also applies that policy immediately.
        from app.jobs.lifecycle import retention_policy

        policy = retention_policy(payload)
        if policy is None:
            raise ValueError("Некорректный срок хранения истории заданий")
        from app.core.tenant_context import current_tenant_id
        from app.jobs.lifecycle import cleanup_terminal_job_history

        deleted_jobs = await cleanup_terminal_job_history(tenant_id=current_tenant_id())
        return {
            "status": "policy_saved",
            "success_hours": policy[0],
            "failure_hours": policy[1],
            "deleted": deleted_jobs,
            "action_audit_deleted": action_audit_deleted,
            "action_audit_retention_failed": action_audit_retention_failed,
        }

    retention_months = settings.DATA_RETENTION_MONTHS
    retention_cutoff = months_before(now, retention_months)
    # Job history retention is a built-in lifecycle policy. Legacy prune tasks
    # continue to manage raw and analytics data, but cannot bypass that policy.
    deleted = 0
    days = max(1, int(payload.get("days", 7)))

    # Unanalysed raw content: same age budget as the job history. Raw text is
    # kept only while an analysis might still want it, so `staged_days` follows
    # `days` unless the task says otherwise.
    staged_days = min(int(payload.get("staged_days", days)), (now - retention_cutoff).days)
    staged_deleted = 0
    session = new_session()
    try:
        async with session.begin():
            staged_deleted = await CollectedItem.objects.delete_older_than(session, staged_days)
    except Exception as e:  # noqa: BLE001 — a sweep failure must not fail the prune
        logger.warning(
            "staged_retention_failed job_id=%s error_code=%s",
            log_id(payload.get("job_id")),
            error_kind(e),
        )
    finally:
        await session.close()

    # Analysis results: the configured data retention, not the job-history age.
    analytics_deleted = 0
    analytics_retention_failed = False
    try:
        analytics_deleted = await AIAnalytics.objects.delete_expired(retention_cutoff)
    except Exception as e:  # noqa: BLE001 — reported in the result, never silently "0 deleted"
        analytics_retention_failed = True
        logger.warning(
            "analytics_retention_failed job_id=%s error_code=%s",
            log_id(payload.get("job_id")),
            error_kind(e),
        )

    result = {
        "deleted": deleted,
        "staged_deleted": staged_deleted,
        "staged_days": staged_days,
        "analytics_deleted": analytics_deleted,
        "retention_months": retention_months,
        "action_audit_deleted": action_audit_deleted,
    }
    if action_audit_retention_failed:
        result["action_audit_retention_failed"] = True
    if analytics_retention_failed:
        result["analytics_retention_failed"] = True
    return result
