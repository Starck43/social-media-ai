"""Provider configuration and separately encrypted, connection-bound secrets."""

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped

from app.core.config import settings
from app.models.base import Base, TenantScopedMixin, TimestampMixin


class WorkspaceConnection(Base, TenantScopedMixin, TimestampMixin):
    __tablename__ = "workspace_connections"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_workspace_connection_tenant"),
        UniqueConstraint(
            "tenant_id",
            "owner_user_id",
            "provider_id",
            "auth_method",
            "account_key",
            name="uq_workspace_connection_account",
        ),
        CheckConstraint(
            "state IN ('unverified','verified','error','reauth','disabled')", name="ck_workspace_connection_state"
        ),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    owner_user_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.users.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[str] = Column(String(30), nullable=False)
    provider_id: Mapped[str] = Column(String(50), nullable=False)
    auth_method: Mapped[str] = Column(String(30), nullable=False)
    schema_version: Mapped[int] = Column(Integer, nullable=False)
    label: Mapped[str] = Column(String(100), nullable=False)
    account_key: Mapped[str] = Column(String(320), nullable=False)
    config: Mapped[dict] = Column(JSON, nullable=False)
    is_active: Mapped[bool] = Column(Boolean, nullable=False, default=True, server_default="true")
    state: Mapped[str] = Column(String(20), nullable=False, default="unverified", server_default="unverified")
    checked_at = Column(DateTime(timezone=True), nullable=True)
    check_code: Mapped[str | None] = Column(String(40), nullable=True)


class WorkspaceConnectionSecret(Base, TimestampMixin):
    """One secret belongs to exactly one connection; it cannot be rebound by form input."""

    __tablename__ = "workspace_connection_secrets"
    __table_args__ = ({"schema": settings.DB_SCHEMA},)

    connection_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.workspace_connections.id", ondelete="CASCADE"), primary_key=True
    )
    secret_encrypted: Mapped[str] = Column(Text, nullable=False)
