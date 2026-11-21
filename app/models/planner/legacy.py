"""Durable legacy identity, link and reconciliation records for P22-T06."""

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantScopedMixin, TimestampMixin

from .task import S


class LegacyTaskId(Base, TenantScopedMixin, TimestampMixin):
    """One tenant-scoped legacy identity resolved to one canonical Task."""

    __tablename__ = "legacy_task_ids"
    __table_args__ = (
        UniqueConstraint("tenant_id", "legacy_namespace", "legacy_id", name="uq_legacy_task_identity"),
        ForeignKeyConstraint(["tenant_id", "task_id"], [f"{S}.tasks.tenant_id", f"{S}.tasks.id"], ondelete="CASCADE"),
        CheckConstraint("legacy_id > 0", name="ck_legacy_task_id_positive"),
        CheckConstraint("length(legacy_namespace) BETWEEN 1 AND 64", name="ck_legacy_task_namespace"),
        CheckConstraint(
            "resolution IN ('mapped','unresolved','restricted') AND "
            "((resolution = 'mapped' AND task_id IS NOT NULL) OR "
            "(resolution IN ('unresolved','restricted') AND task_id IS NULL))",
            name="ck_legacy_task_resolution",
        ),
        Index("ix_legacy_task_canonical", "tenant_id", "task_id"),
        {"schema": S},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int]
    legacy_namespace: Mapped[str] = mapped_column(String(64), nullable=False)
    legacy_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_id: Mapped[int | None] = mapped_column(Integer)
    source_fingerprint: Mapped[str | None] = mapped_column(String(64))
    access_fingerprint: Mapped[str | None] = mapped_column(String(64))
    source_updated_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[str] = mapped_column(String(24), nullable=False, default="mapped", server_default="mapped")
    resolution_reason: Mapped[str | None] = mapped_column(String(120))
    details: Mapped[dict | None] = mapped_column(JSON)


class TaskLink(Base, TenantScopedMixin, TimestampMixin):
    """Explicit relationship between canonical tasks, including execution-for."""

    __tablename__ = "task_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "from_task_id"], [f"{S}.tasks.tenant_id", f"{S}.tasks.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "to_task_id"], [f"{S}.tasks.tenant_id", f"{S}.tasks.id"], ondelete="CASCADE"
        ),
        CheckConstraint("from_task_id <> to_task_id", name="ck_task_link_distinct_tasks"),
        CheckConstraint("relation IN ('execution_for','related')", name="ck_task_link_relation"),
        Index("ix_task_link_target", "tenant_id", "to_task_id", "relation"),
        {"schema": S},
    )

    tenant_id: Mapped[int] = mapped_column(primary_key=True)
    from_task_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    relation: Mapped[str] = mapped_column(String(24), primary_key=True)
    to_task_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    legacy_namespace: Mapped[str | None] = mapped_column(String(64))
    legacy_id: Mapped[int | None] = mapped_column(BigInteger)
    details: Mapped[dict | None] = mapped_column(JSON)


class TaskSourceLink(Base, TenantScopedMixin, TimestampMixin):
    """Typed provenance link from a canonical task to an external source object."""

    __tablename__ = "task_source_links"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "task_id"], [f"{S}.tasks.tenant_id", f"{S}.tasks.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(
            ["tenant_id", "source_object_id"],
            [f"{S}.source_objects.tenant_id", f"{S}.source_objects.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "task_id", "source_object_id", "relation", name="uq_task_source_link_identity"),
        CheckConstraint("length(relation) BETWEEN 1 AND 48", name="ck_task_source_link_relation"),
        Index("ix_task_source_link_source", "tenant_id", "source_object_id", "relation"),
        {"schema": S},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_object_id: Mapped[int] = mapped_column(Integer, nullable=False)
    relation: Mapped[str] = mapped_column(String(48), nullable=False)
    details: Mapped[dict | None] = mapped_column(JSON)


class TaskBackfillRun(Base, TenantScopedMixin, TimestampMixin):
    """Checkpoint and aggregate evidence for one workspace backfill."""

    __tablename__ = "task_backfill_runs"
    __table_args__ = (
        CheckConstraint("status IN ('running','paused','completed','failed')", name="ck_task_backfill_status"),
        UniqueConstraint("tenant_id", "id", name="uq_task_backfill_run_tenant"),
        Index("ix_task_backfill_active", "tenant_id", "status"),
        {"schema": S},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    watermark: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    planner_cursor: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    agent_cursor: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    replay_planner_cursor: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    replay_agent_cursor: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    replay_until: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running", server_default="running")
    counts: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict, server_default="{}")
    error: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))


class TaskBackfillRecord(Base, TenantScopedMixin, TimestampMixin):
    """Per-legacy-row reconciliation evidence retained after a retry."""

    __tablename__ = "task_backfill_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            [f"{S}.task_backfill_runs.tenant_id", f"{S}.task_backfill_runs.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(["tenant_id", "task_id"], [f"{S}.tasks.tenant_id", f"{S}.tasks.id"]),
        CheckConstraint(
            "outcome IN ('mapped','replayed','restricted','unresolved','skipped')", name="ck_task_backfill_outcome"
        ),
        UniqueConstraint("tenant_id", "run_id", "legacy_namespace", "legacy_id", name="uq_task_backfill_record"),
        Index("ix_task_backfill_record_resolution", "tenant_id", "outcome"),
        {"schema": S},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    legacy_namespace: Mapped[str] = mapped_column(String(64), nullable=False)
    legacy_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_id: Mapped[int | None] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    source_fingerprint: Mapped[str | None] = mapped_column(String(64))
    access_fingerprint: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(String(160))
    details: Mapped[dict | None] = mapped_column(JSON)
