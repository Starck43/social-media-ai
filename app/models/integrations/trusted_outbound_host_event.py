"""Installation-level trusted-host change evidence (P14-T09)."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.models.base import Base


class TrustedOutboundHostEvent(Base):
    """Installation-wide evidence, not workspace-scoped.

    No tenant_id under the owner-authorized 2026-11-04 tenant-isolation
    exception. Plain actor and host-entry identities survive deletion of those
    rows. This ledger is append-only through its purpose-built writer.
    """

    __tablename__ = "trusted_outbound_host_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_trusted_outbound_host_events_event_id"),
        CheckConstraint("event_id ~ '^[0-9a-f]{32}$'", name="ck_trusted_outbound_host_events_event_id"),
        CheckConstraint(
            "action IN ('admin.trusted_host_add', 'admin.trusted_host_remove')",
            name="ck_trusted_outbound_host_events_action",
        ),
        CheckConstraint("actor_id > 0", name="ck_trusted_outbound_host_events_actor"),
        CheckConstraint("host_entry_id > 0", name="ck_trusted_outbound_host_events_entry"),
        CheckConstraint("jsonb_typeof(snapshot) = 'object'", name="ck_trusted_outbound_host_events_snapshot"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[int] = mapped_column(Integer, nullable=False)
    host_entry_id: Mapped[int] = mapped_column(Integer, nullable=False)
    host: Mapped[str] = mapped_column(String(253), nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
