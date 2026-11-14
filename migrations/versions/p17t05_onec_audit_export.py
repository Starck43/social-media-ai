"""P17-T05: allow audit export evidence without changing write-command outcomes."""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "p17t05_onec_audit_export"
down_revision = "0155"
branch_labels = None
depends_on = None

# Frozen accepted registry; never derive historical DDL from live application code.
_COMMAND_NAMESPACES = {
    "crm": ("contact", ("contact_create", "contact_update", "contact_delete")),
    "comms": ("campaign", ("campaign_create", "campaign_approve", "campaign_send")),
    "planner": ("task", ("task_create", "task_update", "task_complete")),
    "calendar": ("event", ("event_create", "event_update", "event_delete")),
    "onec": ("command", ("command_approve", "command_execute")),
    "files.connector": ("local_connector", ("register", "revoke", "search", "fetch", "index_delete")),
    "documents": ("document", ("create", "approve", "export")),
    "commands": ("command", ("create", "approve", "execute")),
}


def checks(*, export: bool) -> dict[str, str]:
    codes = {"scheduling.task_admit": ("agent_task", "launch_unconfirmed")}
    for namespace, (resource, operations) in _COMMAND_NAMESPACES.items():
        for operation in operations:
            codes[f"{namespace}.{operation}"] = (resource, "operation_unconfirmed")
    if export:
        codes["onec.audit_export"] = ("workspace", "operation_unconfirmed")
    actions = ", ".join(f"'{code}'" for code in sorted(codes))
    resources = " OR ".join(
        f"(action = '{code}' AND resource_kind = '{resource}')" for code, (resource, _) in sorted(codes.items())
    )
    reasons = " OR ".join(
        f"(action = '{code}' AND reason_code = '{reason}')" for code, (_, reason) in sorted(codes.items())
    )
    return {
        "action": f"action IN ({actions})",
        "resource_kind": "(action = 'scheduling.task_admit' AND resource_kind IS NULL) OR "
        f"(resource_kind IS NOT NULL AND ({resources}))",
        "reason_code": "(action = 'scheduling.task_admit' AND ((outcome = 'succeeded' AND reason_code IS NULL) OR "
        "(outcome = 'unknown' AND reason_code = 'launch_unconfirmed'))) OR "
        "(action <> 'scheduling.task_admit' AND ((outcome = 'succeeded' AND reason_code IS NULL) OR "
        f"(outcome = 'unknown' AND reason_code IS NOT NULL AND ({reasons}))))",
    }


def _replace(values: dict[str, str]) -> None:
    for suffix, expression in values.items():
        name = f"ck_action_audit_events_{suffix}"
        op.drop_constraint(name, "action_audit_events", schema=settings.DB_SCHEMA, type_="check")
        op.create_check_constraint(name, "action_audit_events", expression, schema=settings.DB_SCHEMA)


def upgrade() -> None:
    _replace(checks(export=True))


def downgrade() -> None:
    table = sa.table("action_audit_events", sa.column("action"), schema=settings.DB_SCHEMA)
    if (
        op.get_bind()
        .execute(sa.select(sa.literal(1)).select_from(table).where(table.c.action == "onec.audit_export").limit(1))
        .first()
    ):
        raise ValueError("onec_audit_downgrade_requires_expired_events")
    _replace(checks(export=False))
