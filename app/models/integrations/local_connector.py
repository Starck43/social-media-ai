"""Tenant-owned device registrations, bounded pairing and digest-only replay state."""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.models.base import Base, TenantScopedMixin
from app.models.types.encrypted import EncryptedText

S = settings.DB_SCHEMA


class LocalConnector(Base, TenantScopedMixin):
    __tablename__ = "local_connectors"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_local_connector_tenant"),
        UniqueConstraint("tenant_id", "cert_serial", name="uq_local_connector_certificate"),
        CheckConstraint("epoch > 0 AND consent_epoch > 0 AND policy_epoch > 0", name="ck_local_connector_epochs"),
        Index("ix_local_connector_owner", "tenant_id", "owner_user_id", "id"),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey(f"{S}.users.id", ondelete="CASCADE"), nullable=False)
    device_name_encrypted: Mapped[str] = mapped_column(EncryptedText(), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    certificate_pem: Mapped[str] = mapped_column(Text, nullable=False)
    cert_serial: Mapped[str] = mapped_column(String(64), nullable=False)
    epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    consent_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    policy_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    cloud_roots: Mapped[list] = mapped_column(JSON, nullable=False, default=list, server_default="[]")
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    objects: ClassVar = None


class LocalConnectorPairing(Base, TenantScopedMixin):
    __tablename__ = "local_connector_pairings"
    __table_args__ = (
        UniqueConstraint("pairing_id", name="uq_local_connector_pairing_id"),
        ForeignKeyConstraint(["tenant_id", "device_id"], [f"{S}.local_connectors.tenant_id", f"{S}.local_connectors.id"], ondelete="CASCADE"),
        CheckConstraint("attempts >= 0 AND attempts <= 5", name="ck_local_connector_pairing_attempts"),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pairing_id: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey(f"{S}.users.id", ondelete="CASCADE"), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    public_key: Mapped[str | None] = mapped_column(Text)
    device_name_encrypted: Mapped[str] = mapped_column(EncryptedText(), nullable=False)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    device_id: Mapped[int | None] = mapped_column(Integer)
    objects: ClassVar = None


class LocalConnectorRequest(Base, TenantScopedMixin):
    __tablename__ = "local_connector_requests"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "device_id"], [f"{S}.local_connectors.tenant_id", f"{S}.local_connectors.id"], ondelete="CASCADE"),
        UniqueConstraint("tenant_id", "device_id", "request_id", name="uq_local_connector_request"),
        CheckConstraint("state IN ('dispatched','completed','unknown')", name="ck_local_connector_request_state"),
        Index("ix_local_connector_request_expiry", "tenant_id", "device_id", "expires_at"),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(Integer, nullable=False)
    request_id: Mapped[str] = mapped_column(String(32), nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    objects: ClassVar = None
