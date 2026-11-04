"""Create the local connector gateway persistence schema."""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0133"
down_revision = "0132"
branch_labels = None
depends_on = None


def _registry(add: bool) -> None:
    bind = op.get_bind()
    schema = bind.dialect.identifier_preparer.quote_schema(settings.DB_SCHEMA)
    resource_key = "integrations.local_connector"
    if add:
        bind.execute(
            sa.text(f"""
                INSERT INTO {schema}.model_types
                    (resource_key, model_name, table_name, description, is_managed, created_at, updated_at)
                VALUES (:resource_key, 'localconnector', 'local_connectors',
                        'Tenant-owned local connector device', true, now(), now())
                ON CONFLICT (resource_key) DO NOTHING
                """),
            {"resource_key": resource_key},
        )
        for action in ("VIEW", "CREATE", "DELETE", "CONFIGURE"):
            bind.execute(
                sa.text(f"""
                    INSERT INTO {schema}.permissions (name, action_type, model_type_id, created_at, updated_at)
                    SELECT :name, CAST(:action AS {schema}.action_type), id, now(), now()
                    FROM {schema}.model_types WHERE resource_key = :resource_key
                    ON CONFLICT (model_type_id, action_type) DO NOTHING
                    """),
                {"name": "Local connector: " + action.lower(), "action": action, "resource_key": resource_key},
            )
        return

    for link in ("role_permission", "workspace_role_permission"):
        bind.execute(
            sa.text(f"""
                DELETE FROM {schema}.{link} l USING {schema}.permissions p, {schema}.model_types m
                WHERE l.permission_id = p.id AND p.model_type_id = m.id AND m.resource_key = :resource_key
                """),
            {"resource_key": resource_key},
        )
    bind.execute(
        sa.text(f"""
            DELETE FROM {schema}.permissions p USING {schema}.model_types m
            WHERE p.model_type_id = m.id AND m.resource_key = :resource_key
            """),
        {"resource_key": resource_key},
    )
    bind.execute(
        sa.text(f"DELETE FROM {schema}.model_types WHERE resource_key = :resource_key"),
        {"resource_key": resource_key},
    )


def upgrade() -> None:
    schema = settings.DB_SCHEMA
    op.create_table(
        "local_connectors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("device_name_encrypted", sa.String(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("certificate_pem", sa.Text(), nullable=False),
        sa.Column("cert_serial", sa.String(length=64), nullable=False),
        sa.Column("epoch", sa.Integer(), server_default="1", nullable=False),
        sa.Column("consent_epoch", sa.Integer(), server_default="1", nullable=False),
        sa.Column("policy_epoch", sa.Integer(), server_default="1", nullable=False),
        sa.Column("cloud_roots", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["tenant_id"], [f"{schema}.tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], [f"{schema}.users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_local_connector_tenant"),
        sa.UniqueConstraint("tenant_id", "cert_serial", name="uq_local_connector_certificate"),
        sa.CheckConstraint("epoch > 0 AND consent_epoch > 0 AND policy_epoch > 0", name="ck_local_connector_epochs"),
        schema=schema,
    )
    op.create_index("ix_local_connector_owner", "local_connectors", ["tenant_id", "owner_user_id", "id"], schema=schema)

    op.create_table(
        "local_connector_pairings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("pairing_id", sa.String(length=32), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("fingerprint", sa.String(length=64)),
        sa.Column("public_key", sa.Text()),
        sa.Column("device_name_encrypted", sa.String(), nullable=False),
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("device_id", sa.Integer()),
        sa.ForeignKeyConstraint(["tenant_id"], [f"{schema}.tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], [f"{schema}.users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "device_id"],
            [f"{schema}.local_connectors.tenant_id", f"{schema}.local_connectors.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("pairing_id", name="uq_local_connector_pairing_id"),
        sa.CheckConstraint("attempts >= 0 AND attempts <= 5", name="ck_local_connector_pairing_attempts"),
        schema=schema,
    )
    op.create_index(
        "ix_local_connector_pairing_owner_created",
        "local_connector_pairings",
        ["tenant_id", "owner_user_id", "created_at"],
        schema=schema,
    )

    op.create_table(
        "local_connector_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=32), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], [f"{schema}.tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "device_id"],
            [f"{schema}.local_connectors.tenant_id", f"{schema}.local_connectors.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "device_id", "request_id", name="uq_local_connector_request"),
        sa.CheckConstraint("state IN ('dispatched','completed','unknown')", name="ck_local_connector_request_state"),
        schema=schema,
    )
    op.create_index(
        "ix_local_connector_request_expiry",
        "local_connector_requests",
        ["tenant_id", "device_id", "expires_at"],
        schema=schema,
    )
    _registry(add=True)


def downgrade() -> None:
    schema = settings.DB_SCHEMA
    bind = op.get_bind()
    for name in ("local_connector_requests", "local_connector_pairings", "local_connectors"):
        table = sa.table(name, sa.column("id", sa.Integer()), schema=schema)
        if bind.execute(sa.select(sa.literal(1)).select_from(table).limit(1)).first():
            raise ValueError("local_connector_downgrade_requires_empty_tables")
    _registry(add=False)
    op.drop_table("local_connector_requests", schema=schema)
    op.drop_table("local_connector_pairings", schema=schema)
    op.drop_table("local_connectors", schema=schema)
