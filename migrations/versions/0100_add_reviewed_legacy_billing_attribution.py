"""Add consented, reporting-only mappings for exact legacy billing identities.

Revision ID: 0100
Revises: 0099
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0100"
down_revision = "0099"
branch_labels = None
depends_on = None
SCHEMA = settings.DB_SCHEMA


def _q(name: str) -> str:
    prep = op.get_bind().dialect.identifier_preparer
    return f"{prep.quote_schema(SCHEMA)}.{prep.quote(name)}"


def _immutable(table: str) -> None:
    function = _q(f"reject_{table}_mutation")
    op.execute(
        f"CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$ "
        "BEGIN RAISE EXCEPTION 'Legacy billing attribution evidence is immutable' USING ERRCODE = '55000'; END; $$"
    )
    op.execute(
        f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {_q(table)} FOR EACH ROW EXECUTE FUNCTION {function}()"
    )
    op.execute(
        f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {_q(table)} FOR EACH STATEMENT EXECUTE FUNCTION {function}()"
    )


def upgrade() -> None:
    op.create_table(
        "personal_legacy_attribution_consents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_key", sa.String(64), nullable=False),
        sa.Column(
            "dispatch_id",
            sa.String(32),
            sa.ForeignKey(f"{SCHEMA}.llm_dispatch_receipts.dispatch_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "attempt_key",
            sa.String(64),
            sa.ForeignKey(f"{SCHEMA}.llm_attempts.attempt_key", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "tenant_id", sa.Integer(), sa.ForeignKey(f"{SCHEMA}.tenants.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.personal_billing_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "consented_by_user_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("source_key", "account_id", name="uq_personal_legacy_consent_identity_account"),
        sa.CheckConstraint(
            "(dispatch_id IS NOT NULL) <> (attempt_key IS NOT NULL)", name="ck_personal_legacy_consent_one_identity"
        ),
        sa.CheckConstraint(
            "(dispatch_id IS NOT NULL AND source_key = 'r:' || dispatch_id) OR (attempt_key IS NOT NULL AND source_key = 'a:' || attempt_key)",
            name="ck_personal_legacy_consent_source_key",
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "personal_legacy_attributions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_key", sa.String(64), nullable=False),
        sa.Column(
            "dispatch_id",
            sa.String(32),
            sa.ForeignKey(f"{SCHEMA}.llm_dispatch_receipts.dispatch_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "attempt_key",
            sa.String(64),
            sa.ForeignKey(f"{SCHEMA}.llm_attempts.attempt_key", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "tenant_id", sa.Integer(), sa.ForeignKey(f"{SCHEMA}.tenants.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "account_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.personal_billing_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "consent_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.personal_legacy_attribution_consents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "consented_by_user_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "reviewed_by_user_id",
            sa.Integer(),
            sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("source_key", name="uq_personal_legacy_attribution_identity"),
        sa.UniqueConstraint("consent_id", name="uq_personal_legacy_attribution_consent"),
        sa.CheckConstraint(
            "(dispatch_id IS NOT NULL) <> (attempt_key IS NOT NULL)", name="ck_personal_legacy_attribution_one_identity"
        ),
        sa.CheckConstraint(
            "(dispatch_id IS NOT NULL AND source_key = 'r:' || dispatch_id) OR (attempt_key IS NOT NULL AND source_key = 'a:' || attempt_key)",
            name="ck_personal_legacy_attribution_source_key",
        ),
        schema=SCHEMA,
    )
    op.execute(
        f"CREATE FUNCTION {_q('check_personal_legacy_attribution_consent')}() RETURNS trigger LANGUAGE plpgsql AS $$ "
        "DECLARE account_owner integer; source_tenant integer; owner_active boolean; member_active boolean; BEGIN "
        f"SELECT user_id INTO account_owner FROM {_q('personal_billing_accounts')} WHERE id = NEW.account_id; "
        "IF account_owner IS DISTINCT FROM NEW.consented_by_user_id THEN "
        "RAISE EXCEPTION 'Legacy attribution consent must come from the account owner' USING ERRCODE = '23514'; END IF; "
        f"SELECT is_active INTO owner_active FROM {_q('users')} WHERE id = NEW.consented_by_user_id; "
        "IF owner_active IS DISTINCT FROM TRUE THEN RAISE EXCEPTION 'Consent owner is inactive' USING ERRCODE = '23514'; END IF; "
        f"IF NEW.dispatch_id IS NOT NULL THEN SELECT tenant_id INTO source_tenant FROM {_q('llm_dispatch_receipts')} WHERE dispatch_id = NEW.dispatch_id; "
        f"ELSE SELECT tenant_id INTO source_tenant FROM {_q('llm_attempts')} WHERE attempt_key = NEW.attempt_key; END IF; "
        "IF source_tenant IS DISTINCT FROM NEW.tenant_id THEN RAISE EXCEPTION 'Legacy identity workspace differs' USING ERRCODE = '23514'; END IF; "
        f"SELECT EXISTS (SELECT 1 FROM {_q('tenant_users')} m WHERE m.tenant_id = NEW.tenant_id "
        "AND m.user_id = NEW.consented_by_user_id AND m.is_active) INTO member_active; "
        "IF member_active IS DISTINCT FROM TRUE THEN RAISE EXCEPTION 'Consent owner has no active workspace membership' USING ERRCODE = '23514'; END IF; "
        "RETURN NEW; END; $$"
    )
    op.execute(
        f"CREATE TRIGGER personal_legacy_attribution_consents_validate BEFORE INSERT ON {_q('personal_legacy_attribution_consents')} "
        f"FOR EACH ROW EXECUTE FUNCTION {_q('check_personal_legacy_attribution_consent')}()"
    )
    op.execute(
        f"CREATE FUNCTION {_q('check_personal_legacy_attribution_review')}() RETURNS trigger LANGUAGE plpgsql AS $$ "
        "DECLARE consent record; reviewer_active boolean; BEGIN "
        f"SELECT * INTO consent FROM {_q('personal_legacy_attribution_consents')} WHERE id = NEW.consent_id; "
        "IF consent.id IS NULL OR consent.source_key IS DISTINCT FROM NEW.source_key OR "
        "consent.dispatch_id IS DISTINCT FROM NEW.dispatch_id OR consent.attempt_key IS DISTINCT FROM NEW.attempt_key OR "
        "consent.tenant_id IS DISTINCT FROM NEW.tenant_id OR consent.account_id IS DISTINCT FROM NEW.account_id OR "
        "consent.consented_by_user_id IS DISTINCT FROM NEW.consented_by_user_id THEN "
        "RAISE EXCEPTION 'Reviewed legacy mapping differs from owner consent' USING ERRCODE = '23514'; END IF; "
        f"SELECT u.is_active AND r.codename = 'admin' INTO reviewer_active FROM {_q('users')} u "
        f"JOIN {_q('roles')} r ON r.id = u.role_id WHERE u.id = NEW.reviewed_by_user_id; "
        "IF reviewer_active IS DISTINCT FROM TRUE THEN "
        "RAISE EXCEPTION 'Legacy mapping reviewer must be active platform admin' USING ERRCODE = '23514'; END IF; "
        "RETURN NEW; END; $$"
    )
    op.execute(
        f"CREATE TRIGGER personal_legacy_attributions_validate BEFORE INSERT ON {_q('personal_legacy_attributions')} "
        f"FOR EACH ROW EXECUTE FUNCTION {_q('check_personal_legacy_attribution_review')}()"
    )
    _immutable("personal_legacy_attribution_consents")
    _immutable("personal_legacy_attributions")


def downgrade() -> None:
    raise RuntimeError("Legacy attribution evidence is retained; downgrade requires a reviewed preservation plan")
