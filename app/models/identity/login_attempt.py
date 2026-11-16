"""Shared login-failure counters (P10-T04).

One row per throttled key, so the same budget applies on every login surface and
in every process. The table is global: a login attempt happens before a tenant
or an account is known, so it must not be tenant-scoped.

Rows are infrastructure, not user data. `identifier` holds a normalized
username/email or a client address; no password, hash or token is ever stored.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ...core.config import settings
from ..base import Base, TimestampMixin

#: Failures are counted per account and per client address.
ACCOUNT_SCOPE = "account"
ADDRESS_SCOPE = "address"


class LoginAttempt(Base, TimestampMixin):
    __tablename__ = "login_attempts"
    __table_args__ = (
        UniqueConstraint("scope", "identifier", name="uq_login_attempts_scope_identifier"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    identifier: Mapped[str] = mapped_column(String(255), nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __str__(self) -> str:
        return f"{self.scope}:{self.identifier}"
