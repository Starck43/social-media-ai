"""Tenant-owned quota state; access only through the dedicated manager."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, ForeignKeyConstraint, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.models.base import Base, TenantScopedMixin


class ProviderRateBudget(Base, TenantScopedMixin):
    __tablename__ = "provider_rate_budgets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            [f"{settings.DB_SCHEMA}.workspace_connections.tenant_id", f"{settings.DB_SCHEMA}.workspace_connections.id"],
            ondelete="CASCADE",
            name="fk_rate_budget_tenant_connection",
        ),
        CheckConstraint("tokens >= 0", name="ck_provider_rate_tokens_nonnegative"),
        CheckConstraint("length(account_key) = 64", name="ck_provider_rate_account_digest"),
    )

    tenant_id: Mapped[int] = mapped_column(
        ForeignKey(f"{settings.DB_SCHEMA}.tenants.id", ondelete="CASCADE"), primary_key=True
    )
    provider: Mapped[str] = mapped_column(String(30), primary_key=True)
    account_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    connection_id: Mapped[int] = mapped_column(Integer, nullable=False)
    tokens: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
