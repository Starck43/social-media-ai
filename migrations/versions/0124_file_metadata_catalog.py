"""Private metadata catalog; no body storage, backfill or provider activation.

Revision ID: 0124
Revises: 0123
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0124"
down_revision = "0123"
branch_labels = None
depends_on = None


def _common():
    schema = settings.DB_SCHEMA
    return [
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{schema}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    ]


def upgrade():
    schema = settings.DB_SCHEMA
    op.create_unique_constraint(
        "uq_workspace_connection_tenant", "workspace_connections", ["tenant_id", "id"], schema=schema
    )
    op.create_table(
        "file_catalog_objects",
        *_common(),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("root_key", sa.String(64), nullable=False),
        sa.Column("path_key", sa.String(64), nullable=False),
        sa.Column("path", sa.String(2048), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("observation_number", sa.Integer(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            [f"{schema}.workspace_connections.tenant_id", f"{schema}.workspace_connections.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_file_catalog_object_tenant"),
        sa.UniqueConstraint("tenant_id", "connection_id", "root_key", "path_key", name="uq_file_catalog_path"),
        sa.CheckConstraint("kind IN ('file','dir')", name="ck_file_catalog_kind"),
        sa.CheckConstraint("observation_number > 0", name="ck_file_catalog_number"),
        sa.CheckConstraint("octet_length(path) <= 2048", name="ck_file_catalog_path_size"),
        sa.CheckConstraint(
            "path_key = encode(sha256(convert_to(path, 'UTF8')), 'hex')", name="ck_file_catalog_path_key"
        ),
        schema=schema,
    )
    op.create_index(
        "ix_file_catalog_page", "file_catalog_objects", ["tenant_id", "connection_id", "root_key", "id"], schema=schema
    )
    op.create_table(
        "file_catalog_observations",
        *_common(),
        sa.Column("object_id", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "object_id"],
            [f"{schema}.file_catalog_objects.tenant_id", f"{schema}.file_catalog_objects.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "object_id", "number", name="uq_file_catalog_observation"),
        sa.CheckConstraint("kind IN ('file','dir')", name="ck_file_observation_kind"),
        sa.CheckConstraint("number > 0", name="ck_file_observation_number"),
        schema=schema,
    )
    op.execute(
        f"""
        CREATE FUNCTION "{schema}".guard_file_observation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'File metadata observations are immutable'
                USING ERRCODE = '23514';
        END; $$;
        CREATE TRIGGER file_observation_immutable
            BEFORE UPDATE ON "{schema}".file_catalog_observations
            FOR EACH ROW EXECUTE FUNCTION "{schema}".guard_file_observation();
    """
    )


def downgrade():
    # Destructive only for catalog metadata, never originals or 0122 evidence.
    # Production rollback should disable consumers first and retain the ledger.
    schema = settings.DB_SCHEMA
    op.drop_table("file_catalog_observations", schema=schema)
    op.execute(f'DROP FUNCTION "{schema}".guard_file_observation()')
    op.drop_table("file_catalog_objects", schema=schema)
    op.drop_constraint("uq_workspace_connection_tenant", "workspace_connections", schema=schema, type_="unique")
