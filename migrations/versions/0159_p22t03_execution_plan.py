"""Store revisioned canonical task execution plans."""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0159"
down_revision = "0158"
branch_labels = None
depends_on = None


def upgrade() -> None:
    s = settings.DB_SCHEMA
    op.create_unique_constraint("uq_source_tenant_id", "sources", ["tenant_id", "id"], schema=s)
    op.create_table(
        "task_plan_revisions",
        sa.Column("tenant_id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("scheduled_principal_id", sa.Integer(), sa.ForeignKey(f"{s}.users.id", ondelete="SET NULL")),
        sa.Column("definition_encrypted", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"], [f"{s}.tasks.tenant_id", f"{s}.tasks.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint("revision > 0", name="ck_task_plan_revision_positive"),
        schema=s,
    )
    op.create_table(
        "task_bindings",
        sa.Column("tenant_id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(64), primary_key=True),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey(f"{s}.sources.id", ondelete="SET NULL")),
        sa.Column("connection_id", sa.Integer(), sa.ForeignKey(f"{s}.workspace_connections.id", ondelete="SET NULL")),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id", "revision"],
            [
                f"{s}.task_plan_revisions.tenant_id",
                f"{s}.task_plan_revisions.task_id",
                f"{s}.task_plan_revisions.revision",
            ],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            [f"{s}.sources.tenant_id", f"{s}.sources.id"],
            name="fk_task_binding_source_scope",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            [f"{s}.workspace_connections.tenant_id", f"{s}.workspace_connections.id"],
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint(
            "(kind = 'social_source' AND connection_id IS NULL) OR (kind = 'mail_folder' AND source_id IS NULL)",
            name="ck_task_binding_target_kind",
        ),
        schema=s,
    )
    op.create_index("ix_task_binding_source", "task_bindings", ["source_id"], schema=s)
    op.create_index("ix_task_binding_connection", "task_bindings", ["connection_id"], schema=s)


def downgrade() -> None:
    s = settings.DB_SCHEMA
    op.execute(sa.text(f"""DO $$ BEGIN
            LOCK TABLE "{s}".task_plan_revisions IN ACCESS EXCLUSIVE MODE;
            IF EXISTS (SELECT 1 FROM "{s}".task_plan_revisions) THEN
                RAISE EXCEPTION 'Cannot downgrade while task plan revisions exist';
            END IF;
            END $$"""))
    op.drop_table("task_bindings", schema=s)
    op.drop_table("task_plan_revisions", schema=s)
    op.drop_constraint("uq_source_tenant_id", "sources", schema=s, type_="unique")
