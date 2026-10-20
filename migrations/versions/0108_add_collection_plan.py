"""Add the task-owned collection plan contract.

`collected_items` gains typed identity (`content_type`), a parent link for
comments, lane provenance and the selection outcome. The unique key widens to
(source_id, content_type, external_id) so a comment cannot collide with its
parent post. `sources.owner_task_id` marks a task-scoped query lane.

New evidence tables:
- `collection_seen` — dedupe/new-item ledger (no text);
- `collection_candidates` — discovered people/communities and engagement actors;
- `collection_lane_runs` — per-job, per-lane counts and bounds.

Existing tasks keep `content_types=[\"posts\"]` semantics: the migration
backfills `content_type='post'` and does not change collection or analysis.

Downgrade restores the old unique key. If comment rows already exist, restoring
the narrower key can fail on a post/comment identity collision; that is
intentional and must be resolved before a downgrade, not hidden by dropping
rows.

Revision ID: 0108
Revises: 0107
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0108"
down_revision = "0107"
branch_labels = None
depends_on = None

SCHEMA = settings.DB_SCHEMA
TABLE = "collected_items"
OLD_UNIQUE = "uq_collected_items_source_external"
NEW_UNIQUE = "uq_collected_items_source_content_external"


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column("content_type", sa.String(20), nullable=False, server_default="post"),
        schema=SCHEMA,
    )
    op.add_column(TABLE, sa.Column("parent_external_id", sa.String(255), nullable=True), schema=SCHEMA)
    op.add_column(TABLE, sa.Column("lane_id", sa.String(100), nullable=True), schema=SCHEMA)
    op.add_column(TABLE, sa.Column("selection_status", sa.String(20), nullable=True), schema=SCHEMA)
    op.add_column(TABLE, sa.Column("selection_reason", sa.String(64), nullable=True), schema=SCHEMA)

    op.drop_index(OLD_UNIQUE, table_name=TABLE, schema=SCHEMA)
    op.create_index(
        NEW_UNIQUE,
        TABLE,
        ["source_id", "content_type", "external_id"],
        unique=True,
        schema=SCHEMA,
    )
    op.create_index(
        "ix_collected_items_tenant_selection",
        TABLE,
        ["tenant_id", "selection_status"],
        schema=SCHEMA,
    )

    op.add_column("sources", sa.Column("owner_task_id", sa.Integer(), nullable=True), schema=SCHEMA)
    op.create_foreign_key(
        "fk_sources_owner_task_id",
        "sources",
        "agent_tasks",
        ["owner_task_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="CASCADE",
    )
    op.create_index("ix_sources_owner_task_id", "sources", ["owner_task_id"], schema=SCHEMA)

    op.create_table(
        "collection_seen",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content_type", sa.String(20), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("status", sa.String(20), nullable=False, server_default="new"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint(
            "tenant_id",
            "source_id",
            "content_type",
            "external_id",
            name="uq_collection_seen_identity",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_collection_seen_tenant_source",
        "collection_seen",
        ["tenant_id", "source_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "collection_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("platform", sa.String(50), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("url", sa.String(500), nullable=True),
        sa.Column("city", sa.String(255), nullable=True),
        sa.Column("city_source", sa.String(50), nullable=True),
        sa.Column("city_confidence", sa.Float(), nullable=True),
        sa.Column("metrics", sa.JSON(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="new"),
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.sources.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("discovered_by", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint(
            "tenant_id",
            "platform",
            "kind",
            "external_id",
            name="uq_collection_candidates_identity",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_collection_candidates_tenant_status",
        "collection_candidates",
        ["tenant_id", "status"],
        schema=SCHEMA,
    )

    op.create_table(
        "collection_lane_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("lane_id", sa.String(100), nullable=False),
        sa.Column("lane_type", sa.String(30), nullable=False),
        sa.Column("query", sa.Text(), nullable=True),
        sa.Column("fetched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("selected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("needs_review", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("limit_count", sa.Integer(), nullable=True),
        sa.Column("limit_reached", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("next_cursor", sa.String(500), nullable=True),
        sa.Column("total_available", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(50), nullable=True),
        sa.Column("unsupported_reason", sa.String(255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_collection_lane_runs_tenant_job",
        "collection_lane_runs",
        ["tenant_id", "job_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_collection_lane_runs_tenant_task",
        "collection_lane_runs",
        ["tenant_id", "task_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("collection_lane_runs", schema=SCHEMA)
    op.drop_table("collection_candidates", schema=SCHEMA)
    op.drop_table("collection_seen", schema=SCHEMA)

    op.drop_index("ix_sources_owner_task_id", table_name="sources", schema=SCHEMA)
    op.drop_constraint("fk_sources_owner_task_id", "sources", schema=SCHEMA, type_="foreignkey")
    op.drop_column("sources", "owner_task_id", schema=SCHEMA)

    op.drop_index("ix_collected_items_tenant_selection", table_name=TABLE, schema=SCHEMA)
    op.drop_index(NEW_UNIQUE, table_name=TABLE, schema=SCHEMA)
    op.create_index(OLD_UNIQUE, TABLE, ["source_id", "external_id"], unique=True, schema=SCHEMA)

    for column in (
        "selection_reason",
        "selection_status",
        "lane_id",
        "parent_external_id",
        "content_type",
    ):
        op.drop_column(TABLE, column, schema=SCHEMA)
