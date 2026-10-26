"""Persist stable one-off task launch receipts.

Revision ID: 0112
Revises: 0111
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0112"
down_revision = "0111"
branch_labels = None
depends_on = None
SCHEMA = settings.DB_SCHEMA


def upgrade():
    op.add_column("agent_tasks", sa.Column("launch_key", sa.String(length=64), nullable=True), schema=SCHEMA)
    op.add_column("agent_tasks", sa.Column("launch_fingerprint", sa.String(length=64), nullable=True), schema=SCHEMA)
    op.add_column("agent_tasks", sa.Column("launch_job_id", sa.Integer(), nullable=True), schema=SCHEMA)
    op.create_unique_constraint(
        "uq_agent_task_tenant_launch_key", "agent_tasks", ["tenant_id", "launch_key"], schema=SCHEMA
    )


def downgrade():
    op.drop_constraint("uq_agent_task_tenant_launch_key", "agent_tasks", schema=SCHEMA, type_="unique")
    op.drop_column("agent_tasks", "launch_job_id", schema=SCHEMA)
    op.drop_column("agent_tasks", "launch_fingerprint", schema=SCHEMA)
    op.drop_column("agent_tasks", "launch_key", schema=SCHEMA)
