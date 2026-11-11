"""Workspace-scoped approvals shared by ERP and other outbound connectors.

An approval does not permit private-address access or replace the installation
registry: each connector must still enforce its own network policy at use time.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.models.base import Base, TenantScopedMixin
from app.models.integrations.trusted_outbound_host import HOST_PATTERN


class WorkspaceOutboundHost(Base, TenantScopedMixin):
    __tablename__ = "workspace_outbound_hosts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "host", name="uq_workspace_outbound_hosts_tenant_host"),
        CheckConstraint(f"host ~ '{HOST_PATTERN}'", name="ck_workspace_outbound_hosts_host"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    host: Mapped[str] = mapped_column(String(253), nullable=False)
    approved_by_user_id: Mapped[int] = mapped_column(
        ForeignKey(f"{settings.DB_SCHEMA}.users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
