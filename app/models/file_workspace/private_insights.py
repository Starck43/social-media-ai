"""Default-off private retention tables; accessed only through FileInsightManager."""

from sqlalchemy import (
    Boolean,
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.models.base import Base, TenantScopedMixin, TimestampMixin


def _fk(columns, target, remote):
    return ForeignKeyConstraint(
        columns, [f"{settings.DB_SCHEMA}.{target}.{name}" for name in remote], ondelete="CASCADE"
    )


class FileInsightMount(TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "file_insight_mounts"
    __table_args__ = (
        _fk(["tenant_id", "connection_id"], "workspace_connections", ["tenant_id", "id"]),
        UniqueConstraint("tenant_id", "connection_id", name="uq_file_insight_mount_scope"),
        CheckConstraint("NOT retention_allowed OR processing_allowed", name="ck_file_insight_consent"),
    )
    connection_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey(f"{settings.DB_SCHEMA}.users.id", ondelete="CASCADE"))
    processing_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    retention_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    owner_epoch: Mapped[str] = mapped_column(String(32))
    connection_epoch: Mapped[str] = mapped_column(String(32))
    consent_revision: Mapped[str] = mapped_column(String(32))
    policy_revision: Mapped[str] = mapped_column(String(64))
    clock_highwater = mapped_column(DateTime(timezone=True), nullable=False)


class FileInsightObjectState(TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "file_insight_object_states"
    __table_args__ = (
        _fk(["tenant_id", "connection_id"], "file_insight_mounts", ["tenant_id", "connection_id"]),
        _fk(["tenant_id", "object_id"], "file_catalog_objects", ["tenant_id", "id"]),
    )
    object_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connection_id: Mapped[int] = mapped_column(Integer)
    epoch: Mapped[str] = mapped_column(String(32))


class FileInsightAttempt(TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "file_insight_attempts"
    __table_args__ = (
        _fk(["tenant_id", "connection_id"], "file_insight_mounts", ["tenant_id", "connection_id"]),
        ForeignKeyConstraint(
            ["object_id"], [f"{settings.DB_SCHEMA}.file_insight_object_states.object_id"], ondelete="CASCADE"
        ),
        CheckConstraint("expires_at = captured_at + interval '24 hours'", name="ck_file_insight_attempt_expiry"),
        CheckConstraint(
            "jsonb_typeof(binding_data) = 'object' AND octet_length(binding_data::text) <= 4096",
            name="ck_file_insight_attempt_binding",
        ),
        CheckConstraint(
            "provenance IS NULL OR (jsonb_typeof(provenance) = 'object' AND octet_length(provenance::text) <= 4096)",
            name="ck_file_insight_attempt_provenance",
        ),
        Index("ix_file_insight_attempt_expiry", "tenant_id", "expires_at"),
        Index("ix_file_insight_attempt_scope", "tenant_id", "connection_id", "object_id"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    connection_id: Mapped[int] = mapped_column(Integer)
    object_id: Mapped[int] = mapped_column(Integer)
    owner_id: Mapped[int] = mapped_column(ForeignKey(f"{settings.DB_SCHEMA}.users.id", ondelete="CASCADE"))
    captured_at = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at = mapped_column(DateTime(timezone=True), nullable=False)
    binding_data: Mapped[dict] = mapped_column(JSONB)
    provenance: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    generation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    consumed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")


class FilePrivateInsight(TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "file_private_insights"
    __table_args__ = (
        _fk(["tenant_id", "connection_id"], "file_insight_mounts", ["tenant_id", "connection_id"]),
        UniqueConstraint("attempt_id", name="uq_file_private_insight_attempt"),
        CheckConstraint("charged_size BETWEEN 1 AND 65536", name="ck_file_private_insight_charge"),
        CheckConstraint("octet_length(payload_encrypted) <= 90000", name="ck_file_private_insight_cipher"),
        CheckConstraint("expires_at = captured_at + interval '24 hours'", name="ck_file_private_insight_expiry"),
        Index("ix_file_private_insight_quota", "tenant_id", "owner_id", "connection_id", "object_id"),
        Index("ix_file_private_insight_expiry", "tenant_id", "expires_at"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(
        ForeignKey(f"{settings.DB_SCHEMA}.file_insight_attempts.id", ondelete="CASCADE")
    )
    connection_id: Mapped[int] = mapped_column(Integer)
    object_id: Mapped[int] = mapped_column(
        ForeignKey(f"{settings.DB_SCHEMA}.file_catalog_objects.id", ondelete="CASCADE")
    )
    owner_id: Mapped[int] = mapped_column(ForeignKey(f"{settings.DB_SCHEMA}.users.id", ondelete="CASCADE"))
    payload_encrypted: Mapped[str] = mapped_column(Text)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    charged_size: Mapped[int] = mapped_column(Integer)
    captured_at = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at = mapped_column(DateTime(timezone=True), nullable=False)
