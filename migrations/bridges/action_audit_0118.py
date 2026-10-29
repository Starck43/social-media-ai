"""Add tenant-scoped action audit evidence and support permissions.

Revision ID: 0118
Revises: 0117
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0118"
down_revision = "0117"
branch_labels = None
depends_on = None
SCHEMA = settings.DB_SCHEMA


def upgrade():
    op.create_table(
        "action_audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.String(32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("actor_kind", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.String(64), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("resource_kind", sa.String(40), nullable=True),
        sa.Column("resource_id", sa.String(128), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=True),
        sa.Column("request_id", sa.String(128), nullable=True),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("receipt_ref", sa.String(160), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            [f"{SCHEMA}.tenants.id"],
            name="fk_action_audit_events_tenant",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("event_id", name="uq_action_audit_events_event_id"),
        sa.CheckConstraint(
            "event_id ~ '^[0-9a-f]{32}$'",
            name="ck_action_audit_events_event_id",
        ),
        sa.CheckConstraint(
            "actor_kind IN ('user', 'service', 'scheduler')",
            name="ck_action_audit_events_actor_kind",
        ),
        sa.CheckConstraint(
            "outcome IN ('succeeded', 'unknown')",
            name="ck_action_audit_events_outcome",
        ),
        sa.CheckConstraint(
            "action = 'scheduling.task_admit'",
            name="ck_action_audit_events_action",
        ),
        sa.CheckConstraint(
            "(actor_kind = 'user' AND actor_id ~ '^[1-9][0-9]{0,9}$') OR "
            "(actor_kind = 'service' AND actor_id = 'task_runner') OR "
            "(actor_kind = 'scheduler' AND actor_id = 'scheduler')",
            name="ck_action_audit_events_actor_id",
        ),
        sa.CheckConstraint(
            "resource_kind IS NULL OR resource_kind = 'agent_task'",
            name="ck_action_audit_events_resource_kind",
        ),
        sa.CheckConstraint(
            "resource_id IS NULL OR resource_id ~ '^[1-9][0-9]{0,9}$'",
            name="ck_action_audit_events_resource_id",
        ),
        sa.CheckConstraint(
            "(outcome = 'succeeded' AND reason_code IS NULL) OR "
            "(outcome = 'unknown' AND reason_code = 'launch_unconfirmed')",
            name="ck_action_audit_events_reason_code",
        ),
        sa.CheckConstraint("request_id IS NULL", name="ck_action_audit_events_request_id"),
        sa.CheckConstraint("receipt_ref IS NULL", name="ck_action_audit_events_receipt_ref"),
        sa.CheckConstraint(
            "job_id IS NULL OR (job_id > 0 AND job_id <= 2147483647)",
            name="ck_action_audit_events_job_id",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_action_audit_events_tenant_time_id",
        "action_audit_events",
        ["tenant_id", "occurred_at", "id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_action_audit_events_tenant_outcome_time",
        "action_audit_events",
        ["tenant_id", "outcome", "occurred_at"],
        schema=SCHEMA,
    )

    schema = op.get_bind().dialect.identifier_preparer.quote_schema(SCHEMA)
    op.execute(
        f"CREATE FUNCTION {schema}.guard_action_audit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ "
        "BEGIN "
        "IF TG_OP = 'DELETE' AND current_setting('app.action_audit_retention', true) = 'on' THEN "
        "RETURN OLD; END IF; "
        "RAISE EXCEPTION 'Action audit evidence is immutable outside retention cleanup' "
        "USING ERRCODE = '55000'; END; $$"
    )
    op.execute(
        f"CREATE TRIGGER action_audit_events_immutable BEFORE UPDATE OR DELETE "
        f"ON {schema}.action_audit_events FOR EACH ROW "
        f"EXECUTE FUNCTION {schema}.guard_action_audit_mutation()"
    )
    op.execute(
        f"CREATE TRIGGER action_audit_events_no_truncate BEFORE TRUNCATE "
        f"ON {schema}.action_audit_events FOR EACH STATEMENT "
        f"EXECUTE FUNCTION {schema}.guard_action_audit_mutation()"
    )

    model_types = f"{schema}.model_types"
    permissions = f"{schema}.permissions"
    workspace_roles = f"{schema}.workspace_roles"
    role_permissions = f"{schema}.workspace_role_permission"
    action_type = f"{schema}.action_type"
    op.execute(
        sa.text(
            f"""
            INSERT INTO {model_types}
                (model_name, table_name, description, is_managed, resource_key, created_at, updated_at)
            VALUES
                ('actionauditevent', 'action_audit_events',
                 'Tenant-scoped action audit evidence', true, 'audit.action_audit', now(), now())
            ON CONFLICT (resource_key) DO NOTHING
        """
        )
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO {permissions}
                (name, action_type, model_type_id, created_at, updated_at)
            SELECT seed.name, CAST(seed.action AS {action_type}), mt.id, now(), now()
              FROM {model_types} AS mt
              CROSS JOIN (VALUES
                  ('Action audit events: view', 'VIEW'),
                  ('Action audit events: export', 'EXPORT')
              ) AS seed(name, action)
             WHERE mt.resource_key = 'audit.action_audit'
            ON CONFLICT (model_type_id, action_type) DO NOTHING
        """
        )
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO {role_permissions} (role_id, permission_id)
            SELECT wr.id, p.id
              FROM {workspace_roles} AS wr
              JOIN {model_types} AS mt ON mt.resource_key = 'audit.action_audit'
              JOIN {permissions} AS p ON p.model_type_id = mt.id
             WHERE wr.codename IN ('owner', 'admin')
               AND p.action_type::text IN ('VIEW', 'EXPORT')
            ON CONFLICT DO NOTHING
        """
        )
    )


def downgrade():
    raise RuntimeError(
        "Action audit evidence is retained; downgrade requires an explicit preservation and permission-removal plan"
    )
