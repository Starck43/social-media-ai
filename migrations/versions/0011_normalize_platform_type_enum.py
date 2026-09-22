"""normalize platform type enum

Revision ID: 0011
Revises: 0010
Create Date: 2025-10-08 22:30:55.570014

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0011'
down_revision: Union[str, Sequence[str], None] = '0010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Ensure platform_type enum stores lowercase values matching PlatformType."""

    # Rename old enum so we can recreate it with desired values
    op.execute("ALTER TYPE social_manager.platform_type RENAME TO platform_type_old")

    # Temporarily cast column to text for data migration
    op.alter_column(
        'platforms',
        'platform_type',
        type_=sa.Text(),
        postgresql_using="platform_type::text",
        schema='social_manager'
    )

    # Create the normalized enum type
    op.execute("CREATE TYPE social_manager.platform_type AS ENUM ('vk', 'telegram')")

    # Normalize existing data to lowercase values before casting
    op.execute("UPDATE social_manager.platforms SET platform_type = lower(platform_type)")

    # Cast column to the new enum type
    op.alter_column(
        'platforms',
        'platform_type',
        type_=sa.Enum('vk', 'telegram', name='platform_type', schema='social_manager'),
        postgresql_using="platform_type::social_manager.platform_type",
        schema='social_manager'
    )

    # Drop the old enum definition
    op.execute("DROP TYPE social_manager.platform_type_old")


def downgrade() -> None:
    """Revert platform_type enum back to uppercase values."""

    op.execute("ALTER TYPE social_manager.platform_type RENAME TO platform_type_new")

    op.alter_column(
        'platforms',
        'platform_type',
        type_=sa.Text(),
        postgresql_using="platform_type::text",
        schema='social_manager'
    )

    op.execute("CREATE TYPE social_manager.platform_type AS ENUM ('VK', 'TELEGRAM')")

    op.execute("UPDATE social_manager.platforms SET platform_type = upper(platform_type)")

    op.alter_column(
        'platforms',
        'platform_type',
        type_=sa.Enum('VK', 'TELEGRAM', name='platform_type', schema='social_manager'),
        postgresql_using="platform_type::social_manager.platform_type",
        schema='social_manager'
    )

    op.execute("DROP TYPE social_manager.platform_type_new")
