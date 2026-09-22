"""add_llm_cost_tracking_to_analytics

Revision ID: 0031
Revises: 0030
Create Date: 2025-10-15 20:56:04.732816

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0031'
down_revision: Union[str, Sequence[str], None] = '0030'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add LLM cost tracking fields to ai_analytics
    op.add_column('ai_analytics', 
        sa.Column('request_tokens', sa.Integer(), nullable=True, comment='Input tokens used'),
        schema='social_manager'
    )
    op.add_column('ai_analytics', 
        sa.Column('response_tokens', sa.Integer(), nullable=True, comment='Output tokens generated'),
        schema='social_manager'
    )
    op.add_column('ai_analytics', 
        sa.Column('estimated_cost', sa.Integer(), nullable=True, comment='Estimated cost in USD cents'),
        schema='social_manager'
    )
    op.add_column('ai_analytics', 
        sa.Column('provider_type', sa.String(50), nullable=True, comment='LLM provider: openai, deepseek, etc'),
        schema='social_manager'
    )
    op.add_column('ai_analytics', 
        sa.Column('media_types', sa.JSON(), nullable=True, comment='Types analyzed: text, image, video'),
        schema='social_manager'
    )
    
    # Create index for provider_type for faster aggregation queries
    op.create_index('idx_ai_analytics_provider', 'ai_analytics', ['provider_type'], schema='social_manager')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_ai_analytics_provider', table_name='ai_analytics', schema='social_manager')
    op.drop_column('ai_analytics', 'media_types', schema='social_manager')
    op.drop_column('ai_analytics', 'provider_type', schema='social_manager')
    op.drop_column('ai_analytics', 'estimated_cost', schema='social_manager')
    op.drop_column('ai_analytics', 'response_tokens', schema='social_manager')
    op.drop_column('ai_analytics', 'request_tokens', schema='social_manager')
