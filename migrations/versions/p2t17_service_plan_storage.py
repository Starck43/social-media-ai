"""P2-T17 dormant user tariff storage, without backfill or runtime cutover.

The integrator assigns the numeric revision at merge. No policy seeds or economic
ledger are introduced. Downgrade refuses retained target data/sponsorship.
"""

import sqlalchemy as sa
from alembic import context, op

from app.core.config import settings

revision = "p2t17_service_plan_storage"
down_revision = "p17t08_onec_history_status"
branch_labels = None
depends_on = None


def upgrade():
    schema = settings.DB_SCHEMA
    op.create_table(
        "service_plan_policies",
        sa.Column("plan_code", sa.String(20), primary_key=True),
        sa.Column("policy_version", sa.Integer, primary_key=True),
        sa.Column("module_grants", sa.JSON, nullable=False),
        sa.Column("daily_quotas", sa.JSON, nullable=False),
        sa.Column("resource_limits", sa.JSON, nullable=False),
        sa.Column("owned_workspace_limit_configured", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("max_owned_workspaces", sa.BigInteger, nullable=True),
        sa.Column("token_policy_configured", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("token_unit", sa.String(80), nullable=True),
        sa.Column("token_period", sa.String(80), nullable=True),
        sa.Column("token_limit", sa.BigInteger, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("plan_code IN ('starter', 'pro', 'business')", name="ck_service_plan_policy_code"),
        sa.CheckConstraint("policy_version > 0", name="ck_service_plan_policy_version"),
        sa.CheckConstraint("json_typeof(module_grants) = 'array'", name="ck_service_plan_policy_grants"),
        sa.CheckConstraint("json_typeof(daily_quotas) = 'object'", name="ck_service_plan_policy_quotas"),
        sa.CheckConstraint("json_typeof(resource_limits) = 'object'", name="ck_service_plan_policy_resources"),
        sa.CheckConstraint(
            "(owned_workspace_limit_configured OR max_owned_workspaces IS NULL) "
            "AND (max_owned_workspaces IS NULL OR max_owned_workspaces >= 0)",
            name="ck_service_plan_policy_workspaces",
        ),
        sa.CheckConstraint(
            "(NOT token_policy_configured AND token_unit IS NULL AND token_period IS NULL AND token_limit IS NULL) "
            "OR (token_policy_configured AND token_unit IS NOT NULL AND length(trim(token_unit)) > 0 "
            "AND token_period IS NOT NULL AND length(trim(token_period)) > 0 "
            "AND (token_limit IS NULL OR token_limit >= 0))",
            name="ck_service_plan_policy_tokens",
        ),
        schema=schema,
    )
    op.create_table(
        "user_service_plans",
        sa.Column("user_id", sa.Integer, sa.ForeignKey(f"{schema}.users.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("plan_code", sa.String(20), nullable=False),
        sa.Column("policy_version", sa.Integer, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("plan_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["plan_code", "policy_version"],
            [f"{schema}.service_plan_policies.plan_code", f"{schema}.service_plan_policies.policy_version"],
            name="fk_user_service_plan_policy",
            ondelete="RESTRICT",
        ),
        schema=schema,
    )
    op.add_column("tenants", sa.Column("plan_holder_user_id", sa.Integer, nullable=True), schema=schema)
    op.create_foreign_key(
        "fk_tenants_plan_holder_user",
        "tenants",
        "users",
        ["plan_holder_user_id"],
        ["id"],
        source_schema=schema,
        referent_schema=schema,
        ondelete="RESTRICT",
    )
    op.create_index("ix_tenants_plan_holder_user_id", "tenants", ["plan_holder_user_id"], schema=schema)
    op.create_table(
        "service_plan_backfill_resolutions",
        sa.Column("tenant_id", sa.Integer, primary_key=True),
        sa.Column("holder_user_id", sa.Integer, nullable=False),
        sa.Column("owner_user_ids", sa.JSON, nullable=False),
        sa.Column("legacy_plan", sa.String(20), nullable=False),
        sa.Column("legacy_plan_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("plan_code", sa.String(20), nullable=False),
        sa.Column("policy_version", sa.Integer, nullable=False),
        sa.Column("plan_is_active", sa.Boolean, nullable=False),
        sa.Column("plan_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("plan_decision_ref", sa.String(250), nullable=True),
        sa.Column("operator_user_id", sa.Integer, nullable=False),
        sa.Column("decision_ref", sa.String(250), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("operator_user_id > 0", name="ck_service_plan_backfill_operator"),
        sa.CheckConstraint("length(trim(decision_ref)) > 0", name="ck_service_plan_backfill_decision"),
        sa.CheckConstraint("json_typeof(owner_user_ids) = 'array'", name="ck_service_plan_backfill_owners"),
        sa.ForeignKeyConstraint(
            ["plan_code", "policy_version"],
            [f"{schema}.service_plan_policies.plan_code", f"{schema}.service_plan_policies.policy_version"],
            name="fk_service_plan_backfill_policy",
            ondelete="RESTRICT",
        ),
        schema=schema,
    )
    quoted = op.get_context().dialect.identifier_preparer.quote_schema(schema)
    op.add_column("entitlement_usage", sa.Column("plan_holder_user_id", sa.Integer, nullable=True), schema=schema)
    op.add_column("entitlement_usage", sa.Column("plan_policy_version", sa.Integer, nullable=True), schema=schema)
    op.create_check_constraint(
        "ck_entitlement_usage_holder_policy",
        "entitlement_usage",
        "(plan_holder_user_id IS NULL AND plan_policy_version IS NULL) OR "
        "(plan_holder_user_id IS NOT NULL AND plan_holder_user_id > 0 "
        "AND plan_policy_version IS NOT NULL AND plan_policy_version > 0)",
        schema=schema,
    )
    op.execute(sa.text(f"""
        CREATE FUNCTION {quoted}.service_plan_policy_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'Service plan policy versions are immutable; insert a new version';
        END $$
    """))
    op.execute(sa.text(f"""
        CREATE TRIGGER service_plan_policy_immutable BEFORE UPDATE OR DELETE
        ON {quoted}.service_plan_policies FOR EACH ROW
        EXECUTE FUNCTION {quoted}.service_plan_policy_immutable()
    """))
    op.execute(sa.text(f"""
        CREATE TRIGGER service_plan_policy_no_truncate BEFORE TRUNCATE
        ON {quoted}.service_plan_policies FOR EACH STATEMENT
        EXECUTE FUNCTION {quoted}.service_plan_policy_immutable()
    """))
    for event, level in (("UPDATE OR DELETE", "ROW"), ("TRUNCATE", "STATEMENT")):
        trigger = "service_plan_backfill_immutable" if level == "ROW" else "service_plan_backfill_no_truncate"
        op.execute(sa.text(f"""
            CREATE TRIGGER {trigger} BEFORE {event}
            ON {quoted}.service_plan_backfill_resolutions FOR EACH {level}
            EXECUTE FUNCTION {quoted}.service_plan_policy_immutable()
        """))


def downgrade():
    if context.is_offline_mode():
        raise RuntimeError("Cannot prove an empty service-plan rollback offline")
    schema = settings.DB_SCHEMA
    connection = op.get_bind()
    quoted = connection.dialect.identifier_preparer.quote_schema(schema)
    tables = ("service_plan_backfill_resolutions", "user_service_plans", "service_plan_policies")
    connection.execute(
        sa.text(
            f"LOCK TABLE {', '.join(f'{quoted}.{name}' for name in (*tables, 'tenants', 'entitlement_usage'))} IN ACCESS EXCLUSIVE MODE"
        )
    )
    for table in tables:
        if connection.scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {quoted}.{table})")):
            raise RuntimeError("Service-plan downgrade refused: retained target data must be preserved")
    if connection.scalar(
        sa.text(f"SELECT EXISTS (SELECT 1 FROM {quoted}.entitlement_usage WHERE plan_holder_user_id IS NOT NULL)")
    ):
        raise RuntimeError("Service-plan downgrade refused: admission policy evidence must be preserved")
    if connection.scalar(
        sa.text(f"SELECT EXISTS (SELECT 1 FROM {quoted}.tenants WHERE plan_holder_user_id IS NOT NULL)")
    ):
        raise RuntimeError("Service-plan downgrade refused: sponsorship must be preserved")
    op.drop_table("service_plan_backfill_resolutions", schema=schema)
    op.drop_constraint("ck_entitlement_usage_holder_policy", "entitlement_usage", schema=schema, type_="check")
    op.drop_column("entitlement_usage", "plan_policy_version", schema=schema)
    op.drop_column("entitlement_usage", "plan_holder_user_id", schema=schema)
    op.drop_index("ix_tenants_plan_holder_user_id", table_name="tenants", schema=schema)
    op.drop_constraint("fk_tenants_plan_holder_user", "tenants", schema=schema, type_="foreignkey")
    op.drop_column("tenants", "plan_holder_user_id", schema=schema)
    op.drop_table("user_service_plans", schema=schema)
    op.drop_table("service_plan_policies", schema=schema)
    op.execute(sa.text(f"DROP FUNCTION {quoted}.service_plan_policy_immutable()"))
