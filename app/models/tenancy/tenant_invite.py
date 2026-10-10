"""Workspace invitation model; redemption remains in the existing manager."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped

from ...core.config import settings
from ...core.decorators import app_label
from ..base import Base, TimestampMixin

if TYPE_CHECKING:
    from ..managers.base_manager import BaseManager
    from ..managers.tenant_manager import TenantInviteManager


@app_label("account")
class TenantInvite(Base, TimestampMixin):
    """Invite codes: the only way a new chat joins a tenant (`/start <code>`)."""

    __tablename__ = "tenant_invites"
    __table_args__ = (
        UniqueConstraint("code_hash", name="uq_tenant_invite_code_hash"),
        Index("ix_tenant_invites_tenant_id", "tenant_id"),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    tenant_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.tenants.id", ondelete="CASCADE"), nullable=False
    )
    code_hash: Mapped[str] = Column(String(64), nullable=False)
    role_id: Mapped[int | None] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.roles.id", ondelete="SET NULL"), nullable=True,
        comment="Platform role to assign on invite redemption",
    )
    expires_at: Mapped[datetime | None] = Column(DateTime(timezone=True), nullable=True)
    max_uses: Mapped[int] = Column(Integer, nullable=False, default=1, server_default="1")
    used_count: Mapped[int] = Column(Integer, nullable=False, default=0, server_default="0")
    is_active: Mapped[bool] = Column(Boolean, nullable=False, default=True, server_default="true")

    if TYPE_CHECKING:
        objects: ClassVar[TenantInviteManager | BaseManager]
    else:
        objects: ClassVar = None
