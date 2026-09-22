"""add flexible llm mapping to bot scenarios

Revision ID: 20251014_010000
Revises: 0025
Create Date: 2025-10-14 01:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '0025'
down_revision = '0024'
branch_labels = None
depends_on = None


def upgrade():
    """
    Add flexible LLM mapping fields to bot_scenarios.
    
    New fields:
    - llm_mapping: JSON field for flexible provider/model mapping
    - llm_strategy: Strategy for automatic LLM selection
    """
    # Add llm_mapping JSON field
    op.add_column(
        'bot_scenarios',
        sa.Column('llm_mapping', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        schema='social_manager'
    )
    
    # Add llm_strategy field
    op.add_column(
        'bot_scenarios',
        sa.Column('llm_strategy', sa.String(length=50), nullable=True),
        schema='social_manager'
    )
    
    # Set default strategy for existing rows
    op.execute("""
        UPDATE social_manager.bot_scenarios
        SET llm_strategy = 'cost_efficient'
        WHERE llm_strategy IS NULL
    """)
    
    # Optionally migrate existing FK-based mappings to new JSON format
    op.execute("""
        UPDATE social_manager.bot_scenarios bs
        SET llm_mapping = (
            SELECT jsonb_build_object(
                'text', CASE WHEN bs.text_llm_provider_id IS NOT NULL THEN
                    jsonb_build_object(
                        'provider_id', bs.text_llm_provider_id,
                        'provider_type', lp_text.provider_type,
                        'model_id', lp_text.model_name,
                        'capabilities', lp_text.capabilities
                    )
                ELSE NULL END,
                'image', CASE WHEN bs.image_llm_provider_id IS NOT NULL THEN
                    jsonb_build_object(
                        'provider_id', bs.image_llm_provider_id,
                        'provider_type', lp_image.provider_type,
                        'model_id', lp_image.model_name,
                        'capabilities', lp_image.capabilities
                    )
                ELSE NULL END,
                'video', CASE WHEN bs.video_llm_provider_id IS NOT NULL THEN
                    jsonb_build_object(
                        'provider_id', bs.video_llm_provider_id,
                        'provider_type', lp_video.provider_type,
                        'model_id', lp_video.model_name,
                        'capabilities', lp_video.capabilities
                    )
                ELSE NULL END
            )
            FROM social_manager.llm_providers lp_text,
                 social_manager.llm_providers lp_image,
                 social_manager.llm_providers lp_video
            WHERE lp_text.id = bs.text_llm_provider_id
               OR lp_image.id = bs.image_llm_provider_id
               OR lp_video.id = bs.video_llm_provider_id
            LIMIT 1
        )
        WHERE (bs.text_llm_provider_id IS NOT NULL 
            OR bs.image_llm_provider_id IS NOT NULL 
            OR bs.video_llm_provider_id IS NOT NULL)
          AND bs.llm_mapping IS NULL
    """)
    
    # Create index on llm_mapping for faster queries
    op.execute("""
        CREATE INDEX idx_bot_scenarios_llm_mapping
        ON social_manager.bot_scenarios
        USING gin ((llm_mapping::jsonb))
    """)


def downgrade():
    """Remove flexible LLM mapping fields."""
    # Drop index
    op.drop_index(
        'idx_bot_scenarios_llm_mapping',
        table_name='bot_scenarios',
        schema='social_manager'
    )
    
    # Drop columns
    op.drop_column('bot_scenarios', 'llm_strategy', schema='social_manager')
    op.drop_column('bot_scenarios', 'llm_mapping', schema='social_manager')
