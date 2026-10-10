"""Workspace membership model; role semantics are unchanged."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from sqlalchemy import Boolean, Column, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, relationship

from ...core.config import settings
from ...core.decorators import app_label
from ..base import Base, TimestampMixin

if TYPE_CHECKING:
    from ..managers.base_manager import BaseManager
    from ..managers.tenant_manager import TenantUserManager
    from ..role import Role


@app_label("account")
class TenantUser(Base, TimestampMixin):
    """Membership: a messenger identity (channel+external id) or a web user."""

    __tablename__ = "tenant_users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "channel", "external_user_id", name="uq_tenant_user_identity"),
        Index("ix_tenant_users_tenant_id", "tenant_id"),
        Index("ix_tenant_users_user_id", "user_id"),
        Index(
            "uq_tenant_users_web_membership",
            "tenant_id",
            "user_id",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        {"schema": settings.DB_SCHEMA},
    )

    id: Mapped[int] = Column(Integer, primary_key=True)
    tenant_id: Mapped[int] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.tenants.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = Column(String(20), nullable=False)  # 'telegram' | 'max' | 'web'
    external_user_id: Mapped[str] = Column(String(100), nullable=False)
    user_id: Mapped[int | None] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.users.id", ondelete="CASCADE"), nullable=True
    )
    role_id: Mapped[int | None] = Column(
        Integer, ForeignKey(f"{settings.DB_SCHEMA}.roles.id", ondelete="SET NULL"), nullable=True,
        comment="Platform role for this workspace membership (NULL = legacy/unresolved)",
    )
    is_active: Mapped[bool] = Column(Boolean, nullable=False, default=True, server_default="true")

    # Relationship to the platform role assigned to this workspace membership
    role: Mapped["Role | None"] = relationship("Role", lazy="selectin")

    if TYPE_CHECKING:
        objects: ClassVar[TenantUserManager | BaseManager]
    else:
        objects: ClassVar = None

    @property
    def is_owner(self) -> bool:
        """True when this membership's role grants full workspace access.

        The platform SUPERUSER role is the canonical owner role; legacy rows
        with ``role_id == NULL`` are treated as owners for backward compatibility
        (pre-migration data where the string column was "owner").
        """
        if self.role_id is None:
            return True
        if self.role is None:
            return False
        codename = self.role.codename
        name = codename.name if hasattr(codename, "name") else str(codename)
        return name == "SUPERUSER"
