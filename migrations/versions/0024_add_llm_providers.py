"""add_llm_providers

Revision ID: 0024
Revises: 20251013_172000
Create Date: 2025-10-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0024'
down_revision: Union[str, Sequence[str], None] = '0023'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add LLM providers table and update bot_scenarios."""
    
    # Create llm_provider_type enum if not exists and llm_providers table
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE social_manager.llm_provider_type AS ENUM (
                'deepseek', 'openai', 'anthropic', 'google', 'mistral', 'custom'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
        
        CREATE TABLE IF NOT EXISTS social_manager.llm_providers (
            id SERIAL PRIMARY KEY,
            name VARCHAR(255) NOT NULL UNIQUE,
            description TEXT,
            provider_type social_manager.llm_provider_type NOT NULL,
            api_url VARCHAR(500) NOT NULL,
            api_key_env VARCHAR(100) NOT NULL,
            model_name VARCHAR(100) NOT NULL,
            capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
            config JSONB DEFAULT '{}'::jsonb,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
        );
    """)
    
    # Create index on provider_type
    op.create_index(
        'idx_llm_providers_type',
        'llm_providers',
        ['provider_type'],
        schema='social_manager'
    )
    
    # Create index on is_active
    op.create_index(
        'idx_llm_providers_active',
        'llm_providers',
        ['is_active'],
        schema='social_manager'
    )
    
    # Add LLM provider columns to bot_scenarios
    op.add_column(
        'bot_scenarios',
        sa.Column('text_llm_provider_id', sa.Integer(), nullable=True),
        schema='social_manager'
    )
    op.add_column(
        'bot_scenarios',
        sa.Column('image_llm_provider_id', sa.Integer(), nullable=True),
        schema='social_manager'
    )
    op.add_column(
        'bot_scenarios',
        sa.Column('video_llm_provider_id', sa.Integer(), nullable=True),
        schema='social_manager'
    )
    
    # Create foreign keys
    op.create_foreign_key(
        'fk_bot_scenarios_text_llm_provider',
        'bot_scenarios', 'llm_providers',
        ['text_llm_provider_id'], ['id'],
        source_schema='social_manager',
        referent_schema='social_manager',
        ondelete='SET NULL'
    )
    op.create_foreign_key(
        'fk_bot_scenarios_image_llm_provider',
        'bot_scenarios', 'llm_providers',
        ['image_llm_provider_id'], ['id'],
        source_schema='social_manager',
        referent_schema='social_manager',
        ondelete='SET NULL'
    )
    op.create_foreign_key(
        'fk_bot_scenarios_video_llm_provider',
        'bot_scenarios', 'llm_providers',
        ['video_llm_provider_id'], ['id'],
        source_schema='social_manager',
        referent_schema='social_manager',
        ondelete='SET NULL'
    )
    
    # Insert default DeepSeek provider
    op.execute("""
        INSERT INTO social_manager.llm_providers 
            (name, description, provider_type, api_url, api_key_env, model_name, capabilities, is_active)
        VALUES 
            ('DeepSeek Default', 'Default DeepSeek LLM for text analysis', 'deepseek',
             'https://api.deepseek.com/v1/chat/completions', 'DEEPSEEK_API_KEY', 'deepseek-chat',
             '["text"]'::jsonb, true)
    """)


def downgrade() -> None:
    """Remove LLM providers table and columns from bot_scenarios."""
    
    # Drop foreign keys
    op.drop_constraint(
        'fk_bot_scenarios_text_llm_provider',
        'bot_scenarios',
        schema='social_manager',
        type_='foreignkey'
    )
    op.drop_constraint(
        'fk_bot_scenarios_image_llm_provider',
        'bot_scenarios',
        schema='social_manager',
        type_='foreignkey'
    )
    op.drop_constraint(
        'fk_bot_scenarios_video_llm_provider',
        'bot_scenarios',
        schema='social_manager',
        type_='foreignkey'
    )
    
    # Drop columns from bot_scenarios
    op.drop_column('bot_scenarios', 'text_llm_provider_id', schema='social_manager')
    op.drop_column('bot_scenarios', 'image_llm_provider_id', schema='social_manager')
    op.drop_column('bot_scenarios', 'video_llm_provider_id', schema='social_manager')
    
    # Drop indexes
    op.drop_index('idx_llm_providers_active', table_name='llm_providers', schema='social_manager')
    op.drop_index('idx_llm_providers_type', table_name='llm_providers', schema='social_manager')
    
    # Drop table
    op.drop_table('llm_providers', schema='social_manager')
    
    # Drop enum
    op.execute('DROP TYPE social_manager.llm_provider_type')
