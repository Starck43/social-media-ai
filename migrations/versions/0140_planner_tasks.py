"""P16-T01 planner tasks with text RRULE and source authority from day one.

Revision ID: 0140
Revises: 0139
One migration owns the table, recurrence_source, PII marker/trigger and explicit
planner permissions. No expanded occurrences, external IDs, reminders or sends.
"""

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0140"
down_revision = "0139"
branch_labels = None
depends_on = None

WORKSPACE_ROLE_PRESETS = {
    "owner": ("VIEW", "CREATE", "UPDATE", "DELETE"),
    "admin": ("VIEW", "CREATE", "UPDATE", "DELETE"),
    "manager": ("VIEW", "CREATE", "UPDATE", "DELETE"),
    "analyst": ("VIEW", "CREATE", "UPDATE"),
    "viewer": ("VIEW",),
}


def _registry(add: bool) -> None:
    bind = op.get_bind()
    schema = bind.dialect.identifier_preparer.quote_schema(settings.DB_SCHEMA)
    resource_key = "planner.task"
    if add:
        bind.execute(
            sa.text(
                f"""
                INSERT INTO {schema}.model_types
                    (resource_key, model_name, table_name, description, is_managed, created_at, updated_at)
                VALUES (:resource_key, 'plannertask', 'planner_tasks',
                        'Tenant planner task', true, now(), now())
                ON CONFLICT (resource_key) DO NOTHING
                """
            ),
            {"resource_key": resource_key},
        )
        for action in ("VIEW", "CREATE", "UPDATE", "DELETE"):
            bind.execute(
                sa.text(
                    f"""
                    INSERT INTO {schema}.permissions (name, action_type, model_type_id, created_at, updated_at)
                    SELECT :name, CAST(:action AS {schema}.action_type), id, now(), now()
                    FROM {schema}.model_types WHERE resource_key = :resource_key
                    ON CONFLICT (model_type_id, action_type) DO NOTHING
                    """
                ),
                {"name": "Planner task: " + action.lower(), "action": action, "resource_key": resource_key},
            )
        for codename, actions in WORKSPACE_ROLE_PRESETS.items():
            for action in actions:
                bind.execute(
                    sa.text(
                        f"""
                        INSERT INTO {schema}.workspace_role_permission (role_id, permission_id)
                        SELECT wr.id, p.id
                        FROM {schema}.workspace_roles wr
                        CROSS JOIN {schema}.permissions p
                        JOIN {schema}.model_types m ON p.model_type_id = m.id
                        WHERE wr.codename = :codename
                          AND m.resource_key = :resource_key
                          AND p.action_type = CAST(:action AS {schema}.action_type)
                        ON CONFLICT (role_id, permission_id) DO NOTHING
                        """
                    ),
                    {"codename": codename, "resource_key": resource_key, "action": action},
                )
        return

    for link in ("role_permission", "workspace_role_permission"):
        bind.execute(
            sa.text(
                f"""
                DELETE FROM {schema}.{link} l USING {schema}.permissions p, {schema}.model_types m
                WHERE l.permission_id = p.id AND p.model_type_id = m.id AND m.resource_key = :resource_key
                """
            ),
            {"resource_key": resource_key},
        )
    bind.execute(
        sa.text(
            f"""
            DELETE FROM {schema}.permissions p USING {schema}.model_types m
            WHERE p.model_type_id = m.id AND m.resource_key = :resource_key
            """
        ),
        {"resource_key": resource_key},
    )
    bind.execute(
        sa.text(f"DELETE FROM {schema}.model_types WHERE resource_key = :resource_key"),
        {"resource_key": resource_key},
    )


def upgrade() -> None:
    s = settings.DB_SCHEMA
    op.create_table(
        "planner_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey(f"{s}.tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey(f"{s}.users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assignee_id", sa.Integer(), sa.ForeignKey(f"{s}.users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title_encrypted", sa.String(), nullable=False),
        sa.Column("description_encrypted", sa.String(), nullable=True),
        sa.Column("series_template_encrypted", sa.String(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("timezone", sa.String(100), nullable=False),
        sa.Column("priority", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("contact_id", sa.Integer(), sa.ForeignKey(f"{s}.contacts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("series_id", sa.Integer(), nullable=True),
        sa.Column("previous_occurrence_id", sa.Integer(), nullable=True),
        sa.Column("occurrence_key", sa.String(64), nullable=False),
        sa.Column("occurrence_index", sa.Integer(), nullable=False),
        sa.Column("recurrence", sa.Text(), nullable=True),
        sa.Column("recurrence_source", sa.String(100), nullable=False, server_default="planner"),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("pii_updated_at", sa.BigInteger(), nullable=False, server_default="1"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_planner_task_tenant"),
        sa.UniqueConstraint("tenant_id", "series_id", "occurrence_key", name="uq_planner_occurrence"),
        sa.UniqueConstraint("tenant_id", "previous_occurrence_id", name="uq_planner_successor"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "series_id"], [f"{s}.planner_tasks.tenant_id", f"{s}.planner_tasks.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "parent_id"], [f"{s}.planner_tasks.tenant_id", f"{s}.planner_tasks.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "previous_occurrence_id"],
            [f"{s}.planner_tasks.tenant_id", f"{s}.planner_tasks.id"],
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        # Global SET NULL above preserves the task on contact erasure; deferred
        # composite validation fences the original link by workspace (PG14+).
        sa.ForeignKeyConstraint(
            ["tenant_id", "contact_id"],
            [f"{s}.contacts.tenant_id", f"{s}.contacts.id"],
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint("priority IN ('low','medium','high','critical')", name="ck_planner_priority"),
        sa.CheckConstraint("status IN ('todo','in_progress','done','cancelled')", name="ck_planner_status"),
        sa.CheckConstraint("due_at IS NULL OR due_date IS NULL", name="ck_planner_deadline"),
        sa.CheckConstraint(
            "recurrence IS NULL OR due_at IS NOT NULL OR due_date IS NOT NULL", name="ck_planner_recurrence_due"
        ),
        sa.CheckConstraint("version > 0 AND occurrence_index >= 0", name="ck_planner_versions"),
        sa.CheckConstraint("parent_id IS NULL OR parent_id != id", name="ck_planner_parent"),
        sa.CheckConstraint("(status = 'done') = (completed_at IS NOT NULL)", name="ck_planner_completion"),
        schema=s,
    )
    op.create_index(
        "ix_planner_workspace_due", "planner_tasks", ["tenant_id", "status", "due_at", "due_date"], schema=s
    )
    schema = op.get_bind().dialect.identifier_preparer.quote_schema(s)
    op.execute(
        f"CREATE TRIGGER contact_pii_marker BEFORE INSERT OR UPDATE ON {schema}.planner_tasks FOR EACH ROW EXECUTE FUNCTION {schema}.bump_contact_pii_marker()"
    )
    _registry(add=True)


def downgrade() -> None:
    _registry(add=False)
    # The table owns its trigger and indexes; dropping it removes all three.
    op.drop_table("planner_tasks", schema=settings.DB_SCHEMA)
