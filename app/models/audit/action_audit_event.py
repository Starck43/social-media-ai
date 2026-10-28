"""Tenant-scoped, allowlisted action-audit evidence."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.core.decorators import app_label

from ..base import Base, TenantScopedMixin


@app_label("audit")
class ActionAuditEvent(Base, TenantScopedMixin):
    """Immutable cross-surface correlation evidence, never a billing receipt.

    Foreign row identities are intentionally plain values: audit correlation
    survives task/job pruning and does not authorize access to those rows.
    """

    __tablename__ = "action_audit_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_action_audit_events_event_id"),
        CheckConstraint(
            "event_id ~ '^[0-9a-f]{32}$'",
            name="ck_action_audit_events_event_id",
        ),
        CheckConstraint(
            "actor_kind IN ('user', 'service', 'scheduler')",
            name="ck_action_audit_events_actor_kind",
        ),
        CheckConstraint(
            "outcome IN ('succeeded', 'unknown')",
            name="ck_action_audit_events_outcome",
        ),
        CheckConstraint(
            "action = 'scheduling.task_admit'",
            name="ck_action_audit_events_action",
        ),
        CheckConstraint(
            "(actor_kind = 'user' AND actor_id ~ '^[1-9][0-9]{0,9}$') OR "
            "(actor_kind = 'service' AND actor_id = 'task_runner') OR "
            "(actor_kind = 'scheduler' AND actor_id = 'scheduler')",
            name="ck_action_audit_events_actor_id",
        ),
        CheckConstraint(
            "resource_kind IS NULL OR resource_kind = 'agent_task'",
            name="ck_action_audit_events_resource_kind",
        ),
        CheckConstraint(
            "resource_id IS NULL OR resource_id ~ '^[1-9][0-9]{0,9}$'",
            name="ck_action_audit_events_resource_id",
        ),
        CheckConstraint(
            "(outcome = 'succeeded' AND reason_code IS NULL) OR "
            "(outcome = 'unknown' AND reason_code = 'launch_unconfirmed')",
            name="ck_action_audit_events_reason_code",
        ),
        CheckConstraint("request_id IS NULL", name="ck_action_audit_events_request_id"),
        CheckConstraint("receipt_ref IS NULL", name="ck_action_audit_events_receipt_ref"),
        CheckConstraint(
            "job_id IS NULL OR (job_id > 0 AND job_id <= 2147483647)", name="ck_action_audit_events_job_id"
        ),
        Index("ix_action_audit_events_tenant_time_id", "tenant_id", "occurred_at", "id"),
        Index("ix_action_audit_events_tenant_outcome_time", "tenant_id", "outcome", "occurred_at"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Override the mixin's cascade default: an offboarding flow must explicitly
    # expire audit evidence before deleting a workspace.
    tenant_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.tenants.id", ondelete="RESTRICT"), nullable=False
    )
    event_id: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    actor_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_kind: Mapped[str | None] = mapped_column(String(40), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    receipt_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)

    if TYPE_CHECKING:
        from ..managers.action_audit_event_manager import ActionAuditEventManager

        objects: ClassVar[ActionAuditEventManager]
    else:
        objects: ClassVar = None
