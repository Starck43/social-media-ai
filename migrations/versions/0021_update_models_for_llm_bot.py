"""Update models for LLM bot integration

Revision ID: 0021
Revises: 0020
Create Date: 2025-10-11 03:05:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0021'
down_revision: Union[str, Sequence[str], None] = '0020'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema for Source, BotScenario and AIAnalytics."""

    # Source: add bot_scenario_id FK (nullable, ondelete SET NULL)
    op.add_column(
        'sources',
        sa.Column('bot_scenario_id', sa.Integer(), nullable=True),
        schema='social_manager'
    )
    op.create_foreign_key(
        'fk_sources_bot_scenario',
        'sources', 'bot_scenarios',
        ['bot_scenario_id'], ['id'],
        source_schema='social_manager', referent_schema='social_manager',
        ondelete='SET NULL'
    )

    # BotScenario: add content_types JSONB (nullable)
    op.add_column(
        'bot_scenarios',
        sa.Column('content_types', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema='social_manager'
    )

    # AIAnalytics: add chain columns, llm trace fields and topic_chain index, fix parent FK to include schema
    op.add_column(
        'ai_analytics',
        sa.Column('topic_chain_id', sa.String(length=100), nullable=True),
        schema='social_manager'
    )
    op.add_column(
        'ai_analytics',
        sa.Column('parent_analysis_id', sa.Integer(), nullable=True),
        schema='social_manager'
    )
    op.add_column(
        'ai_analytics',
        sa.Column('llm_model', sa.String(length=100), nullable=True),
        schema='social_manager'
    )
    op.add_column(
        'ai_analytics',
        sa.Column('prompt_text', sa.Text(), nullable=True),
        schema='social_manager'
    )
    op.add_column(
        'ai_analytics',
        sa.Column('response_payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema='social_manager'
    )
    # Create index on topic_chain_id if not exists
    op.create_index('idx_ai_analytics_topic_chain', 'ai_analytics', ['topic_chain_id'], unique=False, schema='social_manager')

    # Drop analysis_type added in 0020 (obsolete)
    with op.batch_alter_table('ai_analytics', schema='social_manager'):
        try:
            op.drop_column('ai_analytics', 'analysis_type', schema='social_manager')
        except Exception:
            # Column may not exist in some environments; ignore
            pass
    try:
        op.execute("DROP TYPE IF EXISTS social_manager.analysis_type")
    except Exception:
        pass

    # Fix parent FK to include schema: drop existing FK and recreate with schema-qualified table
    try:
        # Attempt to drop any existing foreign key constraint on parent_analysis_id
        conn = op.get_bind()
        insp = sa.inspect(conn)
        fks = insp.get_foreign_keys('ai_analytics', schema='social_manager')
        for fk in fks:
            if set(fk.get('constrained_columns') or []) == {'parent_analysis_id'}:
                op.drop_constraint(fk['name'], 'ai_analytics', type_='foreignkey', schema='social_manager')
                break
    except Exception:
        pass
    op.create_foreign_key(
        'fk_ai_analytics_parent',
        'ai_analytics', 'ai_analytics',
        ['parent_analysis_id'], ['id'],
        source_schema='social_manager', referent_schema='social_manager',
        ondelete=None
    )


def downgrade() -> None:
    """Downgrade schema changes."""

    # AIAnalytics
    try:
        op.drop_constraint('fk_ai_analytics_parent', 'ai_analytics', type_='foreignkey', schema='social_manager')
    except Exception:
        pass
    op.drop_index('idx_ai_analytics_topic_chain', table_name='ai_analytics', schema='social_manager')
    op.drop_column('ai_analytics', 'response_payload', schema='social_manager')
    op.drop_column('ai_analytics', 'prompt_text', schema='social_manager')
    op.drop_column('ai_analytics', 'llm_model', schema='social_manager')

    # BotScenario
    op.drop_column('bot_scenarios', 'content_types', schema='social_manager')

    # Source
    try:
        op.drop_constraint('fk_sources_bot_scenario', 'sources', type_='foreignkey', schema='social_manager')
    except Exception:
        pass
    op.drop_column('sources', 'bot_scenario_id', schema='social_manager')

    # Recreate analysis_type type (optional)
    try:
        op.execute("CREATE TYPE social_manager.analysis_type AS ENUM ('sentiment', 'topics', 'activity', 'keywords', 'engagement')")
        op.add_column('ai_analytics', sa.Column('analysis_type', sa.Enum('sentiment', 'topics', 'activity', 'keywords', 'engagement', name='analysis_type', schema='social_manager', inherit_schema=True), nullable=True), schema='social_manager')
    except Exception:
        pass
