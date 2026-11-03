"""P13-T05: extend closed audit codes without changing existing event semantics."""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0130"
down_revision = "0129"
branch_labels = None
depends_on = None

FROZEN_CODES = (
    ("calendar.event_create", "event", "operation_unconfirmed"),
    ("calendar.event_delete", "event", "operation_unconfirmed"),
    ("calendar.event_update", "event", "operation_unconfirmed"),
    ("commands.approve", "command", "operation_unconfirmed"),
    ("commands.create", "command", "operation_unconfirmed"),
    ("commands.execute", "command", "operation_unconfirmed"),
    ("comms.campaign_approve", "campaign", "operation_unconfirmed"),
    ("comms.campaign_create", "campaign", "operation_unconfirmed"),
    ("comms.campaign_send", "campaign", "operation_unconfirmed"),
    ("crm.contact_create", "contact", "operation_unconfirmed"),
    ("crm.contact_delete", "contact", "operation_unconfirmed"),
    ("crm.contact_update", "contact", "operation_unconfirmed"),
    ("documents.approve", "document", "operation_unconfirmed"),
    ("documents.create", "document", "operation_unconfirmed"),
    ("documents.export", "document", "operation_unconfirmed"),
    ("files.connector.fetch", "local_connector", "operation_unconfirmed"),
    ("files.connector.index_delete", "local_connector", "operation_unconfirmed"),
    ("files.connector.register", "local_connector", "operation_unconfirmed"),
    ("files.connector.revoke", "local_connector", "operation_unconfirmed"),
    ("files.connector.search", "local_connector", "operation_unconfirmed"),
    ("onec.command_approve", "command", "operation_unconfirmed"),
    ("onec.command_execute", "command", "operation_unconfirmed"),
    ("planner.task_complete", "task", "operation_unconfirmed"),
    ("planner.task_create", "task", "operation_unconfirmed"),
    ("planner.task_update", "task", "operation_unconfirmed"),
    ("scheduling.task_admit", "agent_task", "launch_unconfirmed"),
)


def _checks() -> dict[str, str]:
    actions = ", ".join(f"'{code}'" for code, _, _ in FROZEN_CODES)
    resources = " OR ".join(
        f"(action = '{code}' AND resource_kind = '{resource}')" for code, resource, _ in FROZEN_CODES
    )
    reasons = " OR ".join(f"(action = '{code}' AND reason_code = '{reason}')" for code, _, reason in FROZEN_CODES)
    return {
        "action": f"action IN ({actions})",
        "resource_kind": "(action = 'scheduling.task_admit' AND resource_kind IS NULL) OR "
        f"(resource_kind IS NOT NULL AND ({resources}))",
        # Preserve all legacy CHECK-valid rows; new actions reject SQL NULL reasons.
        "reason_code": "(action = 'scheduling.task_admit' AND ((outcome = 'succeeded' AND reason_code IS NULL) OR "
        "(outcome = 'unknown' AND reason_code = 'launch_unconfirmed'))) OR "
        "(action <> 'scheduling.task_admit' AND ((outcome = 'succeeded' AND reason_code IS NULL) OR "
        f"(outcome = 'unknown' AND reason_code IS NOT NULL AND ({reasons}))))",
    }


NEW_CHECKS = _checks()
OLD_CHECKS = {
    "action": "action = 'scheduling.task_admit'",
    "resource_kind": "resource_kind IS NULL OR resource_kind = 'agent_task'",
    "reason_code": "(outcome = 'succeeded' AND reason_code IS NULL) OR "
    "(outcome = 'unknown' AND reason_code = 'launch_unconfirmed')",
}


def _replace(checks: dict[str, str]) -> None:
    for suffix, expression in checks.items():
        name = f"ck_action_audit_events_{suffix}"
        op.drop_constraint(name, "action_audit_events", schema=settings.DB_SCHEMA, type_="check")
        op.create_check_constraint(name, "action_audit_events", expression, schema=settings.DB_SCHEMA)


def upgrade() -> None:
    _replace(NEW_CHECKS)


def downgrade() -> None:
    # Never delete immutable evidence to make an old schema fit. Fail before DDL.
    bind = op.get_bind()
    table = sa.table("action_audit_events", sa.column("action"), schema=settings.DB_SCHEMA)
    if bind.execute(
        sa.select(sa.literal(1)).select_from(table).where(table.c.action != "scheduling.task_admit").limit(1)
    ).first():
        raise ValueError("business_audit_downgrade_requires_expired_events")
    _replace(OLD_CHECKS)
