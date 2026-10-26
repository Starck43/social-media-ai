"""General action proposals with sealed approvals and durable claims.
Revision ID: 0115
Revises: 0114
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0115"
down_revision = "0114"
branch_labels = depends_on = None
SCHEMA = settings.DB_SCHEMA
COLUMNS = (
    ("capability", sa.String(100), False, "social.legacy"),
    ("origin", sa.String(20), False, "task"),
    ("target_ref", sa.String(300), True, None),
    ("idempotency_key", sa.String(200), True, None),
    ("payload_hash", sa.String(64), True, None),
    ("approved_payload_hash", sa.String(64), True, None),
    ("approved_user_id", sa.Integer(), True, None),
    ("approval_expires_at", sa.DateTime(timezone=True), True, None),
    ("claimed_at", sa.DateTime(timezone=True), True, None),
    ("claim_expires_at", sa.DateTime(timezone=True), True, None),
    ("claim_token_hash", sa.String(64), True, None),
    ("executed_at", sa.DateTime(timezone=True), True, None),
    ("origin_snapshot", sa.JSON(), True, None),
)


def _replace_fk(column, target, ondelete):
    foreign_keys = sa.inspect(op.get_bind()).get_foreign_keys("bot_actions", schema=SCHEMA)
    matches = [fk for fk in foreign_keys if fk["constrained_columns"] == [column]]
    if len(matches) != 1:
        raise RuntimeError(f"Unexpected action FK for {column}")
    op.drop_constraint(matches[0]["name"], "bot_actions", type_="foreignkey", schema=SCHEMA)
    op.create_foreign_key(
        f"bot_actions_{column}_fkey",
        "bot_actions",
        target,
        [column],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete=ondelete,
    )


def upgrade():
    for name, typ, nullable, default in COLUMNS:
        op.add_column("bot_actions", sa.Column(name, typ, nullable=nullable, server_default=default), schema=SCHEMA)
    op.create_foreign_key(
        "fk_bot_actions_approved_user",
        "bot_actions",
        "users",
        ["approved_user_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_bot_actions_tenant_idempotency", "bot_actions", ["tenant_id", "idempotency_key"], schema=SCHEMA
    )
    for column, target in (("source_id", "sources"), ("agent_scenario_id", "agent_scenarios")):
        op.alter_column("bot_actions", column, nullable=True, schema=SCHEMA)
        _replace_fk(column, target, "SET NULL")
    op.alter_column("bot_actions", "action_type", nullable=True, schema=SCHEMA)
    schema = op.get_bind().dialect.identifier_preparer.quote_schema(SCHEMA)
    op.execute(
        f"UPDATE {schema}.bot_actions SET origin_snapshot = json_build_object('source_id', source_id, 'agent_scenario_id', agent_scenario_id, 'agent_task_id', agent_task_id, 'analytics_id', analytics_id)"
    )
    # No approval/hash backfill: legacy status is not authenticated approval.


def downgrade():
    schema = op.get_bind().dialect.identifier_preparer.quote_schema(SCHEMA)
    unsafe = (
        op.get_bind()
        .execute(
            sa.text(
                f"SELECT 1 FROM {schema}.bot_actions WHERE source_id IS NULL OR agent_scenario_id IS NULL OR action_type IS NULL OR status::text IN ('CLAIMED','UNKNOWN','CANCELLED') LIMIT 1"
            )
        )
        .first()
    )
    if unsafe:
        raise RuntimeError("Cannot discard general actions or new outcomes; export/reconcile explicitly first")
    for column, target in (("source_id", "sources"), ("agent_scenario_id", "agent_scenarios")):
        _replace_fk(column, target, "CASCADE")
        op.alter_column("bot_actions", column, nullable=False, schema=SCHEMA)
    op.alter_column("bot_actions", "action_type", nullable=False, schema=SCHEMA)
    op.drop_constraint("uq_bot_actions_tenant_idempotency", "bot_actions", type_="unique", schema=SCHEMA)
    op.drop_constraint("fk_bot_actions_approved_user", "bot_actions", type_="foreignkey", schema=SCHEMA)
    for name, *_ in reversed(COLUMNS):
        op.drop_column("bot_actions", name, schema=SCHEMA)
