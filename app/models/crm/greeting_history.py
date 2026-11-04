"""Tenant-scoped CRM greeting history and AI draft ledger (`greeting_history`, P14-T04).

Invariants:
- Unique per `(tenant_id, contact_id, event_type, year)` (`uq_greeting_history_contact_event_year`)
  to prevent duplicate greetings or repeat drafts for the same event in the same calendar year.
- Draft text and subject are stored in Fernet `EncryptedText` columns (`draft_text_encrypted`,
  `subject_encrypted`) and registered in `cli/commands/pii.py` `REGISTRY` with monotonic
  `pii_updated_at` marker bumped by the `contact_pii_marker` trigger.
- Sending remains out of scope until P14-T05 (bound via `command_id` to `business_commands`).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import ClassVar

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.models.base import Base, TenantScopedMixin, TimestampMixin
from app.models.types.encrypted import EncryptedText

GREETING_EVENT_TYPES = ("birthday", "anniversary")
GREETING_STATUSES = ("draft", "approved", "sent", "failed", "skipped", "unknown")
GREETING_CHANNELS = ("email", "phone", "telegram", "max", "bitrix24")


def _sql_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


class GreetingHistory(Base, TimestampMixin, TenantScopedMixin):
    """Per-contact, per-event, per-year greeting draft and delivery history."""

    __tablename__ = "greeting_history"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_greeting_history_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "contact_id",
            "event_type",
            "year",
            name="uq_greeting_history_contact_event_year",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "contact_id"],
            [f"{settings.DB_SCHEMA}.contacts.tenant_id", f"{settings.DB_SCHEMA}.contacts.id"],
            ondelete="CASCADE",
            name="fk_greeting_history_contact",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "command_id"],
            [f"{settings.DB_SCHEMA}.business_commands.tenant_id", f"{settings.DB_SCHEMA}.business_commands.id"],
            ondelete="SET NULL",
            name="fk_greeting_history_command",
        ),
        CheckConstraint(f"event_type IN ({_sql_list(GREETING_EVENT_TYPES)})", name="ck_greeting_history_event_type"),
        CheckConstraint(f"status IN ({_sql_list(GREETING_STATUSES)})", name="ck_greeting_history_status"),
        CheckConstraint(
            f"channel IS NULL OR channel IN ({_sql_list(GREETING_CHANNELS)})",
            name="ck_greeting_history_channel",
        ),
        CheckConstraint("year >= 1970 AND year <= 2100", name="ck_greeting_history_year"),
        Index("ix_greeting_history_tenant_year_status", "tenant_id", "year", "status"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pii_updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, server_default="1")
    contact_id: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    channel: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft")
    ai_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    command_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    subject_encrypted: Mapped[str | None] = mapped_column(EncryptedText(length=512), nullable=True)
    draft_text_encrypted: Mapped[str] = mapped_column(EncryptedText(length=16000), nullable=False)
    objects: ClassVar = None
