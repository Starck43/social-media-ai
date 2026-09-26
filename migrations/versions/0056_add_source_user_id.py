"""add user_id to sources (user-scoped sources)

Revision ID: 0056
Revises: 0055
Create Date: 2026-09-25 23:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0056"
down_revision: Union[str, Sequence[str], None] = "0055"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "social_manager"


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        schema=SCHEMA,
    )
    op.create_index("idx_sources_user_id", "sources", ["user_id"], schema=SCHEMA, unique=False)
    op.drop_constraint(
        "uq_source_tenant_platform_external",
        "sources",
        schema=SCHEMA,
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_source_tenant_user_platform_external",
        "sources",
        ["tenant_id", "user_id", "platform_id", "external_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_source_tenant_user_platform_external",
        "sources",
        schema=SCHEMA,
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_source_tenant_platform_external",
        "sources",
        ["tenant_id", "platform_id", "external_id"],
        schema=SCHEMA,
    )
    op.drop_index("idx_sources_user_id", "sources", schema=SCHEMA)
    op.drop_column("sources", "user_id", schema=SCHEMA)