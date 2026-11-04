"""Tenant-scoped business write-approval command ledger and per-target batch items (P13-T04)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar

import sqlalchemy as sa
from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.core.decorators import app_label
from app.models.base import Base, TenantScopedMixin, TimestampMixin

S = settings.DB_SCHEMA
COMMAND_STATES = (
    "draft",
    "previewed",
    "approved",
    "claimed",
    "succeeded",
    "failed",
    "unknown",
    "cancelled",
    "expired",
)
TARGET_STATES = ("pending", "claimed", "succeeded", "failed", "unknown", "cancelled")
APPROVAL_POLICIES = ("per_item", "per_batch", "connection_consent")


def _choices(column: str, values: tuple[str, ...], name: str) -> CheckConstraint:
    return CheckConstraint(f"{column} IN ({', '.join(repr(v) for v in values)})", name=name)


@app_label("scheduling")
class BusinessCommand(Base, TenantScopedMixin, TimestampMixin):
    """Server-side approval and execution ledger for external business writes."""

    __tablename__ = "business_commands"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_business_commands_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "actor_user_id",
            "capability",
            "idempotency_key",
            name="uq_business_commands_idempotency",
        ),
        _choices("state", COMMAND_STATES, "ck_business_commands_state"),
        _choices("approval_policy", APPROVAL_POLICIES, "ck_business_commands_policy"),
        CheckConstraint("payload_hash ~ '^sha256:[0-9a-f]{64}$'", name="ck_business_commands_payload_hash"),
        CheckConstraint(
            "approved_payload_hash IS NULL OR approved_payload_hash ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_business_commands_approved_hash",
        ),
        Index("ix_business_commands_tenant_state", "tenant_id", "state"),
        Index("ix_business_commands_tenant_capability", "tenant_id", "capability"),
        {"schema": S},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    capability: Mapped[str] = mapped_column(String(80), nullable=False)
    capability_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    approval_policy: Mapped[str] = mapped_column(
        String(30), nullable=False, default="per_item", server_default="per_item"
    )
    actor_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey(f"{S}.users.id", ondelete="RESTRICT"), nullable=False
    )
    connection_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    target_refs: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list, server_default=sa.text("'[]'::json")
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=sa.text("'{}'::json")
    )
    payload_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    preview_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=sa.text("'{}'::json")
    )
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="draft", server_default="draft")
    approved_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey(f"{S}.users.id", ondelete="SET NULL"), nullable=True
    )
    approved_payload_hash: Mapped[str | None] = mapped_column(String(71), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approval_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reconcile_hint: Mapped[str | None] = mapped_column(Text, nullable=True)

    objects: ClassVar = None


@app_label("scheduling")
class BusinessCommandTarget(Base, TenantScopedMixin, TimestampMixin):
    """Per-target child execution row for single or batch business commands."""

    __tablename__ = "business_command_targets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "command_id"],
            [f"{S}.business_commands.tenant_id", f"{S}.business_commands.id"],
            ondelete="CASCADE",
            name="fk_business_command_target_command",
        ),
        UniqueConstraint("tenant_id", "command_id", "target_ref", name="uq_business_command_target_ref"),
        UniqueConstraint("tenant_id", "target_idempotency_key", name="uq_business_command_target_idempotency"),
        _choices("state", TARGET_STATES, "ck_business_command_targets_state"),
        Index("ix_business_command_targets_command", "tenant_id", "command_id"),
        {"schema": S},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    command_id: Mapped[int] = mapped_column(Integer, nullable=False)
    target_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    target_idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending")
    provider_receipt: Mapped[str | None] = mapped_column(String(160), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    objects: ClassVar = None
