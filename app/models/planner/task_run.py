"""Canonical task triggers and durable execution receipts."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantScopedMixin, TimestampMixin
from app.models.planner.task import S


def plan_revision_fk():
    fields = ("tenant_id", "task_id", "revision")
    return ForeignKeyConstraint(fields, [f"{S}.task_plan_revisions.{field}" for field in fields], ondelete="CASCADE")


class TaskTrigger(Base, TenantScopedMixin, TimestampMixin):
    __tablename__ = "task_triggers"
    __table_args__ = (
        plan_revision_fk(),
        UniqueConstraint("tenant_id", "task_id", name="uq_task_trigger_task"),
        UniqueConstraint("tenant_id", "task_id", "id", name="uq_task_trigger_scope"),
        CheckConstraint("enabled = (next_due_at IS NOT NULL)", name="ck_task_trigger_cursor"),
        Index("ix_task_trigger_due", "tenant_id", "next_due_at", postgresql_where=text("enabled")),
        {"schema": S},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int]
    revision: Mapped[int]
    principal_id: Mapped[int | None] = mapped_column(ForeignKey(f"{S}.users.id", ondelete="SET NULL"))
    cron_expression: Mapped[str] = mapped_column(String(100))
    timezone_name: Mapped[str] = mapped_column(String(100))
    enabled: Mapped[bool]
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TaskRun(Base, TenantScopedMixin, TimestampMixin):
    __tablename__ = "task_runs"
    __table_args__ = (
        plan_revision_fk(),
        ForeignKeyConstraint(
            ["tenant_id", "task_id", "trigger_id"],
            [f"{S}.task_triggers.{key}" for key in ("tenant_id", "task_id", "id")],
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_task_run_scope"),
        UniqueConstraint("tenant_id", "trigger_id", "occurrence_at", name="uq_task_run_occurrence"),
        UniqueConstraint("tenant_id", "manual_actor_id", "request_key", name="uq_task_run_manual_intent"),
        UniqueConstraint("job_id", name="uq_task_run_initial_job"),
        CheckConstraint(
            "(trigger_id IS NULL AND occurrence_at IS NULL AND manual_actor_id IS NOT NULL AND request_key IS NOT NULL)"
            " OR (trigger_id IS NOT NULL AND occurrence_at IS NOT NULL AND manual_actor_id IS NULL AND request_key IS NULL)",
            name="ck_task_run_origin",
        ),
        CheckConstraint(
            "status IN ('pending','running','done','failed','cancelled','unknown','skipped')",
            name="ck_task_run_status",
        ),
        Index(
            "uq_task_run_active",
            "tenant_id",
            "task_id",
            unique=True,
            postgresql_where=text("status IN ('pending','running')"),
        ),
        {"schema": S},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int]
    revision: Mapped[int]
    trigger_id: Mapped[int | None]
    occurrence_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Retain the numeric actor and initial Job identities even if those rows
    # are later purged. The separately referenced principal controls access.
    manual_actor_id: Mapped[int | None]
    request_key: Mapped[str | None] = mapped_column(String(64))
    fingerprint: Mapped[str] = mapped_column(String(64))
    principal_id: Mapped[int | None] = mapped_column(ForeignKey(f"{S}.users.id", ondelete="SET NULL"))
    payer_user_id: Mapped[int | None] = mapped_column(ForeignKey(f"{S}.users.id", ondelete="SET NULL"))
    job_id: Mapped[int | None]
    status: Mapped[str] = mapped_column(String(16))
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    admitted_inputs_encrypted: Mapped[str] = mapped_column(Text)
    checkpoint_encrypted: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
