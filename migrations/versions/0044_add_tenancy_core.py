"""add tenancy core: tenants, membership, invites, channels, credentials

Adds the multi-tenant core and backfills every business table with a
`tenant_id`, pointing at a bootstrap tenant (slug "owner") so the existing
single-workspace data keeps working.

Downgrade drops the tenancy tables and the tenant columns, i.e. client
workspaces lose their rows — run it only on an owner-only database.

Revision ID: 0044
Revises: 0043
Create Date: 2026-09-23

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0044"
down_revision: Union[str, Sequence[str], None] = "0043"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "social_manager"
BOOTSTRAP_SLUG = "owner"

# Business tables that become tenant-owned (see TenantScopedMixin).
SCOPED_TABLES = (
    "sources",
    "bot_scenarios",
    "notifications",
    "ai_analytics",
    "schedules",
    "jobs",
    "digest_runs",
    "agent_sessions",
    "agent_messages",
    "agent_memory",
)


def _bootstrap_tenant_id() -> int:
    """Id of the owner workspace, created on first run (idempotent)."""
    conn = op.get_bind()
    tenant_id = conn.execute(
        sa.text(f"SELECT id FROM {SCHEMA}.tenants WHERE slug = :slug"), {"slug": BOOTSTRAP_SLUG}
    ).scalar()
    if tenant_id is not None:
        return int(tenant_id)
    return int(
        conn.execute(
            sa.text(f"""
                INSERT INTO {SCHEMA}.tenants
                    (name, slug, plan, timezone, is_active, daily_cost_limit, max_sources,
                     created_at, updated_at)
                VALUES
                    ('Owner workspace', :slug, 'owner', 'Europe/Moscow', true, 5.0, 100,
                     now(), now())
                RETURNING id
                """),
            {"slug": BOOTSTRAP_SLUG},
        ).scalar_one()
    )


def upgrade() -> None:
    """Upgrade schema."""
    # --- Tenancy core -------------------------------------------------------
    op.create_table(
        "tenants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("slug", sa.String(50), nullable=False),
        sa.Column("plan", sa.String(30), nullable=False, server_default="personal"),
        sa.Column("timezone", sa.String(50), nullable=False, server_default="Europe/Moscow"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("daily_cost_limit", sa.Float(), nullable=False, server_default="5.0"),
        sa.Column("max_sources", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("slug", name="uq_tenant_slug"),
        schema=SCHEMA,
    )
    op.create_index("ix_tenants_slug", "tenants", ["slug"], schema=SCHEMA)

    op.create_table(
        "tenant_users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("external_user_id", sa.String(100), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="owner"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], [f"{SCHEMA}.tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "channel", "external_user_id", name="uq_tenant_user_identity"),
        schema=SCHEMA,
    )
    op.create_index("ix_tenant_users_tenant_id", "tenant_users", ["tenant_id"], schema=SCHEMA)

    op.create_table(
        "tenant_invites",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="owner"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_uses", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], [f"{SCHEMA}.tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("code_hash", name="uq_tenant_invite_code_hash"),
        schema=SCHEMA,
    )
    op.create_index("ix_tenant_invites_tenant_id", "tenant_invites", ["tenant_id"], schema=SCHEMA)

    op.create_table(
        "tenant_channels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("chat_id", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="private"),
        sa.Column("is_digest_target", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], [f"{SCHEMA}.tenants.id"], ondelete="CASCADE"),
        # A chat belongs to exactly one workspace: this is what keeps tenant A
        # from reaching tenant B's conversation when they share one bot.
        sa.UniqueConstraint("channel", "chat_id", name="uq_tenant_channel_chat"),
        schema=SCHEMA,
    )
    op.create_index("ix_tenant_channels_tenant_id", "tenant_channels", ["tenant_id"], schema=SCHEMA)

    op.create_table(
        "tenant_credentials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(30), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("label", sa.String(100), nullable=True),
        sa.Column("secret_encrypted", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], [f"{SCHEMA}.tenants.id"], ondelete="CASCADE"),
        schema=SCHEMA,
    )
    op.create_index("ix_tenant_credentials_tenant_id", "tenant_credentials", ["tenant_id"], schema=SCHEMA)

    # --- Backfill: every pre-existing row belongs to the owner workspace ----
    tenant_id = _bootstrap_tenant_id()
    for table in SCOPED_TABLES:
        op.add_column(table, sa.Column("tenant_id", sa.Integer(), nullable=True), schema=SCHEMA)
        op.execute(sa.text(f"UPDATE {SCHEMA}.{table} SET tenant_id = {tenant_id} WHERE tenant_id IS NULL"))
        op.alter_column(table, "tenant_id", nullable=False, schema=SCHEMA)
        op.create_foreign_key(
            f"{table}_tenant_id_fkey",
            table,
            "tenants",
            ["tenant_id"],
            ["id"],
            source_schema=SCHEMA,
            referent_schema=SCHEMA,
            ondelete="CASCADE",
        )
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"], schema=SCHEMA)

    # --- Per-tenant uniqueness (was global) --------------------------------
    op.drop_constraint("uq_source_platform_external", "sources", schema=SCHEMA, type_="unique")
    op.create_unique_constraint(
        "uq_source_tenant_platform_external",
        "sources",
        ["tenant_id", "platform_id", "external_id"],
        schema=SCHEMA,
    )

    op.drop_constraint("schedules_name_key", "schedules", schema=SCHEMA, type_="unique")
    op.create_unique_constraint("uq_schedule_tenant_name", "schedules", ["tenant_id", "name"], schema=SCHEMA)

    op.drop_constraint("uq_agent_memory_scope_key", "agent_memory", schema=SCHEMA, type_="unique")
    op.create_unique_constraint(
        "uq_agent_memory_tenant_scope_key", "agent_memory", ["tenant_id", "scope", "key"], schema=SCHEMA
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("uq_agent_memory_tenant_scope_key", "agent_memory", schema=SCHEMA, type_="unique")
    op.create_unique_constraint("uq_agent_memory_scope_key", "agent_memory", ["scope", "key"], schema=SCHEMA)

    op.drop_constraint("uq_schedule_tenant_name", "schedules", schema=SCHEMA, type_="unique")
    op.create_unique_constraint("schedules_name_key", "schedules", ["name"], schema=SCHEMA)

    op.drop_constraint("uq_source_tenant_platform_external", "sources", schema=SCHEMA, type_="unique")
    op.create_unique_constraint("uq_source_platform_external", "sources", ["platform_id", "external_id"], schema=SCHEMA)

    for table in reversed(SCOPED_TABLES):
        op.drop_index(f"ix_{table}_tenant_id", table_name=table, schema=SCHEMA)
        op.drop_constraint(f"{table}_tenant_id_fkey", table, schema=SCHEMA, type_="foreignkey")
        op.drop_column(table, "tenant_id", schema=SCHEMA)

    for table, index in (
        ("tenant_credentials", "ix_tenant_credentials_tenant_id"),
        ("tenant_channels", "ix_tenant_channels_tenant_id"),
        ("tenant_invites", "ix_tenant_invites_tenant_id"),
        ("tenant_users", "ix_tenant_users_tenant_id"),
    ):
        op.drop_index(index, table_name=table, schema=SCHEMA)
        op.drop_table(table, schema=SCHEMA)

    op.drop_index("ix_tenants_slug", table_name="tenants", schema=SCHEMA)
    op.drop_table("tenants", schema=SCHEMA)
