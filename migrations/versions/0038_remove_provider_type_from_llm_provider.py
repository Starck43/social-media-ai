"""remove_provider_type_from_llm_provider

Provider type is now determined by API URL, not stored in database.
All client selection logic moved to LLMClient.create() method.

Revision ID: 0038
Revises: 0037
Create Date: 2025-10-20 02:03:34.850053

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0038'
down_revision: Union[str, Sequence[str], None] = '0037'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Remove provider_type column from llm_providers
    op.drop_column('llm_providers', 'provider_type', schema='social_manager')
    
    # Drop the enum type (since it's no longer needed)
    op.execute('DROP TYPE IF EXISTS social_manager.llm_provider_type CASCADE')


def downgrade() -> None:
    # Recreate enum type
    op.execute("""
        CREATE TYPE social_manager.llm_provider_type AS ENUM (
            'deepseek', 'openai', 'anthropic', 'google', 'mistral', 'sambanova'
        )
    """)
    
    # Add provider_type column back
    op.add_column('llm_providers', 
        sa.Column('provider_type', 
                  postgresql.ENUM('deepseek', 'openai', 'anthropic', 'google', 'mistral', 'sambanova', 
                                  name='llm_provider_type', schema='social_manager'),
                  nullable=True),
        schema='social_manager'
    )
    
    # Populate provider_type based on api_url
    op.execute("""
        UPDATE social_manager.llm_providers 
        SET provider_type = CASE
            WHEN api_url LIKE '%deepseek%' THEN 'deepseek'::social_manager.llm_provider_type
            WHEN api_url LIKE '%openai%' THEN 'openai'::social_manager.llm_provider_type
            WHEN api_url LIKE '%anthropic%' THEN 'anthropic'::social_manager.llm_provider_type
            WHEN api_url LIKE '%sambanova%' THEN 'sambanova'::social_manager.llm_provider_type
            WHEN api_url LIKE '%google%' THEN 'google'::social_manager.llm_provider_type
            WHEN api_url LIKE '%mistral%' THEN 'mistral'::social_manager.llm_provider_type
            ELSE 'openai'::social_manager.llm_provider_type
        END
    """)
    
    # Make NOT NULL after populating
    op.alter_column('llm_providers', 'provider_type', nullable=False, schema='social_manager')
