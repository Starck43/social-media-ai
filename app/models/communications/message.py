"""Tenant-scoped metadata-only message, thread, and contact channel stats models (`P15-T02`).

Per Owner Decisions D3 and D11, only message metadata, encrypted header fields
(`subject_encrypted`, `sender_encrypted`, `recipient_encrypted`,
`participants_encrypted`), `original_url`, and `content_hash` (`sha256:<hex>`)
are persisted. Message bodies and attachments are never stored in these tables.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import ClassVar

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
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

COMMUNICATION_CHANNELS = ("email", "telegram", "max", "phone", "bitrix24")
MESSAGE_DIRECTIONS = ("inbound", "outbound")
TRIAGE_DECISION_CATEGORIES = ("needs_reply", "fyi", "can_wait", "uncertain")


def _sql_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


class MessageThread(Base, TimestampMixin, TenantScopedMixin):
    """Conversation thread grouping unified messages by RFC References/In-Reply-To or chat ID."""

    __tablename__ = "message_threads"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_message_threads_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "connection_id",
            "channel",
            "thread_key",
            name="uq_message_threads_key",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            [f"{settings.DB_SCHEMA}.workspace_connections.tenant_id", f"{settings.DB_SCHEMA}.workspace_connections.id"],
            ondelete="CASCADE",
            name="fk_message_threads_connection",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "contact_id"],
            [f"{settings.DB_SCHEMA}.contacts.tenant_id", f"{settings.DB_SCHEMA}.contacts.id"],
            ondelete="SET NULL",
            name="fk_message_threads_contact",
        ),
        CheckConstraint(f"channel IN ({_sql_list(COMMUNICATION_CHANNELS)})", name="ck_message_threads_channel"),
        CheckConstraint("message_count >= 0", name="ck_message_threads_message_count"),
        CheckConstraint("unread_count >= 0", name="ck_message_threads_unread_count"),
        Index("ix_message_threads_tenant_last_msg", "tenant_id", "last_message_at", "id"),
        Index("ix_message_threads_tenant_contact", "tenant_id", "contact_id"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pii_updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, server_default="1")
    connection_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    thread_key: Mapped[str] = mapped_column(String(255), nullable=False)
    subject_encrypted: Mapped[str | None] = mapped_column(EncryptedText(), nullable=True)
    participants_encrypted: Mapped[str | None] = mapped_column(EncryptedText(), nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    unread_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    objects: ClassVar = None

    @property
    def participants(self) -> list[str]:
        if not self.participants_encrypted:
            return []
        try:
            parsed = json.loads(self.participants_encrypted)
            if isinstance(parsed, list):
                return [str(item) for item in parsed if item]
        except (ValueError, TypeError):
            return [p.strip() for p in str(self.participants_encrypted).split(",") if p.strip()]
        return []


class UnifiedMessage(Base, TimestampMixin, TenantScopedMixin):
    """Metadata-only message record across email, Telegram, MAX, phone, and Bitrix24 (R-09, D3, D11)."""

    __tablename__ = "unified_messages"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_unified_messages_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "connection_id",
            "channel",
            "original_id",
            name="uq_unified_messages_origin",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            [f"{settings.DB_SCHEMA}.workspace_connections.tenant_id", f"{settings.DB_SCHEMA}.workspace_connections.id"],
            ondelete="CASCADE",
            name="fk_unified_messages_connection",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "contact_id"],
            [f"{settings.DB_SCHEMA}.contacts.tenant_id", f"{settings.DB_SCHEMA}.contacts.id"],
            ondelete="SET NULL",
            name="fk_unified_messages_contact",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "thread_id"],
            [f"{settings.DB_SCHEMA}.message_threads.tenant_id", f"{settings.DB_SCHEMA}.message_threads.id"],
            ondelete="SET NULL",
            name="fk_unified_messages_thread",
        ),
        CheckConstraint(f"channel IN ({_sql_list(COMMUNICATION_CHANNELS)})", name="ck_unified_messages_channel"),
        CheckConstraint(f"direction IN ({_sql_list(MESSAGE_DIRECTIONS)})", name="ck_unified_messages_direction"),
        Index("ix_unified_messages_tenant_thread_sent", "tenant_id", "thread_id", "sent_at", "id"),
        Index("ix_unified_messages_tenant_contact_sent", "tenant_id", "contact_id", "sent_at", "id"),
        Index("ix_unified_messages_tenant_msgid_hash", "tenant_id", "connection_id", "external_message_id_hash"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pii_updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, server_default="1")
    connection_id: Mapped[int] = mapped_column(Integer, nullable=False)
    contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    thread_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    original_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_message_id_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    in_reply_to_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    direction: Mapped[str] = mapped_column(String(20), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    subject_encrypted: Mapped[str | None] = mapped_column(EncryptedText(), nullable=True)
    sender_encrypted: Mapped[str | None] = mapped_column(EncryptedText(), nullable=True)
    recipient_encrypted: Mapped[str | None] = mapped_column(EncryptedText(), nullable=True)
    original_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(71), nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    is_hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    unavailable_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    objects: ClassVar = None

    @property
    def is_unavailable(self) -> bool:
        return self.unavailable_at is not None


class ContactChannelStat(Base, TimestampMixin, TenantScopedMixin):
    """Per-contact, per-channel communication counters and activity timestamps (`P15-T02`)."""

    __tablename__ = "contact_channel_stats"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_contact_channel_stats_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "contact_id",
            "channel",
            name="uq_contact_channel_stats_contact_channel",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "contact_id"],
            [f"{settings.DB_SCHEMA}.contacts.tenant_id", f"{settings.DB_SCHEMA}.contacts.id"],
            ondelete="CASCADE",
            name="fk_contact_channel_stats_contact",
        ),
        CheckConstraint(f"channel IN ({_sql_list(COMMUNICATION_CHANNELS)})", name="ck_contact_channel_stats_channel"),
        CheckConstraint("inbound_count >= 0", name="ck_contact_channel_stats_inbound_nonneg"),
        CheckConstraint("outbound_count >= 0", name="ck_contact_channel_stats_outbound_nonneg"),
        CheckConstraint("latency_total_ms >= 0", name="ck_contact_channel_stats_latency_total_nonneg"),
        CheckConstraint("latency_sample_count >= 0", name="ck_contact_channel_stats_latency_count_nonneg"),
        Index("ix_contact_channel_stats_tenant_contact", "tenant_id", "contact_id"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[int] = mapped_column(Integer, nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    inbound_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    outbound_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    latency_total_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    latency_sample_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    latency_pending_outbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_outbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    objects: ClassVar = None


class ThreadTriageDecision(Base, TimestampMixin, TenantScopedMixin):
    """Durable tenant-scoped user triage classification decision on a conversation thread (P20-T09).

    Records explicit user corrections (basis='user_override', is_model_fact=False)
    so overrides survive restarts and are shared across workers within the tenant workspace.
    """

    __tablename__ = "thread_triage_decisions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_thread_triage_decisions_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "thread_id",
            name="uq_thread_triage_decisions_thread",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "thread_id"],
            [f"{settings.DB_SCHEMA}.message_threads.tenant_id", f"{settings.DB_SCHEMA}.message_threads.id"],
            ondelete="CASCADE",
            name="fk_thread_triage_decisions_thread",
        ),
        CheckConstraint(
            f"category IN ({_sql_list(TRIAGE_DECISION_CATEGORIES)})",
            name="ck_thread_triage_decisions_category",
        ),
        Index("ix_thread_triage_decisions_tenant_thread", "tenant_id", "thread_id"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pii_updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, server_default="1")
    thread_id: Mapped[int] = mapped_column(Integer, nullable=False)
    decided_by_user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey(f"{settings.DB_SCHEMA}.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    category: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_encrypted: Mapped[str | None] = mapped_column(EncryptedText(), nullable=True)
    objects: ClassVar = None

