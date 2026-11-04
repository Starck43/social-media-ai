"""P14-T09: installation-wide trusted outbound hosts and their audit codes.

Revision ID: 0138
Revises: 0136

`trusted_outbound_hosts` has no `tenant_id` by the owner-authorized exception of
2026-11-04 (platform-wide technical row, no customer data). The audit CHECKs gain
`admin.trusted_host_add/remove`; the code list below is frozen and a test keeps it
equal to `app.services.audit_codes`.

Ordering note: P15-T02 owns `0137`. If `0137` lands on `dev` first, this file's
`down_revision` becomes `"0137"` (this file only; never edit another revision).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.config import settings

revision = "0138"
down_revision = "0136"
branch_labels = None
depends_on = None

HOST_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"

PREVIOUS_CODES = (
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
ADDED_CODES = (
    ("admin.trusted_host_add", "trusted_host", "operation_unconfirmed"),
    ("admin.trusted_host_remove", "trusted_host", "operation_unconfirmed"),
)


def _checks(codes) -> dict[str, str]:
    codes = sorted(codes)
    actions = ", ".join(f"'{code}'" for code, _, _ in codes)
    resources = " OR ".join(f"(action = '{code}' AND resource_kind = '{resource}')" for code, resource, _ in codes)
    reasons = " OR ".join(f"(action = '{code}' AND reason_code = '{reason}')" for code, _, reason in codes)
    return {
        "action": f"action IN ({actions})",
        "resource_kind": "(action = 'scheduling.task_admit' AND resource_kind IS NULL) OR "
        f"(resource_kind IS NOT NULL AND ({resources}))",
        "reason_code": "(action = 'scheduling.task_admit' AND ((outcome = 'succeeded' AND reason_code IS NULL) OR "
        "(outcome = 'unknown' AND reason_code = 'launch_unconfirmed'))) OR "
        "(action <> 'scheduling.task_admit' AND ((outcome = 'succeeded' AND reason_code IS NULL) OR "
        f"(outcome = 'unknown' AND reason_code IS NOT NULL AND ({reasons}))))",
    }


NEW_CHECKS = _checks(PREVIOUS_CODES + ADDED_CODES)
OLD_CHECKS = _checks(PREVIOUS_CODES)


def _replace(checks: dict[str, str]) -> None:
    for suffix, expression in checks.items():
        name = f"ck_action_audit_events_{suffix}"
        op.drop_constraint(name, "action_audit_events", schema=settings.DB_SCHEMA, type_="check")
        op.create_check_constraint(name, "action_audit_events", expression, schema=settings.DB_SCHEMA)


def upgrade() -> None:
    s = settings.DB_SCHEMA
    op.create_table(
        "trusted_outbound_hosts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("host", sa.String(253), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column(
            "authorized_addresses",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("note", sa.String(200), nullable=False, server_default=""),
        sa.Column(
            "created_by_actor_id",
            sa.Integer(),
            sa.ForeignKey(f"{s}.users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("host", name="uq_trusted_outbound_hosts_host"),
        sa.CheckConstraint(f"host ~ '{HOST_PATTERN}'", name="ck_trusted_outbound_hosts_host"),
        sa.CheckConstraint("kind IN ('public', 'private')", name="ck_trusted_outbound_hosts_kind"),
        sa.CheckConstraint(
            "jsonb_typeof(authorized_addresses) = 'array' AND ("
            "(kind = 'public' AND jsonb_array_length(authorized_addresses) = 0) OR "
            "(kind = 'private' AND jsonb_array_length(authorized_addresses) BETWEEN 1 AND 16))",
            name="ck_trusted_outbound_hosts_addresses",
        ),
        sa.CheckConstraint("length(note) <= 200", name="ck_trusted_outbound_hosts_note"),
        schema=s,
    )
    _replace(NEW_CHECKS)


def downgrade() -> None:
    # Never delete immutable evidence to make an old schema fit. Fail before DDL.
    bind = op.get_bind()
    table = sa.table("action_audit_events", sa.column("action"), schema=settings.DB_SCHEMA)
    added = [code for code, _, _ in ADDED_CODES]
    if bind.execute(sa.select(sa.literal(1)).select_from(table).where(table.c.action.in_(added)).limit(1)).first():
        raise ValueError("trusted_host_downgrade_requires_expired_events")
    _replace(OLD_CHECKS)
    op.drop_table("trusted_outbound_hosts", schema=settings.DB_SCHEMA)
