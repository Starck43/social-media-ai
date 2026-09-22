"""update_actiontype_enum

Revision ID: 0005
Revises: 0004
Create Date: 2025-09-14 16:50:26.299680

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0005'
down_revision: Union[str, Sequence[str], None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    # Rename the old enum type for rollback
    op.execute('ALTER TYPE IF EXISTS action_type RENAME TO actiontype_old')

    # Create new enum type with correct values in social_manager schema
    op.execute("""
        CREATE TYPE social_manager.action_type AS ENUM 
        ('view', 'create', 'edit', 'delete', 'analyze', 'moderate', 'export', 'configure')
    """)

    # Update the column to use the new enum type
    op.alter_column('permissions', 'action_type',
                   type_=sa.Enum('view', 'create', 'edit', 'delete', 'analyze', 'moderate', 'export', 'configure',
                                name='action_type', schema='social_manager'),
                   postgresql_using='action_type::text::social_manager.action_type',
                   schema='social_manager')

    # Drop the old enum type if it exists
    op.execute('DROP TYPE IF EXISTS actiontype_old')

def downgrade():
    # Rename the new enum type
    op.execute('ALTER TYPE social_manager.action_type RENAME TO actiontype_new')

    # Recreate the old enum type if needed
    op.execute("""
        CREATE TYPE social_manager.action_type AS ENUM 
        ('VIEW', 'CREATE', 'EDIT', 'DELETE', 'ANALYZE', 'MODERATE', 'EXPORT', 'CONFIGURE')
    """)

    # Update the column to use the old enum type
    op.alter_column('permissions', 'action_type',
                   type_=sa.Enum('VIEW', 'CREATE', 'EDIT', 'DELETE', 'ANALYZE', 'MODERATE', 'EXPORT', 'CONFIGURE',
                                name='action_type', schema='social_manager'),
                   postgresql_using='action_type::text::social_manager.action_type',
                   schema='social_manager')

    # Drop the new enum type
    op.execute('DROP TYPE actiontype_new')
