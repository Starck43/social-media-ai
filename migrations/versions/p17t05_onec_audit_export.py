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

# Capability-only catalog entries: no provider data tables are created.
CAPABILITY_RESOURCES = (
    ("onec.data", "onecdata", "onec_data", ("VIEW", "EXPORT")),
    ("onec.command", "oneccommand", "onec_command", ("CREATE", "CONFIGURE")),
)
CAPABILITY_GRANTS = {
    "owner": (
        ("onec.data", "VIEW"),
        ("onec.data", "EXPORT"),
        ("onec.command", "CREATE"),
        ("onec.command", "CONFIGURE"),
    ),
    "admin": (
        ("onec.data", "VIEW"),
        ("onec.data", "EXPORT"),
        ("onec.command", "CREATE"),
        ("onec.command", "CONFIGURE"),
    ),
    "manager": (("onec.data", "VIEW"), ("onec.command", "CREATE")),
    "analyst": (("onec.data", "VIEW"),),
    "viewer": (),
}


def _catalog(*, add: bool) -> None:
    bind = op.get_bind()
    schema = bind.dialect.identifier_preparer.quote_schema(settings.DB_SCHEMA)
    if add:
        for key, model, table, actions in CAPABILITY_RESOURCES:
            bind.execute(
                sa.text(f"""INSERT INTO {schema}.model_types
                (resource_key, model_name, table_name, description, is_managed, created_at, updated_at)
                VALUES (:key, :model, :table, 'OneC workspace capability', true, now(), now())"""),
                {"key": key, "model": model, "table": table},
            )
            for action in actions:
                bind.execute(
                    sa.text(
                        f"""INSERT INTO {schema}.permissions (name, action_type, model_type_id, created_at, updated_at)
                    SELECT :name, CAST(:action AS {schema}.action_type), id, now(), now()
                    FROM {schema}.model_types WHERE resource_key = :key"""
                    ),
                    {"key": key, "action": action, "name": f"{key}: {action.lower()}"},
                )
        for role, pairs in CAPABILITY_GRANTS.items():
            for key, action in pairs:
                bind.execute(
                    sa.text(f"""INSERT INTO {schema}.workspace_role_permission (role_id, permission_id)
                    SELECT wr.id, p.id FROM {schema}.workspace_roles wr
                    JOIN {schema}.permissions p ON p.action_type = CAST(:action AS {schema}.action_type)
                    JOIN {schema}.model_types mt ON mt.id = p.model_type_id
                    WHERE wr.codename = :role AND mt.resource_key = :key"""),
                    {"role": role, "key": key, "action": action},
                )
    else:
        for key, _model, _table, _actions in CAPABILITY_RESOURCES:
            for link in ("role_permission", "workspace_role_permission"):
                bind.execute(
                    sa.text(f"""DELETE FROM {schema}.{link} l USING {schema}.permissions p, {schema}.model_types m
                    WHERE l.permission_id = p.id AND p.model_type_id = m.id AND m.resource_key = :key"""),
                    {"key": key},
                )
            bind.execute(
                sa.text(f"""DELETE FROM {schema}.permissions p USING {schema}.model_types m
                WHERE p.model_type_id = m.id AND m.resource_key = :key"""),
                {"key": key},
            )
            bind.execute(sa.text(f"DELETE FROM {schema}.model_types WHERE resource_key = :key"), {"key": key})


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
    _catalog(add=True)


def downgrade() -> None:
    bind = op.get_bind()
    schema = bind.dialect.identifier_preparer.quote_schema(settings.DB_SCHEMA)
    # Prevent a concurrent export between the refusal check and narrower CHECKs.
    bind.execute(sa.text(f"LOCK TABLE {schema}.action_audit_events IN ACCESS EXCLUSIVE MODE"))
    table = sa.table("action_audit_events", sa.column("action"), schema=settings.DB_SCHEMA)
    if (
        op.get_bind()
        .execute(sa.select(sa.literal(1)).select_from(table).where(table.c.action == "onec.audit_export").limit(1))
        .first()
    ):
        raise ValueError("onec_audit_downgrade_requires_expired_events")
    _replace(checks(export=False))
    _catalog(add=False)
