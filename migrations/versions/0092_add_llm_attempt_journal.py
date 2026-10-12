"""Add the immutable llm_attempts journal (attempt-level accounting evidence).

PRD-03: each actual provider attempt is durable with its usage, tariff
snapshot, cost certainty and outcome. No foreign keys to sources, jobs or
agent_tasks — queue and transcript cleanup must not destroy spend evidence —
and no legacy backfill: earlier spend lived only in mutable projections and
cannot be reconstructed.

Revision ID: 0092
Revises: 0091
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0092"
down_revision = "0091"
branch_labels = None
depends_on = None
schema = settings.DB_SCHEMA


def upgrade() -> None:
    op.create_table(
        "llm_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Integer(),
            sa.ForeignKey(f"{schema}.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_key", sa.String(64), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("analysis_date", sa.Date(), nullable=True),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("agent_task_id", sa.Integer(), nullable=True),
        sa.Column("llm_model_row_id", sa.Integer(), nullable=True),
        sa.Column("model_id", sa.String(200), nullable=True),
        sa.Column("provider_name", sa.String(200), nullable=True),
        sa.Column("input_cost_per_1k", sa.Numeric(18, 8), nullable=True),
        sa.Column("output_cost_per_1k", sa.Numeric(18, 8), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(18, 9), nullable=True),
        sa.Column("cost_certainty", sa.String(32), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("attempt_key", name="uq_llm_attempts_key"),
        schema=schema,
    )
    op.create_index("ix_llm_attempts_tenant_id", "llm_attempts", ["tenant_id"], schema=schema)
    op.create_index("idx_llm_attempts_source_date", "llm_attempts", ["source_id", "analysis_date"], schema=schema)
    op.create_index("idx_llm_attempts_job", "llm_attempts", ["job_id"], schema=schema)


def downgrade() -> None:
    # Attempt evidence is retained deliberately; dropping it is an explicit
    # accounting decision, not a routine rollback.
    op.drop_index("idx_llm_attempts_job", "llm_attempts", schema=schema)
    op.drop_index("idx_llm_attempts_source_date", "llm_attempts", schema=schema)
    op.drop_index("ix_llm_attempts_tenant_id", "llm_attempts", schema=schema)
    op.drop_table("llm_attempts", schema=schema)
