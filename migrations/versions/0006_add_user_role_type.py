"""add_user_role_type

Revision ID: 0006
Revises: 0005
Create Date: 2025-09-14 18:09:03.301403

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0006'
down_revision: Union[str, Sequence[str], None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("""
        UPDATE social_manager.users 
        SET role_id = (SELECT id FROM social_manager.roles WHERE codename = 'VIEWER' LIMIT 1)
        WHERE role_id IS NULL
    """)

    op.alter_column(
        'users',
        'role_id',
        existing_type=sa.INTEGER(),
        nullable=False,
        schema='social_manager'
    )

    op.alter_column(
        'roles',
        'codename',
        type_=sa.Enum('VIEWER', 'AI_BOT', 'MANAGER', 'ANALYST', 'MODERATOR', 'ADMIN', 'SUPERUSER',
                      name='user_role_type', schema='social_manager'),
        postgresql_using='codename::social_manager.user_role_type',
        schema='social_manager'
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        'roles',
        'codename',
        type_=sa.String(50),
        postgresql_using='codename::text',
        schema='social_manager'
    )

    op.execute("DROP TYPE IF EXISTS user_role_type")

    op.alter_column(
        'users',
        'role_id',
        existing_type=sa.INTEGER(),
        nullable=True,
        schema='social_manager'
    )
