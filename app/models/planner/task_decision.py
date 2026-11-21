"""Local proposals and references to independently owned review ledgers."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantScopedMixin
from app.models.planner.task import S
from app.models.types.encrypted import EncryptedText


class DecisionText(EncryptedText):
    impl = Text
    cache_ok = True


def _task_fk():
    return ForeignKeyConstraint(["tenant_id", "task_id"], [f"{S}.tasks.tenant_id", f"{S}.tasks.id"], ondelete="CASCADE")


class TaskDecision(Base, TenantScopedMixin):
    __tablename__ = "task_decisions"
    __table_args__ = (
        _task_fk(),
        CheckConstraint(
            "(outcome IS NULL AND decided_at IS NULL) OR "
            "(outcome IS NOT NULL AND outcome IN ('accepted','rejected') AND decided_at IS NOT NULL)",
            name="ck_task_decision_outcome",
        ),
        Index("ix_task_decision_reviewer", "tenant_id", "task_id", "reviewer_id"),
        {"schema": S},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int]
    task_version: Mapped[int]
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey(f"{S}.users.id", ondelete="SET NULL"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(DecisionText())
    outcome: Mapped[str | None] = mapped_column(String(16))
    outcome_reason: Mapped[str | None] = mapped_column(DecisionText())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TaskDecisionLink(Base, TenantScopedMixin):
    __tablename__ = "task_decision_links"
    __table_args__ = (
        _task_fk(),
        CheckConstraint("source IN ('command','document')", name="ck_task_decision_link_source"),
        {"schema": S},
    )

    tenant_id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(16), primary_key=True)
    source_id: Mapped[int] = mapped_column(primary_key=True)
