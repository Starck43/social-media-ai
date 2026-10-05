"""add agent chat settings to tenants

Revision ID: 0077
Revises: 0076
Create Date: 2026-10-05
"""
from alembic import op
import sqlalchemy as sa

revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("agent_model", sa.String(100), nullable=True))
    op.add_column("tenants", sa.Column("agent_max_tokens", sa.Integer, nullable=True, server_default="1024"))
    op.add_column("tenants", sa.Column("agent_temperature", sa.Float, nullable=True, server_default="0.3"))
    op.add_column("tenants", sa.Column("agent_system_prompt", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("tenants", "agent_system_prompt")
    op.drop_column("tenants", "agent_temperature")
    op.drop_column("tenants", "agent_max_tokens")
    op.drop_column("tenants", "agent_model")
