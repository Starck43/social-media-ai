"""Allow trusted platform operators to initiate personal-billed tasks.

Revision ID: 0103
Revises: 0102

The application permits an active platform operator or administrator to initiate
a workspace task when they have no active workspace membership. Keep payer
membership and every other dispatch validation unchanged.
"""

from __future__ import annotations

from alembic import op

from app.core.config import settings

revision = "0103"
down_revision = "0102"
branch_labels = None
depends_on = None
SCHEMA = settings.DB_SCHEMA


def _q(name: str) -> str:
    prep = op.get_bind().dialect.identifier_preparer
    return f"{prep.quote_schema(SCHEMA)}.{prep.quote(name)}"


def _replace_validator(*, allow_platform_operator: bool) -> None:
    initiator_check = (
        f"SELECT EXISTS (SELECT 1 FROM {_q('users')} u JOIN {_q('roles')} r ON r.id = u.role_id "
        "WHERE u.id = NEW.initiated_by_user_id AND u.is_active AND r.codename IN ('operator', 'admin')) "
        "INTO operator_active; "
        "IF member_active IS DISTINCT FROM TRUE AND operator_active IS DISTINCT FROM TRUE THEN "
        "RAISE EXCEPTION 'Task initiator workspace membership is inactive' USING ERRCODE = '23514'; END IF; "
        if allow_platform_operator
        else "IF member_active IS DISTINCT FROM TRUE THEN "
        "RAISE EXCEPTION 'Task initiator workspace membership is inactive' USING ERRCODE = '23514'; END IF; "
    )
    operator_local = "operator_active boolean; " if allow_platform_operator else ""
    op.execute(
        f"CREATE OR REPLACE FUNCTION {_q('check_personal_llm_dispatch_account')}() RETURNS trigger LANGUAGE plpgsql AS $$ "
        "DECLARE valid boolean; task_tenant integer; task_payer integer; initiator_active boolean; member_active boolean; admin_active boolean; "
        f"{operator_local}"
        "receipt_tenant integer; receipt_model integer; receipt_provider integer; BEGIN "
        f"SELECT tenant_id, llm_model_row_id, llm_provider_row_id INTO receipt_tenant, receipt_model, receipt_provider "
        f"FROM {_q('llm_dispatch_receipts')} WHERE dispatch_id = NEW.dispatch_id; "
        "IF receipt_tenant IS DISTINCT FROM NEW.tenant_id OR receipt_model IS DISTINCT FROM NEW.llm_model_row_id "
        "OR receipt_provider IS DISTINCT FROM NEW.llm_provider_row_id THEN "
        "RAISE EXCEPTION 'Personal dispatch must match its legacy receipt identity' USING ERRCODE = '23514'; END IF; "
        f"SELECT a.is_active AND a.configured_at IS NOT NULL AND a.user_id = NEW.payer_user_id AND u.is_active "
        f"INTO valid FROM {_q('personal_billing_accounts')} a "
        f"JOIN {_q('users')} u ON u.id = a.user_id WHERE a.id = NEW.account_id; "
        "IF valid IS DISTINCT FROM TRUE THEN "
        "RAISE EXCEPTION 'Dispatch payer account differs or is inactive' USING ERRCODE = '23514'; END IF; "
        "IF NEW.initiated_by_user_id IS NOT NULL THEN "
        f"SELECT is_active INTO initiator_active FROM {_q('users')} WHERE id = NEW.initiated_by_user_id; "
        "IF initiator_active IS DISTINCT FROM TRUE THEN "
        "RAISE EXCEPTION 'Dispatch initiator is inactive' USING ERRCODE = '23514'; END IF; END IF; "
        "IF NEW.agent_task_id IS NULL AND NEW.initiated_by_user_id IS DISTINCT FROM NEW.payer_user_id THEN "
        "RAISE EXCEPTION 'Non-task dispatch initiator must be the payer' USING ERRCODE = '23514'; END IF; "
        "IF NEW.agent_task_id IS NULL AND NEW.purpose = 'admin_probe' THEN "
        f"SELECT EXISTS (SELECT 1 FROM {_q('users')} u JOIN {_q('roles')} r ON r.id = u.role_id "
        "WHERE u.id = NEW.payer_user_id AND u.is_active AND r.codename = 'admin') INTO admin_active; "
        "IF admin_active IS DISTINCT FROM TRUE THEN RAISE EXCEPTION 'Admin probe payer must be active platform admin' USING ERRCODE = '23514'; END IF; "
        "END IF; "
        "IF NEW.agent_task_id IS NULL AND NEW.purpose <> 'admin_probe' THEN "
        f"SELECT EXISTS (SELECT 1 FROM {_q('tenant_users')} m WHERE m.tenant_id = NEW.tenant_id "
        "AND m.user_id = NEW.payer_user_id AND m.is_active) INTO member_active; "
        "IF member_active IS DISTINCT FROM TRUE THEN "
        "RAISE EXCEPTION 'Non-task payer workspace membership is inactive' USING ERRCODE = '23514'; END IF; END IF; "
        "IF NEW.agent_task_id IS NOT NULL THEN "
        f"SELECT tenant_id INTO task_tenant FROM {_q('agent_tasks')} WHERE id = NEW.agent_task_id; "
        "IF task_tenant IS DISTINCT FROM NEW.tenant_id THEN "
        "RAISE EXCEPTION 'Dispatch task workspace differs' USING ERRCODE = '23503'; END IF; "
        f"SELECT payer_user_id INTO task_payer FROM {_q('task_billing_payers')} "
        "WHERE agent_task_id = NEW.agent_task_id AND tenant_id = NEW.tenant_id ORDER BY consent_version DESC LIMIT 1; "
        "IF task_payer IS DISTINCT FROM NEW.payer_user_id THEN "
        "RAISE EXCEPTION 'Dispatch task payer lacks current consent' USING ERRCODE = '23514'; END IF; "
        f"SELECT EXISTS (SELECT 1 FROM {_q('tenant_users')} m WHERE m.tenant_id = NEW.tenant_id "
        "AND m.user_id = NEW.payer_user_id AND m.is_active) INTO member_active; "
        "IF member_active IS DISTINCT FROM TRUE THEN "
        "RAISE EXCEPTION 'Task payer workspace membership is inactive' USING ERRCODE = '23514'; END IF; "
        "IF NEW.initiated_by_user_id IS NOT NULL THEN "
        f"SELECT EXISTS (SELECT 1 FROM {_q('tenant_users')} m WHERE m.tenant_id = NEW.tenant_id "
        "AND m.user_id = NEW.initiated_by_user_id AND m.is_active) INTO member_active; "
        f"{initiator_check}END IF; END IF; "
        "RETURN NEW; END; $$"
    )


def upgrade() -> None:
    _replace_validator(allow_platform_operator=True)


def downgrade() -> None:
    """Restore the exact dispatch validator installed by revision 0097."""
    _replace_validator(allow_platform_operator=False)
