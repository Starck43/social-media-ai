"""Internal cross-process provider quota state; no generic manager or CRUD.

This global coordination record contains only a keyed account digest and quota
state. It intentionally has no customer content, tenant reference or secrets.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ProviderRateBudget(Base):
    __tablename__ = "provider_rate_budgets"
    __table_args__ = (
        CheckConstraint("tokens >= 0", name="ck_provider_rate_tokens_nonnegative"),
        CheckConstraint("length(account_key) = 64", name="ck_provider_rate_account_digest"),
    )

    provider: Mapped[str] = mapped_column(String(30), primary_key=True)
    account_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    tokens: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
