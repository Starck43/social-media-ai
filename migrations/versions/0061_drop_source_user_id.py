"""drop sources.user_id — sources are workspace-scoped, not user-scoped

Sources belong to a Tenant, visible to every member of the workspace. The
`user_id` column made the unique key `(tenant_id, user_id, platform_id,
external_id)` so the same group could be tracked twice (once per user) and,
because NULL never equals NULL in a unique constraint, messenger-only members
(user_id NULL) could create unlimited duplicates.

The column was a deviation from the tenant-scoped design (see the 0056 downgrade,
which already reverted to the workspace-level key). This migration restores that:
unique per (tenant_id, platform_id, external_id), keeping the active row when a
duplicate group exists.

Revision ID: 0061
Revises: 0060
Create Date: 2026-09-27

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0061"
down_revision: Union[str, Sequence[str], None] = "0060"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "social_manager"


def upgrade() -> None:
    # Keep exactly one row per (tenant_id, platform_id, external_id), preferring
    # an active one, then the smallest id; children (ai_analytics, bot_actions)
    # cascade on delete.
    op.execute(
        f"""
        DELETE FROM {SCHEMA}.sources s
        WHERE EXISTS (
            SELECT 1 FROM {SCHEMA}.sources k
            WHERE k.tenant_id = s.tenant_id
              AND k.platform_id = s.platform_id
              AND k.external_id = s.external_id
              AND k.id <> s.id
              AND (
                (COALESCE(k.is_active, false) AND NOT COALESCE(s.is_active, false))
                OR (COALESCE(k.is_active, false) = COALESCE(s.is_active, false) AND k.id < s.id)
              )
        )
        """
    )
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


def downgrade() -> None:
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
