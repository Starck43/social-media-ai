"""P21-T15 token allowance pools and joint admission state.

The revision is intentionally named until merge-time numbering. Settlement
state is mutable and event/reservation evidence is immutable; P21-T16 owns the
state transitions and reconciliation worker.
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "p21t15_atomic_admission"
down_revision = "p21t14_rate_limits"
branch_labels = None
depends_on = None

SCHEMA = settings.DB_SCHEMA
S = f'"{SCHEMA}"'


def _immutable(table: str, trigger_stem: str, message: str) -> None:
    function = f"reject_{table}_mutation"
    op.execute(
        sa.text(
            f"CREATE FUNCTION {S}.{function}() RETURNS trigger LANGUAGE plpgsql AS "
            f"'BEGIN RAISE EXCEPTION ''{message}'' USING ERRCODE = ''55000''; END;'"
        )
    )
    op.execute(
        sa.text(
            f'CREATE TRIGGER "{trigger_stem}_immutable" BEFORE UPDATE OR DELETE ON {S}."{table}" '
            f"FOR EACH ROW EXECUTE FUNCTION {S}.{function}()"
        )
    )
    op.execute(
        sa.text(
            f'CREATE TRIGGER "{trigger_stem}_no_truncate" BEFORE TRUNCATE ON {S}."{table}" '
            f"FOR EACH STATEMENT EXECUTE FUNCTION {S}.{function}()"
        )
    )


def upgrade() -> None:
    op.create_table(
        "llm_token_allowance_cycles",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("plan_holder_user_id", sa.Integer, nullable=False),
        sa.Column("allowance_pool", sa.String(48), nullable=False),
        sa.Column("cycle_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cycle_ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("policy_version", sa.Integer, nullable=False),
        sa.Column("token_limit", sa.BigInteger, nullable=True),
        sa.Column("reserved_tokens", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("consumed_tokens", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["plan_holder_user_id"],
            [f"{SCHEMA}.users.id"],
            name="fk_llm_token_allowance_cycle_holder",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "plan_holder_user_id", "cycle_started_at", "allowance_pool", name="uq_llm_token_allowance_cycle_start"
        ),
        sa.UniqueConstraint("id", "plan_holder_user_id", "allowance_pool", name="uq_llm_token_allowance_cycle_holder"),
        sa.CheckConstraint("plan_holder_user_id > 0", name="ck_llm_token_allowance_cycle_holder"),
        sa.CheckConstraint("cycle_ends_at > cycle_started_at", name="ck_llm_token_allowance_cycle_window"),
        sa.CheckConstraint("length(allowance_pool) > 0", name="ck_llm_token_allowance_cycle_pool"),
        sa.CheckConstraint("policy_version > 0", name="ck_llm_token_allowance_cycle_policy"),
        sa.CheckConstraint("token_limit IS NULL OR token_limit >= 0", name="ck_llm_token_allowance_cycle_limit"),
        sa.CheckConstraint(
            "reserved_tokens >= 0 AND consumed_tokens >= 0", name="ck_llm_token_allowance_cycle_amounts"
        ),
        sa.CheckConstraint(
            "reserved_tokens <= 9223372036854775807 - consumed_tokens", name="ck_llm_token_allowance_cycle_sum"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_llm_token_allowance_cycles_active",
        "llm_token_allowance_cycles",
        ["plan_holder_user_id", "cycle_ends_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "llm_token_allowance_reservations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("dispatch_id", sa.String(32), nullable=False),
        sa.Column("tenant_id", sa.Integer, nullable=False),
        sa.Column("actor_user_id", sa.Integer, nullable=False),
        sa.Column("plan_holder_user_id", sa.Integer, nullable=False),
        sa.Column("allowance_pool", sa.String(48), nullable=False),
        sa.Column("cycle_id", sa.Integer, nullable=False),
        sa.Column("model_row_id", sa.Integer, nullable=False),
        sa.Column("provider_row_id", sa.Integer, nullable=False),
        sa.Column("model_id", sa.String(200), nullable=False),
        sa.Column("catalog_id", sa.String(80), nullable=False),
        sa.Column("funding_class", sa.String(16), nullable=False),
        sa.Column("payer_ref", sa.String(200), nullable=False),
        sa.Column("credential_version", sa.BigInteger, nullable=False),
        sa.Column("consent_version", sa.Integer, nullable=True),
        sa.Column("policy_version", sa.Integer, nullable=False),
        sa.Column("reserved_tokens", sa.BigInteger, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["dispatch_id"],
            [f"{SCHEMA}.llm_dispatch_receipts.dispatch_id"],
            name="fk_llm_token_reservation_receipt",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], [f"{SCHEMA}.tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_user_id"], [f"{SCHEMA}.users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["cycle_id", "plan_holder_user_id", "allowance_pool"],
            [
                f"{SCHEMA}.llm_token_allowance_cycles.id",
                f"{SCHEMA}.llm_token_allowance_cycles.plan_holder_user_id",
                f"{SCHEMA}.llm_token_allowance_cycles.allowance_pool",
            ],
            name="fk_llm_token_allowance_reservation_cycle",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["plan_holder_user_id"], [f"{SCHEMA}.users.id"], name="fk_llm_token_reservation_holder", ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("dispatch_id", name="uq_llm_token_allowance_reservations_dispatch"),
        sa.CheckConstraint("reserved_tokens >= 0", name="ck_llm_token_allowance_reservation_amount"),
        sa.CheckConstraint("length(allowance_pool) > 0", name="ck_llm_token_allowance_reservation_pool"),
        sa.CheckConstraint(
            "policy_version > 0 AND credential_version >= 0", name="ck_llm_token_allowance_reservation_versions"
        ),
        sa.CheckConstraint(
            "funding_class IN ('platform', 'personal')", name="ck_llm_token_allowance_reservation_funding"
        ),
        sa.CheckConstraint(
            "consent_version IS NULL OR consent_version > 0", name="ck_llm_token_allowance_reservation_consent"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_llm_token_allowance_reservations_holder_cycle",
        "llm_token_allowance_reservations",
        ["plan_holder_user_id", "cycle_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "llm_dispatch_reservation_states",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("dispatch_id", sa.String(32), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="reserved"),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_tokens", sa.BigInteger, nullable=True),
        sa.Column("actual_usd", sa.Numeric(18, 9), nullable=True),
        sa.Column("latest_event_key", sa.String(64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["dispatch_id"],
            [f"{SCHEMA}.llm_dispatch_receipts.dispatch_id"],
            name="fk_llm_reservation_state_receipt",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("dispatch_id", name="uq_llm_dispatch_reservation_state_dispatch"),
        sa.CheckConstraint(
            "status IN ('reserved', 'dispatched', 'not_dispatched', 'settled', 'unresolved')",
            name="ck_llm_dispatch_reservation_state_status",
        ),
        sa.CheckConstraint(
            "actual_tokens IS NULL OR actual_tokens >= 0", name="ck_llm_dispatch_reservation_state_tokens"
        ),
        sa.CheckConstraint(
            "actual_usd IS NULL OR (actual_usd >= 0 AND actual_usd <= 999999999.999999999)",
            name="ck_llm_dispatch_reservation_state_usd",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_llm_dispatch_reservation_states_reconcile",
        "llm_dispatch_reservation_states",
        ["lease_until", "dispatch_id"],
        schema=SCHEMA,
        postgresql_where=sa.text("status IN ('reserved', 'dispatched', 'unresolved') AND lease_until IS NOT NULL"),
    )

    op.create_table(
        "llm_dispatch_reservation_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("dispatch_id", sa.String(32), nullable=False),
        sa.Column("event_key", sa.String(64), nullable=False),
        sa.Column("event_kind", sa.String(20), nullable=False),
        sa.Column("actual_tokens", sa.BigInteger, nullable=True),
        sa.Column("actual_usd", sa.Numeric(18, 9), nullable=True),
        sa.Column("evidence_fingerprint", sa.String(64), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["dispatch_id"],
            [f"{SCHEMA}.llm_dispatch_receipts.dispatch_id"],
            name="fk_llm_reservation_event_receipt",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("dispatch_id", "event_key", name="uq_llm_dispatch_reservation_event_key"),
        sa.CheckConstraint(
            "event_kind IN ('admitted', 'dispatched', 'not_dispatched', 'settled', 'unresolved', 'reconciled')",
            name="ck_llm_dispatch_reservation_event_kind",
        ),
        sa.CheckConstraint(
            "actual_tokens IS NULL OR actual_tokens >= 0", name="ck_llm_dispatch_reservation_event_tokens"
        ),
        sa.CheckConstraint(
            "actual_usd IS NULL OR (actual_usd >= 0 AND actual_usd <= 999999999.999999999)",
            name="ck_llm_dispatch_reservation_event_usd",
        ),
        sa.CheckConstraint(
            "evidence_fingerprint IS NULL OR evidence_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_llm_dispatch_reservation_event_fingerprint",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_llm_dispatch_reservation_events_dispatch",
        "llm_dispatch_reservation_events",
        ["dispatch_id", "observed_at"],
        schema=SCHEMA,
    )

    _immutable(
        "llm_token_allowance_reservations",
        "llm_token_allowance_reservations",
        "Token allowance reservation evidence is immutable",
    )
    _immutable(
        "llm_dispatch_reservation_events",
        "llm_dispatch_reservation_events",
        "Dispatch reservation events are immutable",
    )


def downgrade() -> None:
    # A live reservation or event is evidence and must never be erased by a
    # convenience downgrade. The operator must first use the accepted retention
    # workflow (outside this migration); the schema owner can then archive/drop.
    for table in (
        "llm_dispatch_reservation_events",
        "llm_dispatch_reservation_states",
        "llm_token_allowance_reservations",
        "llm_token_allowance_cycles",
    ):
        op.execute(
            sa.text(
                f'DO $$ BEGIN IF EXISTS (SELECT 1 FROM {S}."{table}") THEN '
                f"RAISE EXCEPTION 'Refusing to drop nonempty {table}' USING ERRCODE = '55000'; END IF; END $$"
            )
        )
    op.drop_index(
        "ix_llm_dispatch_reservation_events_dispatch", table_name="llm_dispatch_reservation_events", schema=SCHEMA
    )
    op.drop_table("llm_dispatch_reservation_events", schema=SCHEMA)
    op.drop_index(
        "ix_llm_dispatch_reservation_states_reconcile", table_name="llm_dispatch_reservation_states", schema=SCHEMA
    )
    op.drop_table("llm_dispatch_reservation_states", schema=SCHEMA)
    op.drop_index(
        "ix_llm_token_allowance_reservations_holder_cycle",
        table_name="llm_token_allowance_reservations",
        schema=SCHEMA,
    )
    op.drop_table("llm_token_allowance_reservations", schema=SCHEMA)
    op.drop_index("ix_llm_token_allowance_cycles_active", table_name="llm_token_allowance_cycles", schema=SCHEMA)
    op.drop_table("llm_token_allowance_cycles", schema=SCHEMA)
    op.execute(sa.text(f"DROP FUNCTION {S}.reject_llm_dispatch_reservation_events_mutation()"))
    op.execute(sa.text(f"DROP FUNCTION {S}.reject_llm_token_allowance_reservations_mutation()"))
