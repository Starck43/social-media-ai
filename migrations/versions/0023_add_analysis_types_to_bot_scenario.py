"""add_analysis_types_to_bot_scenario

Revision ID: 0023
Revises: 0022
Create Date: 2025-10-12 15:33:26.532073

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0023'
down_revision: Union[str, Sequence[str], None] = '0022'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: separate analysis_types from scope."""
    # Add new columns
    op.add_column('bot_scenarios', sa.Column('description', sa.Text(), nullable=True), schema='social_manager')
    op.add_column('bot_scenarios', sa.Column('analysis_types', sa.JSON(), nullable=False, server_default='[]'), schema='social_manager')
    
    # Migrate data: extract analysis_types from scope
    connection = op.get_bind()
    
    # Get all scenarios with their scope
    result = connection.execute(sa.text("SELECT id, scope FROM social_manager.bot_scenarios"))
    scenarios = result.fetchall()
    
    for scenario_id, scope in scenarios:
        if scope and isinstance(scope, dict):
            # Extract analysis_types from scope
            analysis_types = scope.get('analysis_types', [])
            
            # Remove analysis_types from scope
            if 'analysis_types' in scope:
                del scope['analysis_types']
            
            # Update record
            connection.execute(
                sa.text("UPDATE social_manager.bot_scenarios SET analysis_types = :analysis_types, scope = :scope WHERE id = :id"),
                {"analysis_types": sa.JSON().bind_processor(connection.dialect)(analysis_types), 
                 "scope": sa.JSON().bind_processor(connection.dialect)(scope),
                 "id": scenario_id}
            )
    
    connection.commit()


def downgrade() -> None:
    """Downgrade schema: merge analysis_types back into scope."""
    # Migrate data back: put analysis_types into scope
    connection = op.get_bind()
    
    result = connection.execute(sa.text("SELECT id, scope, analysis_types FROM social_manager.bot_scenarios"))
    scenarios = result.fetchall()
    
    for scenario_id, scope, analysis_types in scenarios:
        scope = scope or {}
        if analysis_types:
            scope['analysis_types'] = analysis_types
        
        connection.execute(
            sa.text("UPDATE social_manager.bot_scenarios SET scope = :scope WHERE id = :id"),
            {"scope": sa.JSON().bind_processor(connection.dialect)(scope), "id": scenario_id}
        )
    
    connection.commit()
    
    # Drop columns
    op.drop_column('bot_scenarios', 'analysis_types', schema='social_manager')
    op.drop_column('bot_scenarios', 'description', schema='social_manager')
