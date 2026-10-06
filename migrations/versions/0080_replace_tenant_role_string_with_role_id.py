"""replace tenant_users/tenant_invites role string with role_id FK

Revision ID: 0080
Revises: 0079
Create Date: 2026-10-06 00:00:00.000000

The workspace role was a free string ("owner", "member") on both
``tenant_users`` and ``tenant_invites``.  This migration replaces it with a
nullable FK to ``roles.id`` so that workspace memberships inherit the same
platform-role permission matrix that ``/api`` and ``/admin`` already use.

Backfill rules:
  * "owner"  → SUPERUSER role
  * "member" → VIEWER role
  * any other value → NULL (will be resolved by Phase 2 logic)

The string column is dropped after the backfill; ``role_id IS NULL`` marks
legacy/unresolved rows for backward-compatible fallback.

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0080'
down_revision: Union[str, Sequence[str], None] = '0079'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "public"  # resolved from settings.DB_SCHEMA at runtime


def _legacy_to_codename(role: str | None) -> str | None:
    """Map legacy workspace-role string to a ``Role.codename``."""
    if role is None:
        return None
    legacy_map = {"owner": "SUPERUSER", "member": "VIEWER"}
    return legacy_map.get(role)


def upgrade() -> None:
    op.add_column(
        'tenant_users',
        sa.Column(
            'role_id',
            sa.Integer(),
            sa.ForeignKey(f'{SCHEMA}.roles.id', ondelete='SET NULL'),
            nullable=True,
            comment='Platform role for this workspace membership (NULL = legacy/unresolved)',
        ),
        schema=SCHEMA,
    )

    # Backfill tenant_users.role_id from tenant_users.role
    op.execute(
        f"""
        UPDATE "{SCHEMA}".tenant_users tu
        SET role_id = m.role_id
        FROM (
            SELECT id AS role_id, codename::text AS codename_text
            FROM "{SCHEMA}".roles
            WHERE codename::text IN ('SUPERUSER', 'VIEWER')
        ) m
        WHERE tu.role IN ('owner', 'member')
          AND m.codename_text = CASE tu.role
                               WHEN 'owner' THEN 'SUPERUSER'
                               WHEN 'member' THEN 'VIEWER'
                           END
        """
    )

    op.add_column(
        'tenant_invites',
        sa.Column(
            'role_id',
            sa.Integer(),
            sa.ForeignKey(f'{SCHEMA}.roles.id', ondelete='SET NULL'),
            nullable=True,
            comment='Platform role to assign on invite redemption',
        ),
        schema=SCHEMA,
    )

    # Backfill tenant_invites.role_id from tenant_invites.role
    op.execute(
        f"""
        UPDATE "{SCHEMA}".tenant_invites ti
        SET role_id = m.role_id
        FROM (
            SELECT id AS role_id, codename::text AS codename_text
            FROM "{SCHEMA}".roles
            WHERE codename::text IN ('SUPERUSER', 'VIEWER')
        ) m
        WHERE ti.role IN ('owner', 'member')
          AND m.codename_text = CASE ti.role
                               WHEN 'owner' THEN 'SUPERUSER'
                               WHEN 'member' THEN 'VIEWER'
                           END
        """
    )

    # Drop old string columns
    op.drop_column('tenant_users', 'role', schema=SCHEMA)
    op.drop_column('tenant_invites', 'role', schema=SCHEMA)


def downgrade() -> None:
    # Re-add string columns
    op.add_column(
        'tenant_invites',
        sa.Column('role', sa.String(20), nullable=False, server_default='owner'),
        schema=SCHEMA,
    )
    op.add_column(
        'tenant_users',
        sa.Column('role', sa.String(20), nullable=False, server_default='owner'),
        schema=SCHEMA,
    )

    # Backfill string from role_id (reverse map)
    op.execute(
        f"""
        UPDATE "{SCHEMA}".tenant_users tu
        SET role = CASE r.codename::text
                      WHEN 'SUPERUSER' THEN 'owner'
                      WHEN 'VIEWER'    THEN 'member'
                      ELSE 'member'
                   END
        FROM "{SCHEMA}".roles r
        WHERE tu.role_id = r.id
        """
    )
    op.execute(
        f"""
        UPDATE "{SCHEMA}".tenant_invites ti
        SET role = CASE r.codename::text
                      WHEN 'SUPERUSER' THEN 'owner'
                      WHEN 'VIEWER'    THEN 'member'
                      ELSE 'owner'
                   END
        FROM "{SCHEMA}".roles r
        WHERE ti.role_id = r.id
        """
    )

    op.drop_column('tenant_invites', 'role_id', schema=SCHEMA)
    op.drop_column('tenant_users', 'role_id', schema=SCHEMA)
