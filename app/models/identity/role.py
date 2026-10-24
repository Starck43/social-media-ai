"""Platform-only fixed role identities."""

from typing import ClassVar

from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...core.access_catalog import role_label
from ...core.config import settings
from ..base import Base, TimestampMixin

role_permission = Table(
    "role_permission",
    Base.metadata,
    Column("role_id", Integer, ForeignKey(f"{settings.DB_SCHEMA}.roles.id"), primary_key=True),
    Column("permission_id", Integer, ForeignKey(f"{settings.DB_SCHEMA}.permissions.id"), primary_key=True),
    schema=settings.DB_SCHEMA,
)


class PlatformRole(Base, TimestampMixin):
    __tablename__ = "roles"
    __table_args__ = (
        CheckConstraint("codename IN ('user', 'operator', 'admin', 'superuser')", name="ck_platform_role_codename"),
        {"schema": settings.DB_SCHEMA},
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    codename: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    users = relationship("User", back_populates="role")
    permissions = relationship("Permission", secondary=role_permission, backref="roles")
    objects: ClassVar = None

    @property
    def label(self):
        return role_label(self.codename)

    def __str__(self):
        return self.label


Role = PlatformRole  # One-release Python import compatibility; not another mapper.
