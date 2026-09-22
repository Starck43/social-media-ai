"""add_media_prompts_to_scenarios

Revision ID: 0032
Revises: 0031
Create Date: 2025-10-15 21:48:36.236620

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0032'
down_revision: Union[str, Sequence[str], None] = '0031'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add media-specific prompt fields to bot_scenarios."""
    
    # Rename ai_prompt to text_prompt for clarity
    op.alter_column(
        'bot_scenarios',
        'ai_prompt',
        new_column_name='text_prompt',
        schema='social_manager',
        comment='Custom prompt for text analysis'
    )
    
    # Add new media prompt fields
    op.add_column(
        'bot_scenarios',
        sa.Column('image_prompt', sa.Text(), nullable=True,
                  comment='Custom prompt for image analysis. If null, uses default.'),
        schema='social_manager'
    )
    op.add_column(
        'bot_scenarios',
        sa.Column('video_prompt', sa.Text(), nullable=True,
                  comment='Custom prompt for video analysis. If null, uses default.'),
        schema='social_manager'
    )
    op.add_column(
        'bot_scenarios',
        sa.Column('audio_prompt', sa.Text(), nullable=True,
                  comment='Custom prompt for audio analysis. If null, uses default.'),
        schema='social_manager'
    )
    op.add_column(
        'bot_scenarios',
        sa.Column('unified_summary_prompt', sa.Text(), nullable=True,
                  comment='Custom prompt for unified summary. If null, uses default.'),
        schema='social_manager'
    )


def downgrade() -> None:
    """Revert media prompt fields."""
    
    # Rename back
    op.alter_column(
        'bot_scenarios',
        'text_prompt',
        new_column_name='ai_prompt',
        schema='social_manager'
    )
    
    # Drop new columns
    op.drop_column('bot_scenarios', 'unified_summary_prompt', schema='social_manager')
    op.drop_column('bot_scenarios', 'audio_prompt', schema='social_manager')
    op.drop_column('bot_scenarios', 'video_prompt', schema='social_manager')
    op.drop_column('bot_scenarios', 'image_prompt', schema='social_manager')
