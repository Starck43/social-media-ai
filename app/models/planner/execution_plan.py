"""Append-only execution plan revisions and typed target references."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantScopedMixin
from app.models.planner.task import S


class TaskPlanRevision(Base, TenantScopedMixin):
    __tablename__ = "task_plan_revisions"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "task_id"], [f"{S}.tasks.tenant_id", f"{S}.tasks.id"], ondelete="CASCADE"),
        CheckConstraint("revision > 0", name="ck_task_plan_revision_positive"),
        {"schema": S},
    )

    tenant_id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(primary_key=True)
    revision: Mapped[int] = mapped_column(primary_key=True)
    definition_encrypted: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# Deleted targets leave an empty FK and a historical encrypted definition.
# Resolution requires both the original identity and its surviving typed link.
BINDING_TARGET_CHECK = (
    "(kind = 'social_source' AND connection_id IS NULL) OR (kind = 'mail_folder' AND source_id IS NULL)"
)


class TaskPlanBinding(Base, TenantScopedMixin):
    __tablename__ = "task_bindings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "task_id", "revision"],
            [
                f"{S}.task_plan_revisions.tenant_id",
                f"{S}.task_plan_revisions.task_id",
                f"{S}.task_plan_revisions.revision",
            ],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            [f"{S}.sources.tenant_id", f"{S}.sources.id"],
            name="fk_task_binding_source_scope",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            [f"{S}.workspace_connections.tenant_id", f"{S}.workspace_connections.id"],
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(BINDING_TARGET_CHECK, name="ck_task_binding_target_kind"),
        Index("ix_task_binding_source", "source_id"),
        Index("ix_task_binding_connection", "connection_id"),
        {"schema": S},
    )

    tenant_id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(primary_key=True)
    revision: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(24))
    source_id: Mapped[int | None] = mapped_column(ForeignKey(f"{S}.sources.id", ondelete="SET NULL"))
    connection_id: Mapped[int | None] = mapped_column(ForeignKey(f"{S}.workspace_connections.id", ondelete="SET NULL"))
