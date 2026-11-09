"""Revision-bound approvals with encrypted comments and tenant-scoped keys."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.core.decorators import app_label
from app.models.base import Base, TenantScopedMixin, TimestampMixin
from app.models.types.encrypted import EncryptedText

S = settings.DB_SCHEMA


class EncryptedComment(EncryptedText):
    impl = Text
    cache_ok = True


@app_label("knowledge")
class DocumentApproval(Base, TenantScopedMixin, TimestampMixin):
    __tablename__ = "document_approvals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            [f"{S}.knowledge_documents.tenant_id", f"{S}.knowledge_documents.id"],
            ondelete="CASCADE",
            name="fk_approval_document",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_id", "revision_id"],
            [f"{S}.document_revisions.tenant_id", f"{S}.document_revisions.document_id", f"{S}.document_revisions.id"],
            ondelete="CASCADE",
            name="fk_approval_revision",
        ),
        UniqueConstraint("tenant_id", "requester_user_id", "request_idempotency_key", name="uq_approval_request"),
        CheckConstraint(
            "status IN ('pending','approved','rejected','revision_requested','expired')", name="ck_approval_status"
        ),
        CheckConstraint("revision_hash ~ '^sha256:[0-9a-f]{64}$'", name="ck_approval_revision_hash"),
        CheckConstraint("request_hash ~ '^sha256:[0-9a-f]{64}$'", name="ck_approval_request_hash"),
        CheckConstraint(
            "decision_hash IS NULL OR decision_hash ~ '^sha256:[0-9a-f]{64}$'", name="ck_approval_decision_hash"
        ),
        CheckConstraint("expires_at > requested_at", name="ck_approval_expiry"),
        CheckConstraint(
            "(status IN ('approved','rejected','revision_requested') AND responded_at IS NOT NULL AND decision_hash IS NOT NULL) OR (status IN ('pending','expired') AND responded_at IS NULL AND decision_hash IS NULL)",
            name="ck_approval_decision",
        ),
        Index(
            "uq_approval_pending_document",
            "tenant_id",
            "document_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_approval_inbox_expiry", "tenant_id", "approver_user_id", "status", "expires_at", "id"),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    document_id: Mapped[int] = mapped_column(Integer, nullable=False)
    revision_id: Mapped[int] = mapped_column(Integer, nullable=False)
    revision_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    requester_user_id: Mapped[int | None] = mapped_column(ForeignKey(f"{S}.users.id", ondelete="SET NULL"))
    approver_user_id: Mapped[int | None] = mapped_column(ForeignKey(f"{S}.users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    comment_encrypted: Mapped[str | None] = mapped_column(EncryptedComment())
    request_idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    decision_hash: Mapped[str | None] = mapped_column(String(71))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    pii_updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="1")
