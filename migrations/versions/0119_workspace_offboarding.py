"""Add immutable platform evidence for successful workspace offboarding.

Revision ID: 0119
Revises: baseline_0118
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import context, op

from app.core.config import settings

revision = "0119"
down_revision = "baseline_0118"
branch_labels = None
depends_on = None

SCHEMA = settings.DB_SCHEMA
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
RESOURCE_KEY = "audit.workspace_offboarding"
MODEL_NAME = "workspaceoffboardingevent"
TABLE_NAME = "workspace_offboarding_events"


def _schema() -> str:
    if not _IDENTIFIER.fullmatch(SCHEMA) or len(SCHEMA) > 63:
        raise ValueError("Unsafe workspace-offboarding schema")
    return op.get_bind().dialect.identifier_preparer.quote_schema(SCHEMA)


def _add_registry() -> None:
    schema = _schema()
    action_type = f'{schema}."action_type"'
    op.get_bind().execute(
        sa.text(f"""
            INSERT INTO {schema}."model_types"
                (resource_key, model_name, table_name, description, is_managed, created_at, updated_at)
            VALUES
                (:resource_key, :model_name, :table_name,
                 'Immutable platform workspace offboarding evidence', true, now(), now())
            ON CONFLICT (resource_key) DO NOTHING
            """),
        {
            "resource_key": RESOURCE_KEY,
            "model_name": MODEL_NAME,
            "table_name": TABLE_NAME,
        },
    )
    op.get_bind().execute(
        sa.text(f"""
            INSERT INTO {schema}."permissions"
                (name, action_type, model_type_id, created_at, updated_at)
            SELECT seed.name, CAST(seed.action AS {action_type}), mt.id, now(), now()
              FROM {schema}."model_types" AS mt
              CROSS JOIN (VALUES
                  ('Workspace offboarding events: view', 'VIEW'),
                  ('Workspace offboarding events: export', 'EXPORT')
              ) AS seed(name, action)
             WHERE mt.resource_key = :resource_key
            ON CONFLICT (model_type_id, action_type) DO NOTHING
            """),
        {"resource_key": RESOURCE_KEY},
    )


def upgrade() -> None:
    schema = _schema()
    op.create_table(
        TABLE_NAME,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.String(32), nullable=False),
        sa.Column("workspace_id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("reason_code", sa.String(32), nullable=False),
        sa.Column("retention_policy", sa.String(24), nullable=False),
        sa.Column("expired_audit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("outcome", sa.String(16), nullable=False, server_default="succeeded"),
        sa.UniqueConstraint("event_id", name="uq_workspace_offboarding_events_event_id"),
        sa.UniqueConstraint("workspace_id", name="uq_workspace_offboarding_events_workspace_id"),
        sa.CheckConstraint("event_id ~ '^[0-9a-f]{32}$'", name="ck_workspace_offboarding_events_event_id"),
        sa.CheckConstraint(
            "workspace_id > 0 AND workspace_id <= 2147483647",
            name="ck_workspace_offboarding_events_workspace_id",
        ),
        sa.CheckConstraint("actor_id > 0 AND actor_id <= 2147483647", name="ck_workspace_offboarding_events_actor_id"),
        sa.CheckConstraint(
            "reason_code IN ('duplicate', 'owner_request', 'contract_ended', 'test_completed')",
            name="ck_workspace_offboarding_events_reason_code",
        ),
        sa.CheckConstraint(
            "retention_policy IN ('preserve', 'expire_elapsed')",
            name="ck_workspace_offboarding_events_retention_policy",
        ),
        sa.CheckConstraint("expired_audit_count >= 0", name="ck_workspace_offboarding_events_expired_audit_count"),
        sa.CheckConstraint("outcome = 'succeeded'", name="ck_workspace_offboarding_events_outcome"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workspace_offboarding_events_occurred_at_id",
        TABLE_NAME,
        ["occurred_at", "id"],
        schema=SCHEMA,
    )
    op.execute(
        f'CREATE OR REPLACE FUNCTION {schema}."guard_workspace_offboarding_mutation"() '
        "RETURNS trigger LANGUAGE plpgsql AS $guard$ "
        "BEGIN RAISE EXCEPTION 'Workspace offboarding evidence is immutable' "
        "USING ERRCODE = '55000'; END; $guard$"
    )
    op.execute(
        f"CREATE TRIGGER workspace_offboarding_events_immutable BEFORE UPDATE OR DELETE "
        f'ON {schema}."{TABLE_NAME}" FOR EACH ROW '
        f'EXECUTE FUNCTION {schema}."guard_workspace_offboarding_mutation"()'
    )
    op.execute(
        f"CREATE TRIGGER workspace_offboarding_events_no_truncate BEFORE TRUNCATE "
        f'ON {schema}."{TABLE_NAME}" FOR EACH STATEMENT '
        f'EXECUTE FUNCTION {schema}."guard_workspace_offboarding_mutation"()'
    )
    _add_registry()


def _test_teardown_allowed() -> bool:
    bind = op.get_bind()
    host = getattr(getattr(bind, "engine", None), "url", None)
    host = getattr(host, "host", None)
    if SCHEMA != "test_schema" or host not in {"localhost", "127.0.0.1", "::1"}:
        return False
    if context.get_x_argument(as_dictionary=True).get("p11_t29_test_teardown") != "1":
        return False
    return bind.execute(sa.text("SELECT current_database() = 'social_manager'")).scalar_one() is True


def downgrade() -> None:
    schema = _schema()
    events = f'{schema}."{TABLE_NAME}"'
    bind = op.get_bind()
    has_rows = bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {events})")).scalar_one()
    if has_rows and not _test_teardown_allowed():
        raise RuntimeError(
            "Workspace offboarding evidence is retained; non-empty downgrade is refused "
            "(test_schema teardown requires -x p11_t29_test_teardown=1)"
        )

    # The only row-erasing path is a controlled, owner-authorized disposable test-schema reset.
    if has_rows:
        op.execute(f"DROP TABLE {events}")
    else:
        op.drop_table(TABLE_NAME, schema=SCHEMA)
    op.execute(f'DROP FUNCTION IF EXISTS {schema}."guard_workspace_offboarding_mutation"()')

    # Remove only this resource's permission links and reference rows.
    op.get_bind().execute(
        sa.text(f"""
            DELETE FROM {schema}."role_permission" rp
             USING {schema}."permissions" p, {schema}."model_types" mt
             WHERE rp.permission_id = p.id AND p.model_type_id = mt.id
               AND mt.resource_key = :resource_key
               AND p.action_type::text IN ('VIEW', 'EXPORT')
            """),
        {"resource_key": RESOURCE_KEY},
    )
    op.get_bind().execute(
        sa.text(f"""
            DELETE FROM {schema}."workspace_role_permission" wrp
             USING {schema}."permissions" p, {schema}."model_types" mt
             WHERE wrp.permission_id = p.id AND p.model_type_id = mt.id
               AND mt.resource_key = :resource_key
               AND p.action_type::text IN ('VIEW', 'EXPORT')
            """),
        {"resource_key": RESOURCE_KEY},
    )
    op.get_bind().execute(
        sa.text(f"""
            DELETE FROM {schema}."permissions" p
             USING {schema}."model_types" mt
             WHERE p.model_type_id = mt.id AND mt.resource_key = :resource_key
               AND p.action_type::text IN ('VIEW', 'EXPORT')
            """),
        {"resource_key": RESOURCE_KEY},
    )
    op.get_bind().execute(
        sa.text(f'DELETE FROM {schema}."model_types" WHERE resource_key = :resource_key'),
        {"resource_key": RESOURCE_KEY},
    )
