"""Tenant-fenced, read-only action-audit support projections."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import require_model_perm
from app.models import ActionAuditEvent
from app.types import ActionType

router = APIRouter()


def _present(event: ActionAuditEvent) -> dict:
    """Return only the documented allowlisted envelope."""
    return {
        "event_id": event.event_id,
        "tenant_id": event.tenant_id,
        "occurred_at": event.occurred_at.isoformat(),
        "actor_kind": event.actor_kind,
        "actor_id": event.actor_id,
        "action": event.action,
        "resource_kind": event.resource_kind,
        "resource_id": event.resource_id,
        "outcome": event.outcome,
        "reason_code": event.reason_code,
        "request_id": event.request_id,
        "job_id": event.job_id,
        "receipt_ref": event.receipt_ref,
    }


async def _read(
    *,
    since: datetime | None,
    until: datetime | None,
    limit: int,
    before_id: int | None,
    outcome: str | None,
    action: str | None,
) -> dict:
    upper = until or datetime.now(timezone.utc)
    lower = since or upper - timedelta(days=30)
    try:
        rows = await ActionAuditEvent.objects.list_events(
            since=lower,
            until=upper,
            limit=limit,
            before_id=before_id,
            outcome=outcome,
            action=action,
        )
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=400, detail="Invalid action-audit query window or filter") from error
    return {
        "items": [_present(row) for row in rows],
        "next_before_id": rows[-1].id if len(rows) == limit else None,
        "limit": limit,
        "since": lower.isoformat(),
        "until": upper.isoformat(),
    }


@router.get(
    "/events",
    response_model=dict,
    dependencies=[Depends(require_model_perm("actionauditevent", ActionType.VIEW))],
)
async def list_action_audit_events(
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    before_id: int | None = Query(default=None, gt=0),
    outcome: str | None = None,
    action: str | None = None,
):
    """List bounded audit events for the authenticated member's workspace."""
    return await _read(
        since=since,
        until=until,
        limit=limit,
        before_id=before_id,
        outcome=outcome,
        action=action,
    )


@router.get(
    "/events/export",
    response_model=dict,
    dependencies=[Depends(require_model_perm("actionauditevent", ActionType.EXPORT))],
)
async def export_action_audit_events(
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=1000, ge=1, le=1000),
    before_id: int | None = Query(default=None, gt=0),
    outcome: str | None = None,
    action: str | None = None,
):
    """Export only the safe event envelope, bounded to 90 days and 1000 rows."""
    return await _read(
        since=since,
        until=until,
        limit=limit,
        before_id=before_id,
        outcome=outcome,
        action=action,
    )
