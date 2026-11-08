"""P16-T05 opt-in and durable once-per-contact/event/year ledger.

Revision ID: 0145
Revises: 0144
Deleting a contact leaves its event ledger orphaned with contact_id NULL; the
planner task remains with its contact link cleared under the existing planner FK. Deleting a generated task
clears its ledger reference, retaining idempotency. Deleting the enabling user
sets the settings owner to NULL, disabling generation without reassignment.
Downgrade refuses to discard settings or run identities.
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0145"
down_revision = "0144"
branch_labels = None
depends_on = None


def upgrade():
    s = settings.DB_SCHEMA
    op.create_table(
        "planner_event_settings",
        sa.Column("tenant_id", sa.Integer, sa.ForeignKey(f"{s}.tenants.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("owner_user_id", sa.Integer, sa.ForeignKey(f"{s}.users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        schema=s,
    )
    op.create_table(
        "planner_event_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("tenant_id", sa.Integer, sa.ForeignKey(f"{s}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contact_id", sa.Integer, sa.ForeignKey(f"{s}.contacts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("event_type", sa.String(20), nullable=False),
        sa.Column("year", sa.Integer, nullable=False),
        sa.Column("root_task_id", sa.Integer, sa.ForeignKey(f"{s}.planner_tasks.id", ondelete="SET NULL")),
        sa.Column("greeting_task_id", sa.Integer, sa.ForeignKey(f"{s}.planner_tasks.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        # Keep live event keys unique, but allow multiple anonymous orphans.
        sa.UniqueConstraint(
            "tenant_id",
            "contact_id",
            "event_type",
            "year",
            name="uq_planner_event_year",
        ),
        sa.UniqueConstraint("tenant_id", "greeting_task_id", name="uq_planner_event_greeting_task"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_planner_event_tenant_id"),
        sa.UniqueConstraint("tenant_id", "id", "event_type", "year", name="uq_planner_event_claim_target"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contact_id"],
            [f"{s}.contacts.tenant_id", f"{s}.contacts.id"],
            deferrable=True,
            initially="DEFERRED",
            name="fk_planner_event_contact",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "root_task_id"],
            [f"{s}.planner_tasks.tenant_id", f"{s}.planner_tasks.id"],
            deferrable=True,
            initially="DEFERRED",
            name="fk_planner_event_root_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "greeting_task_id"],
            [f"{s}.planner_tasks.tenant_id", f"{s}.planner_tasks.id"],
            deferrable=True,
            initially="DEFERRED",
            name="fk_planner_event_greeting_tenant",
        ),
        sa.CheckConstraint("event_type IN ('birthday', 'anniversary')", name="ck_planner_event_type"),
        sa.CheckConstraint("year >= 1970 AND year <= 2100", name="ck_planner_event_year"),
        schema=s,
    )

    op.create_table(
        "planner_event_claims",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("tenant_id", sa.Integer, sa.ForeignKey(f"{s}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("contact_id", sa.Integer, nullable=False),
        sa.Column("run_id", sa.Integer, nullable=False),
        # Set on the copy a merge creates for the survivor: the contact whose
        # closed event this claim now stands for until an unmerge hands it back.
        sa.Column("originated_contact_id", sa.Integer, nullable=True),
        sa.Column("event_type", sa.String(20), nullable=False),
        sa.Column("year", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("tenant_id", "contact_id", "event_type", "year", name="uq_planner_event_claim"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "contact_id"], [f"{s}.contacts.tenant_id", f"{s}.contacts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id", "event_type", "year"],
            [
                f"{s}.planner_event_runs.tenant_id",
                f"{s}.planner_event_runs.id",
                f"{s}.planner_event_runs.event_type",
                f"{s}.planner_event_runs.year",
            ],
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
            name="fk_planner_event_claim_run",
        ),
        sa.CheckConstraint("event_type IN ('birthday', 'anniversary')", name="ck_planner_event_claim_type"),
        sa.CheckConstraint("year >= 1970 AND year <= 2100", name="ck_planner_event_claim_year"),
        schema=s,
    )
    op.create_table(
        "planner_event_groups",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("tenant_id", sa.Integer, sa.ForeignKey(f"{s}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.Integer, nullable=False),
        # The contact whose closed event this group was before the merge moved it.
        sa.Column("origin_contact_id", sa.Integer, nullable=False),
        # The closed key, so a contact that closed the same key twice is separated.
        sa.Column("event_type", sa.String(20), nullable=False),
        sa.Column("year", sa.Integer, nullable=False),
        # The run that held these tasks before the merge moved them here; an
        # unmerge puts them back rather than inferring current ownership.
        sa.Column("origin_run_id", sa.Integer, nullable=True),
        sa.Column("root_task_id", sa.Integer, sa.ForeignKey(f"{s}.planner_tasks.id", ondelete="SET NULL")),
        sa.Column("greeting_task_id", sa.Integer, sa.ForeignKey(f"{s}.planner_tasks.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "origin_contact_id", "event_type", "year", name="uq_planner_event_group_origin"
        ),
        sa.CheckConstraint("event_type IN ('birthday', 'anniversary')", name="ck_planner_event_group_type"),
        sa.CheckConstraint("year >= 1970 AND year <= 2100", name="ck_planner_event_group_year"),
        sa.UniqueConstraint("tenant_id", "greeting_task_id", name="uq_planner_event_group_greeting"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            [f"{s}.planner_event_runs.tenant_id", f"{s}.planner_event_runs.id"],
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
            name="fk_planner_event_group_run",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "root_task_id"],
            [f"{s}.planner_tasks.tenant_id", f"{s}.planner_tasks.id"],
            deferrable=True,
            initially="DEFERRED",
            name="fk_planner_event_group_root_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "greeting_task_id"],
            [f"{s}.planner_tasks.tenant_id", f"{s}.planner_tasks.id"],
            deferrable=True,
            initially="DEFERRED",
            name="fk_planner_event_group_greeting_tenant",
        ),
        schema=s,
    )


def downgrade():
    bind = op.get_bind()
    q = bind.dialect.identifier_preparer.quote_schema
    schema = q(settings.DB_SCHEMA)
    runs, config = f'{schema}.{q("planner_event_runs")}', f'{schema}.{q("planner_event_settings")}'
    # Keep both write-blocked until the enclosing Alembic transaction completes.
    bind.execute(sa.text(f"LOCK TABLE {runs}, {config} IN ACCESS EXCLUSIVE MODE"))
    if bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {runs}) OR EXISTS (SELECT 1 FROM {config})")).scalar():
        raise RuntimeError("Downgrade would discard event-planning settings or run identities")
    op.drop_table("planner_event_groups", schema=settings.DB_SCHEMA)
    op.drop_table("planner_event_claims", schema=settings.DB_SCHEMA)
    op.drop_table("planner_event_runs", schema=settings.DB_SCHEMA)
    op.drop_table("planner_event_settings", schema=settings.DB_SCHEMA)
