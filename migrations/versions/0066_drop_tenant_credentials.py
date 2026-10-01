"""drop tenant_credentials (workspace-scoped secrets removed)

Personal secrets (VK L2 user_token, Telegram MTProto parts) moved to
`user_credentials` keyed by `users.id` in 0065. What remained in
`tenant_credentials` (app_id, client_secret, service_token, bot_token) is
application/infrastructure config now read from the environment, so there is
nothing left to store per workspace. The table is dropped; the downgrade
recreates it with the same shape.

Revision ID: 0066
Revises: 0065
Create Date: 2026-10-01

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0066"
down_revision: Union[str, Sequence[str], None] = "0065"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "public"


def upgrade() -> None:
    # Permissions and the model_types row reference this table via FKs without
    # CASCADE, and role_permission references the permissions — drop in order so
    # register_model_types (which deletes the model_types row afterwards) does
    # not hit an FK violation.
    op.execute(
        sa.text(
            f"DELETE FROM {SCHEMA}.role_permission WHERE permission_id IN ("
            f"SELECT p.id FROM {SCHEMA}.permissions p "
            f"JOIN {SCHEMA}.model_types m ON p.model_type_id = m.id "
            f"WHERE m.table_name = 'tenant_credentials')"
        )
    )
    op.execute(
        sa.text(
            f"DELETE FROM {SCHEMA}.permissions WHERE model_type_id IN "
            f"(SELECT id FROM {SCHEMA}.model_types WHERE table_name = 'tenant_credentials')"
        )
    )
    op.execute(sa.text(f"DELETE FROM {SCHEMA}.model_types WHERE table_name = 'tenant_credentials'"))
    op.drop_index("ix_tenant_credentials_tenant_id", table_name="tenant_credentials", schema=SCHEMA)
    op.drop_table("tenant_credentials", schema=SCHEMA)


def downgrade() -> None:
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
