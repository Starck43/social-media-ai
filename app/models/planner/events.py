"""Private per-workspace event-planning configuration and durable run identities."""

from sqlalchemy import JSON, CheckConstraint, ForeignKey, ForeignKeyConstraint, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.models.base import Base, TenantScopedMixin, TimestampMixin

S = settings.DB_SCHEMA


class PlannerEventSettings(Base, TimestampMixin):
    __tablename__ = "planner_event_settings"
    __table_args__ = ({"schema": S},)

    tenant_id: Mapped[int] = mapped_column(ForeignKey(f"{S}.tenants.id", ondelete="CASCADE"), primary_key=True)
    # NULL is disabled. Erasing the actor disables rather than transferring ownership.
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey(f"{S}.users.id", ondelete="SET NULL"))


class PlannerEventRun(Base, TenantScopedMixin, TimestampMixin):
    __tablename__ = "planner_event_runs"
    __table_args__ = (
        # A non-NULL contact is unique per event/year. Orphans remain separate
        # so erasing a second contact cannot be blocked by a prior erasure.
        UniqueConstraint(
            "tenant_id",
            "contact_id",
            "event_type",
            "year",
            name="uq_planner_event_year",
        ),
        UniqueConstraint("tenant_id", "greeting_task_id", name="uq_planner_event_greeting_task"),
        UniqueConstraint("tenant_id", "id", name="uq_planner_event_tenant_id"),
        UniqueConstraint("tenant_id", "id", "event_type", "year", name="uq_planner_event_claim_target"),
        ForeignKeyConstraint(
            ["tenant_id", "contact_id"],
            [f"{S}.contacts.tenant_id", f"{S}.contacts.id"],
            deferrable=True,
            initially="DEFERRED",
            name="fk_planner_event_contact",
        ),
        # Global single-column SET NULL keeps the ledger when a task is deleted;
        # deferred composite FKs also reject foreign-workspace references.
        ForeignKeyConstraint(
            ["tenant_id", "root_task_id"],
            [f"{S}.planner_tasks.tenant_id", f"{S}.planner_tasks.id"],
            deferrable=True,
            initially="DEFERRED",
            name="fk_planner_event_root_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "greeting_task_id"],
            [f"{S}.planner_tasks.tenant_id", f"{S}.planner_tasks.id"],
            deferrable=True,
            initially="DEFERRED",
            name="fk_planner_event_greeting_tenant",
        ),
        CheckConstraint("event_type IN ('birthday', 'anniversary')", name="ck_planner_event_type"),
        CheckConstraint("year >= 1970 AND year <= 2100", name="ck_planner_event_year"),
        {"schema": S},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # NULL marks an orphaned ledger row after contact deletion. The global FK
    # clears only contact_id; the deferred composite FK enforces tenant isolation.
    contact_id: Mapped[int | None] = mapped_column(ForeignKey(f"{S}.contacts.id", ondelete="SET NULL"))
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    root_task_id: Mapped[int | None] = mapped_column(ForeignKey(f"{S}.planner_tasks.id", ondelete="SET NULL"))
    greeting_task_id: Mapped[int | None] = mapped_column(ForeignKey(f"{S}.planner_tasks.id", ondelete="SET NULL"))


class PlannerEventClaim(Base, TenantScopedMixin, TimestampMixin):
    """A contact's closed event key; retained across merge and unmerge."""

    __tablename__ = "planner_event_claims"
    __table_args__ = (
        UniqueConstraint("tenant_id", "contact_id", "event_type", "year", name="uq_planner_event_claim"),
        ForeignKeyConstraint(
            ["tenant_id", "contact_id"], [f"{S}.contacts.tenant_id", f"{S}.contacts.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "run_id", "event_type", "year"],
            [
                f"{S}.planner_event_runs.tenant_id",
                f"{S}.planner_event_runs.id",
                f"{S}.planner_event_runs.event_type",
                f"{S}.planner_event_runs.year",
            ],
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
            name="fk_planner_event_claim_run",
        ),
        CheckConstraint("event_type IN ('birthday', 'anniversary')", name="ck_planner_event_claim_type"),
        CheckConstraint("year >= 1970 AND year <= 2100", name="ck_planner_event_claim_year"),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact_id: Mapped[int] = mapped_column(Integer, nullable=False)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # Set on the copy a merge creates for the survivor: the contact whose closed
    # event this claim now stands for, until an unmerge hands it back.
    originated_contact_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)


class PlannerEventGroup(Base, TenantScopedMixin, TimestampMixin):
    """A task group recorded when a contact's closed event moved to a survivor."""

    __tablename__ = "planner_event_groups"
    __table_args__ = (
        # One recorded group per merged contact and event key: an unmerge finds
        # exactly the tasks and the run that contact brought.
        UniqueConstraint("tenant_id", "origin_contact_id", "event_type", "year", name="uq_planner_event_group_origin"),
        UniqueConstraint("tenant_id", "greeting_task_id", name="uq_planner_event_group_greeting"),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            [f"{S}.planner_event_runs.tenant_id", f"{S}.planner_event_runs.id"],
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
            name="fk_planner_event_group_run",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "root_task_id"],
            [f"{S}.planner_tasks.tenant_id", f"{S}.planner_tasks.id"],
            deferrable=True,
            initially="DEFERRED",
            name="fk_planner_event_group_root_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "greeting_task_id"],
            [f"{S}.planner_tasks.tenant_id", f"{S}.planner_tasks.id"],
            deferrable=True,
            initially="DEFERRED",
            name="fk_planner_event_group_greeting_tenant",
        ),
        CheckConstraint("event_type IN ('birthday', 'anniversary')", name="ck_planner_event_group_type"),
        CheckConstraint("year >= 1970 AND year <= 2100", name="ck_planner_event_group_year"),
        {"schema": S},
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # The contact whose closed event this group was before the merge moved it.
    origin_contact_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # The closed key, so a contact that closed the same key twice is separated.
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    # The run that held these tasks before the merge moved them here; an unmerge
    # puts them back rather than inferring ownership from the current rows.
    origin_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    root_task_id: Mapped[int | None] = mapped_column(ForeignKey(f"{S}.planner_tasks.id", ondelete="SET NULL"))
    greeting_task_id: Mapped[int | None] = mapped_column(ForeignKey(f"{S}.planner_tasks.id", ondelete="SET NULL"))
