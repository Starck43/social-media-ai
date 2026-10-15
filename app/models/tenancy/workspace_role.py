"""Workspace role catalog, independent from platform operator roles."""

from typing import ClassVar

from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...core.access_catalog import role_label
from ...core.config import settings
from ..base import Base, TimestampMixin

workspace_role_permission = Table(
    "workspace_role_permission",
    Base.metadata,
    Column(
        "role_id", Integer, ForeignKey(f"{settings.DB_SCHEMA}.workspace_roles.id", ondelete="CASCADE"), primary_key=True
    ),
    Column(
        "permission_id",
        Integer,
        ForeignKey(f"{settings.DB_SCHEMA}.permissions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    schema=settings.DB_SCHEMA,
)


class WorkspaceRole(Base, TimestampMixin):
    __tablename__ = "workspace_roles"
    __table_args__ = (
        CheckConstraint(
            "codename IN ('owner','admin','manager','analyst','viewer')", name="ck_workspace_role_codename"
        ),
        {"schema": settings.DB_SCHEMA},
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    codename: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    permissions = relationship("Permission", secondary=workspace_role_permission)
    objects: ClassVar = None

    @property
    def label(self):
        return role_label(self.codename, workspace=True)

    def __str__(self):
        return self.label
