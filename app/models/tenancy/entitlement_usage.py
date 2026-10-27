"""Durable, append-only capability admissions; independent of job retention."""

from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped

from app.core.config import settings
from app.core.decorators import app_label
from app.models.base import Base, TenantScopedMixin


@app_label("tenancy")
class EntitlementUsage(Base, TenantScopedMixin):
    __tablename__ = "entitlement_usage"
    __table_args__ = (
        UniqueConstraint("tenant_id", "job_id", "capability", name="uq_entitlement_usage_job_capability"),
        Index("ix_entitlement_usage_daily", "tenant_id", "capability", "admitted_at"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    capability: Mapped[str] = Column(String(160), nullable=False)
    # Historical identities, deliberately not FKs: pruning a job/task/user must
    # not erase or null the audit evidence. Workspace offboarding still cascades.
    job_id: Mapped[int] = Column(Integer, nullable=False)
    task_id: Mapped[int | None] = Column(Integer, nullable=True)
    initiated_by_user_id: Mapped[int | None] = Column(Integer, nullable=True)
    plan: Mapped[str] = Column(String(20), nullable=False)
    catalog_version: Mapped[int] = Column(Integer, nullable=False)
    admitted_at: Mapped[datetime] = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
