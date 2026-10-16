"""Add personal billing ownership and immutable payer attribution.

Revision ID: 0097
Revises: 0096

No legacy tenant finance records are rewritten or attributed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0097"
down_revision = "0096"
branch_labels = None
depends_on = None

SCHEMA = settings.DB_SCHEMA


def _qualified(name: str) -> str:
    prep = op.get_bind().dialect.identifier_preparer
    return f"{prep.quote_schema(SCHEMA)}.{prep.quote(name)}"


def _immutable_guard(table: str, stem: str, message: str) -> None:
    qualified = _qualified(table)
    function = _qualified(f"reject_{stem}_mutation")
    op.execute(
        f"CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$ "
        f"BEGIN RAISE EXCEPTION '{message}' USING ERRCODE = '55000'; END; $$"
    )
    op.execute(
        f"CREATE TRIGGER {stem}_immutable BEFORE UPDATE OR DELETE ON {qualified} "
        f"FOR EACH ROW EXECUTE FUNCTION {function}()"
    )
    op.execute(
        f"CREATE TRIGGER {stem}_no_truncate BEFORE TRUNCATE ON {qualified} "
        f"FOR EACH STATEMENT EXECUTE FUNCTION {function}()"
    )


def upgrade() -> None:
    op.create_table(
        "personal_billing_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("daily_limit_usd", sa.Numeric(18, 9), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("configured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("policy_version", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", name="uq_personal_billing_accounts_user"),
        sa.CheckConstraint(
            "daily_limit_usd IS NULL OR (daily_limit_usd >= 0 AND daily_limit_usd <= 999999999.999999999)",
            name="ck_personal_billing_accounts_daily_limit",
        ),
        sa.CheckConstraint(
            "(configured_at IS NULL AND daily_limit_usd IS NULL AND policy_version IS NULL) OR "
            "(configured_at IS NOT NULL AND daily_limit_usd IS NOT NULL AND "
            "policy_version = 'personal-daily-cap-v1')",
            name="ck_personal_billing_accounts_configuration",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "task_billing_payers",
        sa.Column("id", sa.Integer(), primary_key=True),
        # Plain integer preserves consent history after task deletion.
        sa.Column("agent_task_id", sa.Integer(), nullable=False),
        sa.Column(
            "tenant_id", sa.Integer(), sa.ForeignKey(f"{SCHEMA}.tenants.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "payer_user_id", sa.Integer(), sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "consented_by_user_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("consent_version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("agent_task_id", "consent_version", name="uq_task_billing_payers_consent"),
        sa.CheckConstraint("agent_task_id > 0 AND consent_version > 0", name="ck_task_billing_payers_identity"),
        sa.CheckConstraint("consented_by_user_id = payer_user_id", name="ck_task_billing_payers_self_consent"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_task_billing_payers_current",
        "task_billing_payers",
        ["tenant_id", "agent_task_id", "consent_version"],
        schema=SCHEMA,
    )

    op.create_table(
        "personal_llm_dispatches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("dispatch_id", sa.String(32), nullable=False, unique=True),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.personal_billing_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "payer_user_id", sa.Integer(), sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "initiated_by_user_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "tenant_id", sa.Integer(), sa.ForeignKey(f"{SCHEMA}.tenants.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("utc_day", sa.Date(), nullable=False),
        sa.Column("llm_model_row_id", sa.Integer(), nullable=False),
        sa.Column("llm_provider_row_id", sa.Integer(), nullable=False),
        sa.Column("model_id", sa.String(200), nullable=False),
        sa.Column("provider_name", sa.String(200), nullable=False),
        sa.Column("input_cost_per_1k", sa.Numeric(18, 8), nullable=True),
        sa.Column("output_cost_per_1k", sa.Numeric(18, 8), nullable=True),
        sa.Column("tariff_state", sa.String(16), nullable=False),
        sa.Column("pricing_policy_version", sa.String(32), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("agent_task_id", sa.Integer(), nullable=True),
        sa.Column("agent_session_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("dispatch_id ~ '^[0-9a-f]{32}$'", name="ck_personal_llm_dispatch_identity"),
        sa.CheckConstraint("tariff_state IN ('known', 'free', 'undefined')", name="ck_personal_llm_dispatch_tariff"),
        sa.CheckConstraint(
            "(input_cost_per_1k IS NULL OR (input_cost_per_1k >= 0 AND input_cost_per_1k <= 9999999999.99999999)) "
            "AND (output_cost_per_1k IS NULL OR "
            "(output_cost_per_1k >= 0 AND output_cost_per_1k <= 9999999999.99999999))",
            name="ck_personal_llm_dispatch_rates",
        ),
        sa.CheckConstraint(
            "(tariff_state = 'undefined' AND (input_cost_per_1k IS NULL OR output_cost_per_1k IS NULL)) OR "
            "(tariff_state = 'free' AND input_cost_per_1k IS NOT NULL AND output_cost_per_1k IS NOT NULL "
            "AND input_cost_per_1k = 0 AND output_cost_per_1k = 0) OR "
            "(tariff_state = 'known' AND input_cost_per_1k IS NOT NULL AND output_cost_per_1k IS NOT NULL "
            "AND input_cost_per_1k + output_cost_per_1k > 0)",
            name="ck_personal_llm_dispatch_snapshot",
        ),
        sa.CheckConstraint("utc_day = (prepared_at AT TIME ZONE 'UTC')::date", name="ck_personal_llm_dispatch_utc_day"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_personal_llm_dispatches_account_created",
        "personal_llm_dispatches",
        ["account_id", "created_at"],
        schema=SCHEMA,
    )
    op.create_index("ix_personal_llm_dispatches_workspace", "personal_llm_dispatches", ["tenant_id"], schema=SCHEMA)

    op.execute(
        f"CREATE FUNCTION {_qualified('check_task_billing_payer_consent')}() RETURNS trigger LANGUAGE plpgsql AS $$ "
        "DECLARE task_tenant integer; account_ready boolean; owner_active boolean; latest_version integer; BEGIN "
        f"SELECT tenant_id INTO task_tenant FROM {_qualified('agent_tasks')} WHERE id = NEW.agent_task_id; "
        "IF task_tenant IS DISTINCT FROM NEW.tenant_id THEN "
        "RAISE EXCEPTION 'Task payer consent scope differs' USING ERRCODE = '23503'; END IF; "
        f"SELECT a.is_active AND a.configured_at IS NOT NULL, u.is_active INTO account_ready, owner_active "
        f"FROM {_qualified('personal_billing_accounts')} a JOIN {_qualified('users')} u ON u.id = a.user_id "
        "WHERE a.user_id = NEW.payer_user_id; "
        "IF account_ready IS DISTINCT FROM TRUE OR owner_active IS DISTINCT FROM TRUE THEN "
        "RAISE EXCEPTION 'Task payer has no configured personal account' USING ERRCODE = '23514'; END IF; "
        f"SELECT max(consent_version) INTO latest_version FROM {_qualified('task_billing_payers')} "
        "WHERE agent_task_id = NEW.agent_task_id; "
        "IF NEW.consent_version IS DISTINCT FROM coalesce(latest_version, 0) + 1 THEN "
        "RAISE EXCEPTION 'Task payer consent version is not next' USING ERRCODE = '23514'; END IF; "
        "RETURN NEW; END; $$"
    )
    op.execute(
        f"CREATE TRIGGER task_billing_payers_validate BEFORE INSERT ON {_qualified('task_billing_payers')} "
        f"FOR EACH ROW EXECUTE FUNCTION {_qualified('check_task_billing_payer_consent')}()"
    )
    _immutable_guard("task_billing_payers", "task_billing_payers", "Task payer consents are immutable")

    op.execute(
        f"CREATE FUNCTION {_qualified('check_personal_llm_dispatch_account')}() RETURNS trigger LANGUAGE plpgsql AS $$ "
        "DECLARE valid boolean; task_tenant integer; task_payer integer; initiator_active boolean; member_active boolean; admin_active boolean; "
        "receipt_tenant integer; receipt_model integer; receipt_provider integer; BEGIN "
        f"SELECT tenant_id, llm_model_row_id, llm_provider_row_id INTO receipt_tenant, receipt_model, receipt_provider "
        f"FROM {_qualified('llm_dispatch_receipts')} WHERE dispatch_id = NEW.dispatch_id; "
        "IF receipt_tenant IS DISTINCT FROM NEW.tenant_id OR receipt_model IS DISTINCT FROM NEW.llm_model_row_id "
        "OR receipt_provider IS DISTINCT FROM NEW.llm_provider_row_id THEN "
        "RAISE EXCEPTION 'Personal dispatch must match its legacy receipt identity' USING ERRCODE = '23514'; END IF; "
        f"SELECT a.is_active AND a.configured_at IS NOT NULL AND a.user_id = NEW.payer_user_id AND u.is_active "
        f"INTO valid FROM {_qualified('personal_billing_accounts')} a "
        f"JOIN {_qualified('users')} u ON u.id = a.user_id WHERE a.id = NEW.account_id; "
        "IF valid IS DISTINCT FROM TRUE THEN "
        "RAISE EXCEPTION 'Dispatch payer account differs or is inactive' USING ERRCODE = '23514'; END IF; "
        "IF NEW.initiated_by_user_id IS NOT NULL THEN "
        f"SELECT is_active INTO initiator_active FROM {_qualified('users')} WHERE id = NEW.initiated_by_user_id; "
        "IF initiator_active IS DISTINCT FROM TRUE THEN "
        "RAISE EXCEPTION 'Dispatch initiator is inactive' USING ERRCODE = '23514'; END IF; END IF; "
        "IF NEW.agent_task_id IS NULL AND NEW.initiated_by_user_id IS DISTINCT FROM NEW.payer_user_id THEN "
        "RAISE EXCEPTION 'Non-task dispatch initiator must be the payer' USING ERRCODE = '23514'; END IF; "
        "IF NEW.agent_task_id IS NULL AND NEW.purpose = 'admin_probe' THEN "
        f"SELECT EXISTS (SELECT 1 FROM {_qualified('users')} u JOIN {_qualified('roles')} r ON r.id = u.role_id "
        "WHERE u.id = NEW.payer_user_id AND u.is_active AND r.codename = 'admin') INTO admin_active; "
        "IF admin_active IS DISTINCT FROM TRUE THEN RAISE EXCEPTION 'Admin probe payer must be active platform admin' USING ERRCODE = '23514'; END IF; "
        "END IF; "
        "IF NEW.agent_task_id IS NULL AND NEW.purpose <> 'admin_probe' THEN "
        f"SELECT EXISTS (SELECT 1 FROM {_qualified('tenant_users')} m WHERE m.tenant_id = NEW.tenant_id "
        "AND m.user_id = NEW.payer_user_id AND m.is_active) INTO member_active; "
        "IF member_active IS DISTINCT FROM TRUE THEN "
        "RAISE EXCEPTION 'Non-task payer workspace membership is inactive' USING ERRCODE = '23514'; END IF; END IF; "
        "IF NEW.agent_task_id IS NOT NULL THEN "
        f"SELECT tenant_id INTO task_tenant FROM {_qualified('agent_tasks')} WHERE id = NEW.agent_task_id; "
        "IF task_tenant IS DISTINCT FROM NEW.tenant_id THEN "
        "RAISE EXCEPTION 'Dispatch task workspace differs' USING ERRCODE = '23503'; END IF; "
        f"SELECT payer_user_id INTO task_payer FROM {_qualified('task_billing_payers')} "
        "WHERE agent_task_id = NEW.agent_task_id AND tenant_id = NEW.tenant_id ORDER BY consent_version DESC LIMIT 1; "
        "IF task_payer IS DISTINCT FROM NEW.payer_user_id THEN "
        "RAISE EXCEPTION 'Dispatch task payer lacks current consent' USING ERRCODE = '23514'; END IF; "
        f"SELECT EXISTS (SELECT 1 FROM {_qualified('tenant_users')} m WHERE m.tenant_id = NEW.tenant_id "
        "AND m.user_id = NEW.payer_user_id AND m.is_active) INTO member_active; "
        "IF member_active IS DISTINCT FROM TRUE THEN "
        "RAISE EXCEPTION 'Task payer workspace membership is inactive' USING ERRCODE = '23514'; END IF; "
        "IF NEW.initiated_by_user_id IS NOT NULL THEN "
        f"SELECT EXISTS (SELECT 1 FROM {_qualified('tenant_users')} m WHERE m.tenant_id = NEW.tenant_id "
        "AND m.user_id = NEW.initiated_by_user_id AND m.is_active) INTO member_active; "
        "IF member_active IS DISTINCT FROM TRUE THEN "
        "RAISE EXCEPTION 'Task initiator workspace membership is inactive' USING ERRCODE = '23514'; END IF; END IF; "
        "END IF; "
        "RETURN NEW; END; $$"
    )
    op.execute(
        f"CREATE TRIGGER personal_llm_dispatches_validate BEFORE INSERT ON {_qualified('personal_llm_dispatches')} "
        f"FOR EACH ROW EXECUTE FUNCTION {_qualified('check_personal_llm_dispatch_account')}()"
    )
    _immutable_guard("personal_llm_dispatches", "personal_llm_dispatches", "Personal dispatch attribution is immutable")


def downgrade() -> None:
    raise RuntimeError("Personal billing attribution is retained; downgrade requires a reviewed preservation plan")
