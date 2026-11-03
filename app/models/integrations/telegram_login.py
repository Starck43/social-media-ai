"""Bounded encrypted Telegram login state; codes and passwords are never stored."""

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped

from app.core.config import settings
from app.models.base import Base, TenantScopedMixin, TimestampMixin


class TelegramLoginAttempt(Base, TenantScopedMixin, TimestampMixin):
    __tablename__ = "telegram_login_attempts"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "request_key", name="uq_telegram_login_request"),
        CheckConstraint(
            "state IN ('processing','code','password','completed','failed','expired','cancelled')",
            name="ck_telegram_login_state",
        ),
        CheckConstraint(
            "revision > 0 AND code_attempts >= 0 AND password_attempts >= 0", name="ck_telegram_login_counters"
        ),
        Index(
            "uq_telegram_login_pending_owner",
            "owner_user_id",
            unique=True,
            postgresql_where=text("state IN ('processing','code','password')"),
        ),
        Index("ix_telegram_login_owner_created", "owner_user_id", "created_at"),
        {"schema": settings.DB_SCHEMA},
    )
    id: Mapped[str] = Column(String(36), primary_key=True)
    owner_user_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.users.id", ondelete="CASCADE"), nullable=False
    )
    request_key: Mapped[str] = Column(String(36), nullable=False)
    state: Mapped[str] = Column(String(20), nullable=False)
    revision: Mapped[int] = Column(Integer, nullable=False, default=1, server_default="1")
    code_attempts: Mapped[int] = Column(Integer, nullable=False, default=0, server_default="0")
    password_attempts: Mapped[int] = Column(Integer, nullable=False, default=0, server_default="0")
    expires_at = Column(DateTime(timezone=True), nullable=False)
    secret_encrypted: Mapped[str] = Column(Text, nullable=False)
    failure_code: Mapped[str | None] = Column(String(40), nullable=True)
    authorization_id = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.personal_connector_authorizations.id", ondelete="SET NULL")
    )
    grant_id = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.workspace_authorization_grants.id", ondelete="SET NULL")
    )
