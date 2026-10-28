"""Safe, non-receipt logging paths for non-success action outcomes."""

from __future__ import annotations

import logging
from typing import Any

_ACTION = "scheduling.task_admit"
_MAX_DB_ID = 2147483647
_DENIED_ROLLED_BACK_LOGGER = logging.getLogger("app.action_audit.denied_rolled_back")
_UNKNOWN_LOGGER = logging.getLogger("app.action_audit.unknown")
_REASONS = frozenset(
    {
        "admission_busy",
        "audit_write_failed",
        "commit_ack_unknown",
        "launch_unconfirmed",
        "operation_refused",
        "permission_denied",
        "transaction_rolled_back",
    }
)


def _safe_context(
    *,
    tenant_id: Any,
    actor_kind: str,
    actor_id: Any,
    resource_id: Any,
    job_id: Any,
    reason_code: str,
) -> tuple[Any, ...] | None:
    if type(tenant_id) is not int or not 0 < tenant_id <= 2147483647:
        return None
    if actor_kind not in {"user", "service", "scheduler"} or reason_code not in _REASONS:
        return None
    if actor_kind == "user":
        if type(actor_id) is not int or not 0 < actor_id <= _MAX_DB_ID:
            return None
    elif (actor_kind, actor_id) not in {("service", "task_runner"), ("scheduler", "scheduler")}:
        return None
    if resource_id is not None and (type(resource_id) is not int or not 0 < resource_id <= _MAX_DB_ID):
        return None
    if job_id is not None and (type(job_id) is not int or not 0 < job_id <= _MAX_DB_ID):
        return None
    return (
        tenant_id,
        actor_kind,
        actor_id,
        _ACTION,
        "agent_task",
        resource_id,
        job_id,
        reason_code,
    )


def log_denied(*, tenant_id: Any, actor_kind: str, actor_id: Any, task_id: Any = None, reason_code: str) -> None:
    """Log a safe refusal; no raw arguments, exception text or credentials."""
    fields = _safe_context(
        tenant_id=tenant_id,
        actor_kind=actor_kind,
        actor_id=actor_id,
        resource_id=task_id,
        job_id=None,
        reason_code=reason_code,
    )
    if fields is None:
        return
    _DENIED_ROLLED_BACK_LOGGER.warning(
        "action_audit_path=denied outcome=denied tenant_id=%s actor_kind=%s actor_id=%s "
        "action=%s resource_kind=%s resource_id=%s job_id=%s reason_code=%s",
        *fields,
    )


def log_rolled_back(
    *,
    tenant_id: Any,
    actor_kind: str,
    actor_id: Any,
    task_id: Any = None,
    reason_code: str = "transaction_rolled_back",
) -> None:
    """Log a safe transaction rollback after rollback is confirmed."""
    fields = _safe_context(
        tenant_id=tenant_id,
        actor_kind=actor_kind,
        actor_id=actor_id,
        resource_id=task_id,
        job_id=None,
        reason_code=reason_code,
    )
    if fields is None:
        return
    _DENIED_ROLLED_BACK_LOGGER.warning(
        "action_audit_path=denied_rolled_back outcome=rolled_back tenant_id=%s actor_kind=%s actor_id=%s "
        "action=%s resource_kind=%s resource_id=%s job_id=%s reason_code=%s",
        *fields,
    )


def log_unknown(
    *,
    tenant_id: Any,
    actor_kind: str,
    actor_id: Any,
    task_id: Any = None,
    job_id: Any = None,
    reason_code: str = "launch_unconfirmed",
) -> None:
    """Log a separate indeterminate category; it is not a receipt or replay key."""
    fields = _safe_context(
        tenant_id=tenant_id,
        actor_kind=actor_kind,
        actor_id=actor_id,
        resource_id=task_id,
        job_id=job_id,
        reason_code=reason_code,
    )
    if fields is None:
        return
    _UNKNOWN_LOGGER.error(
        "action_audit_path=unknown outcome=unknown tenant_id=%s actor_kind=%s actor_id=%s "
        "action=%s resource_kind=%s resource_id=%s job_id=%s reason_code=%s",
        *fields,
    )


def log_persistence_gap(
    *,
    tenant_id: Any,
    actor_kind: str,
    actor_id: Any,
    task_id: Any = None,
    job_id: Any = None,
) -> None:
    """Signal that an unknown outcome could not be persisted, without exception text."""
    fields = _safe_context(
        tenant_id=tenant_id,
        actor_kind=actor_kind,
        actor_id=actor_id,
        resource_id=task_id,
        job_id=job_id,
        reason_code="audit_write_failed",
    )
    if fields is None:
        return
    _UNKNOWN_LOGGER.critical(
        "action_audit_path=unknown_persistence_gap outcome=unknown persistence=failed "
        "tenant_id=%s actor_kind=%s actor_id=%s action=%s resource_kind=%s "
        "resource_id=%s job_id=%s reason_code=%s",
        *fields,
    )
