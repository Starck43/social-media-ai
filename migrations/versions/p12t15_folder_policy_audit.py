"""P12-T15: allow restrictive folder policy audit events; no provider writes."""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "p12t15_policy_audit"
down_revision = "0157"
branch_labels = None
depends_on = None

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


def checks(*, policy: bool) -> dict[str, str]:
    codes = {"scheduling.task_admit": ("agent_task", "launch_unconfirmed")}
    for namespace, (resource, operations) in _COMMAND_NAMESPACES.items():
        for operation in operations:
            codes[f"{namespace}.{operation}"] = (resource, "operation_unconfirmed")
    codes["onec.audit_export"] = ("workspace", "operation_unconfirmed")
    if policy:
        codes["files.policy_update"] = ("workspace_connection", "operation_unconfirmed")
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
    _replace(checks(policy=True))


def downgrade() -> None:
    bind = op.get_bind()
    schema = bind.dialect.identifier_preparer.quote_schema(settings.DB_SCHEMA)
    bind.execute(sa.text(f"LOCK TABLE {schema}.action_audit_events IN ACCESS EXCLUSIVE MODE"))
    table = sa.table("action_audit_events", sa.column("action"), schema=settings.DB_SCHEMA)
    if bind.execute(
        sa.select(sa.literal(1)).select_from(table).where(table.c.action == "files.policy_update").limit(1)
    ).first():
        raise ValueError("folder_policy_downgrade_requires_expired_events")
    _replace(checks(policy=False))
