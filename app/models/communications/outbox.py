"""Encrypted transport outbox. No implicit retry of a durably claimed target."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
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

S = settings.DB_SCHEMA
STATES = "'draft','validated','approved','claimed','sent','uncertain_delivery','failed','cancelled'"


class OutboxMessage(Base, TenantScopedMixin, TimestampMixin):
    __tablename__ = "outbox_messages"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_outbox_message_tenant"),
        UniqueConstraint("tenant_id", "author_id", "idempotency_key", name="uq_outbox_message_request"),
        ForeignKeyConstraint(
            ["tenant_id", "contact_id"], [f"{S}.contacts.tenant_id", f"{S}.contacts.id"], ondelete="RESTRICT"
        ),
        CheckConstraint(f"state IN ({STATES})", name="ck_outbox_message_state"),
        CheckConstraint(
            "state NOT IN ('approved','claimed','sent','uncertain_delivery') OR (approved_by IS NOT NULL AND approved_hash IS NOT NULL AND approval_expires_at IS NOT NULL AND scheduled_at IS NOT NULL)",
            name="ck_outbox_message_approval",
        ),
        CheckConstraint("purpose IN ('transactional','greeting','marketing')", name="ck_outbox_purpose"),
        Index("ix_outbox_due", "tenant_id", "state", "scheduled_at", "id"),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    author_id: Mapped[int] = mapped_column(ForeignKey(f"{S}.users.id", ondelete="RESTRICT"), nullable=False)
    contact_id: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    subject_encrypted: Mapped[str | None] = mapped_column(EncryptedText())
    content_encrypted: Mapped[str] = mapped_column(EncryptedText(), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="draft")
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    timezone: Mapped[str] = mapped_column(String(100), nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[int | None] = mapped_column(ForeignKey(f"{S}.users.id", ondelete="RESTRICT"))
    approved_hash: Mapped[str | None] = mapped_column(String(71))
    approval_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pii_updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, server_default="1")


class OutboxTarget(Base, TenantScopedMixin, TimestampMixin):
    __tablename__ = "outbox_targets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_outbox_target_tenant"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_outbox_target_attempt"),
        UniqueConstraint(
            "tenant_id", "message_id", "channel", "connection_id", "identity_id", name="uq_outbox_destination"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "message_id"],
            [f"{S}.outbox_messages.tenant_id", f"{S}.outbox_messages.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            [f"{S}.workspace_connections.tenant_id", f"{S}.workspace_connections.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "command_id"],
            [f"{S}.business_commands.tenant_id", f"{S}.business_commands.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(f"state IN ({STATES})", name="ck_outbox_target_state"),
        CheckConstraint("channel IN ('email','telegram','max')", name="ck_outbox_target_channel"),
        CheckConstraint(
            "state NOT IN ('claimed','sent','uncertain_delivery') OR attempts = 1", name="ck_outbox_attempt_state"
        ),
        CheckConstraint("state != 'approved' OR dispatch_after IS NOT NULL", name="ck_outbox_approved_due"),
        CheckConstraint("attempts IN (0,1)", name="ck_outbox_single_attempt"),
        CheckConstraint(
            "(attempts = 0 AND claim_token IS NULL AND claimed_at IS NULL) OR (attempts = 1 AND claim_token IS NOT NULL AND claimed_at IS NOT NULL)",
            name="ck_outbox_claim_fence",
        ),
        CheckConstraint("delivery_started_at IS NULL OR attempts = 1", name="ck_outbox_delivery_started"),
        Index("ix_outbox_target_message", "tenant_id", "message_id", "state"),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    connection_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # Snapshot remains available if the identity is deleted; fresh resolution then fails closed.
    identity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    recipient_encrypted: Mapped[str] = mapped_column(EncryptedText(), nullable=False)
    credential_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    command_id: Mapped[int | None] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="validated")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    dispatch_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_token: Mapped[str | None] = mapped_column(String(36))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    error_code: Mapped[str | None] = mapped_column(String(64))
    pii_updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, server_default="1")
