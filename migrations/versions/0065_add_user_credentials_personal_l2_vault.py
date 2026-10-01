"""add user_credentials (personal L2 vault)

Secrets that belong to a *person* (VK L2 user_token, Telegram MTProto session)
move here from the workspace-scoped `tenant_credentials`. The table is bound to
`users.id`, not `tenants.id`, because one person may own several workspaces and
their tokens must live once, shared across those workspaces — not duplicated per
tenant. Access control (a workspace may only use a token of a user who is an
active member) is enforced at the call site, not here.

Revision ID: 0065
Revises: 0064
Create Date: 2026-10-01

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0065"
down_revision: Union[str, Sequence[str], None] = "0064"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "public"


def upgrade() -> None:
    op.create_table(
        "user_credentials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(30), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("label", sa.String(100), nullable=True),
        sa.Column("secret_encrypted", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], [f"{SCHEMA}.users.id"], ondelete="CASCADE"),
        schema=SCHEMA,
    )
    op.create_index("ix_user_credentials_user_id", "user_credentials", ["user_id"], schema=SCHEMA)
    op.create_index("ix_user_credentials_user_platform", "user_credentials", ["user_id", "platform"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_user_credentials_user_platform", table_name="user_credentials", schema=SCHEMA)
    op.drop_index("ix_user_credentials_user_id", table_name="user_credentials", schema=SCHEMA)
    op.drop_table("user_credentials", schema=SCHEMA)
