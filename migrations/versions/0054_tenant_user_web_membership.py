"""bind web users to workspaces (tenant_users.user_id)

The client UI (docs/design/ui.md) logs people in through the `users` table,
while `tenant_users` membership was keyed on messenger identities only
(channel + external_user_id). Web membership reuses the same table:
channel='web' rows carry a non-null `user_id` FK, so one member query covers
both surfaces and invites stay a single code base.

Revision ID: 0054
Revises: 0053
Create Date: 2026-09-25 22:10:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0054"
down_revision: Union[str, Sequence[str], None] = "0053"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "social_manager"


def upgrade() -> None:
    op.add_column(
        "tenant_users",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_tenant_users_user_id",
        "tenant_users",
        ["user_id"],
        schema=SCHEMA,
    )
    # One web membership per (tenant, user); messenger rows keep user_id NULL
    # and NULLs do not collide in a Postgres unique index.
    op.create_index(
        "uq_tenant_users_web_membership",
        "tenant_users",
        ["tenant_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("uq_tenant_users_web_membership", table_name="tenant_users", schema=SCHEMA)
    op.drop_index("ix_tenant_users_user_id", table_name="tenant_users", schema=SCHEMA)
    op.drop_column("tenant_users", "user_id", schema=SCHEMA)
