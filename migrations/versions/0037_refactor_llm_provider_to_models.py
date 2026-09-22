"""refactor LLMProvider to LLMProvider + LLMModel architecture

Revision ID: 0037
Revises: 0036
Create Date: 2025-10-19 21:00:00.000000

Changes:
1. Remove model_name, capabilities from llm_providers
2. Add text_llm_model_id, image_llm_model_id, video_llm_model_id to bot_scenarios
3. Migrate data from old FK to new FK
4. Drop old FK columns text_llm_provider_id, etc.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = '0037'
down_revision: Union[str, Sequence[str], None] = '0036'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: refactor LLMProvider → LLMProvider + LLMModel."""
    
    # Step 1: Create llm_models table if not exists
    # (It might already exist if user created it manually)
    op.execute("""
        CREATE TABLE IF NOT EXISTS social_manager.llm_models (
            id SERIAL PRIMARY KEY,
            provider_id INTEGER NOT NULL REFERENCES social_manager.llm_providers(id) ON DELETE CASCADE,
            name VARCHAR(100) NOT NULL,
            description TEXT,
            input_cost DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            output_cost DOUBLE PRECISION NOT NULL DEFAULT 0.0,
            context_window INTEGER NOT NULL DEFAULT 4096,
            capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
            config JSONB DEFAULT '{}'::jsonb,
            is_active BOOLEAN DEFAULT TRUE,
            is_default BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
    """)
    
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_social_manager_llm_models_provider_id 
        ON social_manager.llm_models(provider_id);
    """)
    
    # Step 2: Migrate data from llm_providers to llm_models
    # For each provider, create a corresponding model
    op.execute("""
        INSERT INTO social_manager.llm_models (
            provider_id,
            name,
            description,
            capabilities,
            config,
            is_active,
            is_default,
            created_at,
            updated_at
        )
        SELECT 
            p.id as provider_id,
            COALESCE(p.model_name, p.name || ' Default Model') as name,
            p.description,
            COALESCE(p.capabilities::jsonb, '[]'::jsonb) as capabilities,
            COALESCE(p.config::jsonb, '{}'::jsonb) as config,
            p.is_active,
            TRUE as is_default,  -- First model for provider is default
            p.created_at,
            p.updated_at
        FROM social_manager.llm_providers p
        WHERE NOT EXISTS (
            SELECT 1 FROM social_manager.llm_models m 
            WHERE m.provider_id = p.id
        )
    """)
    
    # Step 3: Add new FK columns to bot_scenarios
    op.add_column('bot_scenarios',
        sa.Column('text_llm_model_id', sa.Integer(), nullable=True),
        schema='social_manager'
    )
    op.add_column('bot_scenarios',
        sa.Column('image_llm_model_id', sa.Integer(), nullable=True),
        schema='social_manager'
    )
    op.add_column('bot_scenarios',
        sa.Column('video_llm_model_id', sa.Integer(), nullable=True),
        schema='social_manager'
    )
    
    # Step 4: Migrate data from old FK to new FK
    # Map text_llm_provider_id → text_llm_model_id (using default model for provider)
    op.execute("""
        UPDATE social_manager.bot_scenarios bs
        SET text_llm_model_id = (
            SELECT m.id 
            FROM social_manager.llm_models m
            WHERE m.provider_id = bs.text_llm_provider_id
            AND m.is_default = TRUE
            LIMIT 1
        )
        WHERE bs.text_llm_provider_id IS NOT NULL
    """)
    
    op.execute("""
        UPDATE social_manager.bot_scenarios bs
        SET image_llm_model_id = (
            SELECT m.id 
            FROM social_manager.llm_models m
            WHERE m.provider_id = bs.image_llm_provider_id
            AND m.is_default = TRUE
            LIMIT 1
        )
        WHERE bs.image_llm_provider_id IS NOT NULL
    """)
    
    op.execute("""
        UPDATE social_manager.bot_scenarios bs
        SET video_llm_model_id = (
            SELECT m.id 
            FROM social_manager.llm_models m
            WHERE m.provider_id = bs.video_llm_provider_id
            AND m.is_default = TRUE
            LIMIT 1
        )
        WHERE bs.video_llm_provider_id IS NOT NULL
    """)
    
    # Step 5: Create FK constraints for new columns
    op.create_foreign_key(
        'fk_bot_scenarios_text_llm_model',
        'bot_scenarios', 'llm_models',
        ['text_llm_model_id'], ['id'],
        source_schema='social_manager',
        referent_schema='social_manager',
        ondelete='SET NULL'
    )
    op.create_foreign_key(
        'fk_bot_scenarios_image_llm_model',
        'bot_scenarios', 'llm_models',
        ['image_llm_model_id'], ['id'],
        source_schema='social_manager',
        referent_schema='social_manager',
        ondelete='SET NULL'
    )
    op.create_foreign_key(
        'fk_bot_scenarios_video_llm_model',
        'bot_scenarios', 'llm_models',
        ['video_llm_model_id'], ['id'],
        source_schema='social_manager',
        referent_schema='social_manager',
        ondelete='SET NULL'
    )
    
    # Step 6: Drop old FK columns from bot_scenarios
    # Check existing constraint names and drop them
    op.execute("""
        DO $$ 
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.table_constraints 
                      WHERE constraint_name = 'fk_bot_scenarios_text_llm_provider'
                      AND table_schema = 'social_manager') THEN
                ALTER TABLE social_manager.bot_scenarios 
                DROP CONSTRAINT fk_bot_scenarios_text_llm_provider;
            END IF;
            
            IF EXISTS (SELECT 1 FROM information_schema.table_constraints 
                      WHERE constraint_name = 'fk_bot_scenarios_image_llm_provider'
                      AND table_schema = 'social_manager') THEN
                ALTER TABLE social_manager.bot_scenarios 
                DROP CONSTRAINT fk_bot_scenarios_image_llm_provider;
            END IF;
            
            IF EXISTS (SELECT 1 FROM information_schema.table_constraints 
                      WHERE constraint_name = 'fk_bot_scenarios_video_llm_provider'
                      AND table_schema = 'social_manager') THEN
                ALTER TABLE social_manager.bot_scenarios 
                DROP CONSTRAINT fk_bot_scenarios_video_llm_provider;
            END IF;
        END $$;
    """)
    
    op.drop_column('bot_scenarios', 'text_llm_provider_id', schema='social_manager')
    op.drop_column('bot_scenarios', 'image_llm_provider_id', schema='social_manager')
    op.drop_column('bot_scenarios', 'video_llm_provider_id', schema='social_manager')
    
    # Step 7: Drop model_name and capabilities from llm_providers
    op.drop_column('llm_providers', 'model_name', schema='social_manager')
    op.drop_column('llm_providers', 'capabilities', schema='social_manager')


def downgrade() -> None:
    """Downgrade schema: revert to LLMProvider only."""
    
    # Step 1: Add back model_name and capabilities to llm_providers
    op.add_column('llm_providers',
        sa.Column('model_name', sa.String(100), nullable=True),
        schema='social_manager'
    )
    op.add_column('llm_providers',
        sa.Column('capabilities', postgresql.JSONB(), nullable=True),
        schema='social_manager'
    )
    
    # Step 2: Restore data from llm_models to llm_providers
    op.execute("""
        UPDATE social_manager.llm_providers p
        SET 
            model_name = m.name,
            capabilities = m.capabilities
        FROM social_manager.llm_models m
        WHERE m.provider_id = p.id AND m.is_default = TRUE
    """)
    
    # Step 3: Add back old FK columns to bot_scenarios
    op.add_column('bot_scenarios',
        sa.Column('text_llm_provider_id', sa.Integer(), nullable=True),
        schema='social_manager'
    )
    op.add_column('bot_scenarios',
        sa.Column('image_llm_provider_id', sa.Integer(), nullable=True),
        schema='social_manager'
    )
    op.add_column('bot_scenarios',
        sa.Column('video_llm_provider_id', sa.Integer(), nullable=True),
        schema='social_manager'
    )
    
    # Step 4: Restore data from new FK to old FK
    op.execute("""
        UPDATE social_manager.bot_scenarios bs
        SET text_llm_provider_id = (
            SELECT m.provider_id 
            FROM social_manager.llm_models m
            WHERE m.id = bs.text_llm_model_id
        )
        WHERE bs.text_llm_model_id IS NOT NULL
    """)
    
    op.execute("""
        UPDATE social_manager.bot_scenarios bs
        SET image_llm_provider_id = (
            SELECT m.provider_id 
            FROM social_manager.llm_models m
            WHERE m.id = bs.image_llm_model_id
        )
        WHERE bs.image_llm_model_id IS NOT NULL
    """)
    
    op.execute("""
        UPDATE social_manager.bot_scenarios bs
        SET video_llm_provider_id = (
            SELECT m.provider_id 
            FROM social_manager.llm_models m
            WHERE m.id = bs.video_llm_model_id
        )
        WHERE bs.video_llm_model_id IS NOT NULL
    """)
    
    # Step 5: Restore FK constraints
    op.create_foreign_key(
        'bot_scenarios_text_llm_provider_id_fkey',
        'bot_scenarios', 'llm_providers',
        ['text_llm_provider_id'], ['id'],
        source_schema='social_manager',
        referent_schema='social_manager',
        ondelete='SET NULL'
    )
    op.create_foreign_key(
        'bot_scenarios_image_llm_provider_id_fkey',
        'bot_scenarios', 'llm_providers',
        ['image_llm_provider_id'], ['id'],
        source_schema='social_manager',
        referent_schema='social_manager',
        ondelete='SET NULL'
    )
    op.create_foreign_key(
        'bot_scenarios_video_llm_provider_id_fkey',
        'bot_scenarios', 'llm_providers',
        ['video_llm_provider_id'], ['id'],
        source_schema='social_manager',
        referent_schema='social_manager',
        ondelete='SET NULL'
    )
    
    # Step 6: Drop new FK columns
    op.drop_constraint(
        'fk_bot_scenarios_text_llm_model',
        'bot_scenarios',
        type_='foreignkey',
        schema='social_manager'
    )
    op.drop_constraint(
        'fk_bot_scenarios_image_llm_model',
        'bot_scenarios',
        type_='foreignkey',
        schema='social_manager'
    )
    op.drop_constraint(
        'fk_bot_scenarios_video_llm_model',
        'bot_scenarios',
        type_='foreignkey',
        schema='social_manager'
    )
    
    op.drop_column('bot_scenarios', 'text_llm_model_id', schema='social_manager')
    op.drop_column('bot_scenarios', 'image_llm_model_id', schema='social_manager')
    op.drop_column('bot_scenarios', 'video_llm_model_id', schema='social_manager')
    
    # Step 7: Drop llm_models table
    op.drop_table('llm_models', schema='social_manager')
