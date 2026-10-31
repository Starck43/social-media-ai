"""Platform-level immutable evidence for successful workspace offboarding."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.core.decorators import app_label

from ..base import Base


@app_label("audit")
class WorkspaceOffboardingEvent(Base):
    """A minimal journal entry that survives deletion of its workspace and actor."""

    __tablename__ = "workspace_offboarding_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_workspace_offboarding_events_event_id"),
        UniqueConstraint("workspace_id", name="uq_workspace_offboarding_events_workspace_id"),
        CheckConstraint("event_id ~ '^[0-9a-f]{32}$'", name="ck_workspace_offboarding_events_event_id"),
        CheckConstraint(
            "workspace_id > 0 AND workspace_id <= 2147483647", name="ck_workspace_offboarding_events_workspace_id"
        ),
        CheckConstraint("actor_id > 0 AND actor_id <= 2147483647", name="ck_workspace_offboarding_events_actor_id"),
        CheckConstraint(
            "reason_code IN ('duplicate', 'owner_request', 'contract_ended', 'test_completed')",
            name="ck_workspace_offboarding_events_reason_code",
        ),
        CheckConstraint(
            "retention_policy IN ('preserve', 'expire_elapsed')",
            name="ck_workspace_offboarding_events_retention_policy",
        ),
        CheckConstraint("expired_audit_count >= 0", name="ck_workspace_offboarding_events_expired_audit_count"),
        CheckConstraint("outcome = 'succeeded'", name="ck_workspace_offboarding_events_outcome"),
        Index("ix_workspace_offboarding_events_occurred_at_id", "occurred_at", "id"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[str] = mapped_column(String(32), nullable=False)
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_id: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    reason_code: Mapped[str] = mapped_column(String(32), nullable=False)
    retention_policy: Mapped[str] = mapped_column(String(24), nullable=False)
    expired_audit_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, server_default="succeeded")

    if TYPE_CHECKING:
        from ..managers.workspace_offboarding_manager import WorkspaceOffboardingManager

        objects: ClassVar[WorkspaceOffboardingManager]
    else:
        objects: ClassVar = None
