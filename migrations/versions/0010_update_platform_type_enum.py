"""update platform type enum

Revision ID: 0010
Revises: 0009
Create Date: 2025-10-08 20:51:50.684208

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0010'
down_revision: Union[str, Sequence[str], None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()

    # Rename current enum type so we can recreate it with new values
    op.execute("ALTER TYPE social_manager.platform_type RENAME TO platform_type_old")

    # Temporarily cast column to TEXT
    op.alter_column(
        'platforms',
        'platform_type',
        type_=sa.Text(),
        postgresql_using="platform_type::text",
        schema='social_manager'
    )

    # Normalize existing data to the new enum members
    op.execute(
        """
        UPDATE social_manager.platforms
        SET platform_type = CASE
            WHEN platform_type = 'SOCIAL' THEN 'VK'
            WHEN platform_type = 'MESSENGER' THEN 'TELEGRAM'
            ELSE UPPER(platform_type)
        END
        """
    )

    # Create the new enum and cast column to it
    new_enum = sa.Enum('VK', 'TELEGRAM', name='platform_type', schema='social_manager')
    new_enum.create(bind, checkfirst=True)

    op.alter_column(
        'platforms',
        'platform_type',
        type_=new_enum,
        postgresql_using="platform_type::text::social_manager.platform_type",
        schema='social_manager'
    )

    # Drop the old enum type
    op.execute("DROP TYPE social_manager.platform_type_old")


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()

    # Rename current enum type so we can recreate the legacy one
    op.execute("ALTER TYPE social_manager.platform_type RENAME TO platform_type_new")

    # Cast column to TEXT for data transformation
    op.alter_column(
        'platforms',
        'platform_type',
        type_=sa.Text(),
        postgresql_using="platform_type::text",
        schema='social_manager'
    )

    # Restore legacy values
    op.execute(
        """
        UPDATE social_manager.platforms
        SET platform_type = CASE
            WHEN platform_type = 'VK' THEN 'SOCIAL'
            WHEN platform_type = 'TELEGRAM' THEN 'MESSENGER'
            ELSE UPPER(platform_type)
        END
        """
    )

    # Recreate legacy enum and cast column back
    old_enum = sa.Enum('SOCIAL', 'MESSENGER', name='platform_type', schema='social_manager')
    old_enum.create(bind, checkfirst=True)

    op.alter_column(
        'platforms',
        'platform_type',
        type_=old_enum,
        postgresql_using="platform_type::text::social_manager.platform_type",
        schema='social_manager'
    )

    # Drop the temporary enum type
    op.execute("DROP TYPE social_manager.platform_type_new")
