"""Personal source ownership and the authorisation-required notification type.

`owner_user_id` records the member whose personal credential a source collects
with. It is intentionally nullable and NOT backfilled: existing rows keep
collecting through the deployment service credential (or an explicit
`params.token_owner`) instead of inheriting the previous implicit
"newest workspace owner" behaviour.

`notification_type` is a native PostgreSQL enum stored as member names. Alembic
does not autogenerate new members, so the value is added explicitly. PostgreSQL
12+ allows `ALTER TYPE ... ADD VALUE` inside a transaction as long as the new
value is not used in the same transaction.

Revision ID: 0123
Revises: 0122
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0123"
down_revision = "0122"
branch_labels = None
depends_on = None

NEW_NOTIFICATION_TYPE = "SOURCE_AUTH_REQUIRED"


def upgrade():
    schema = settings.DB_SCHEMA
    op.add_column(
        "sources",
        sa.Column("owner_user_id", sa.Integer(), nullable=True, comment=None),
        schema=schema,
    )
    op.create_foreign_key(
        "fk_sources_owner_user_id_users",
        "sources",
        "users",
        ["owner_user_id"],
        ["id"],
        source_schema=schema,
        referent_schema=schema,
        ondelete="SET NULL",
    )
    op.create_index("ix_sources_owner_user_id", "sources", ["owner_user_id"], schema=schema)
    op.alter_column(
        "sources",
        "owner_user_id",
        comment="Member whose personal L2 credential this source collects with; NULL = service-only",
        existing_type=sa.Integer(),
        existing_nullable=True,
        schema=schema,
    )
    op.execute(f"ALTER TYPE \"{schema}\".notification_type ADD VALUE IF NOT EXISTS '{NEW_NOTIFICATION_TYPE}'")


def downgrade():
    # PostgreSQL cannot drop an enum member. The type keeps the extra label, which
    # is inert for every reader that does not reference it; the column is removed.
    schema = settings.DB_SCHEMA
    op.drop_index("ix_sources_owner_user_id", table_name="sources", schema=schema)
    op.drop_constraint("fk_sources_owner_user_id_users", "sources", type_="foreignkey", schema=schema)
    op.drop_column("sources", "owner_user_id", schema=schema)
