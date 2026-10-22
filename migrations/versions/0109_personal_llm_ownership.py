"""Personal LLM ownership and guarded task links. Never assigns legacy keys implicitly.

Reviewed owner instruction (relayed by the coordinated settings session): existing
catalog belongs to user 2. Apply explicitly with -x llm_legacy_owner_id=2 only
following local backup/target/writer checks. This definition is not deployment.

Revision ID: 0109
Revises: 0108
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import context, op

from app.core.config import settings

revision = "0109"
down_revision = "0108"
branch_labels = None
depends_on = None
SCHEMA = settings.DB_SCHEMA


def upgrade():
    bind = op.get_bind()
    q = bind.dialect.identifier_preparer.quote
    schema = q(SCHEMA)
    providers = f"{schema}.llm_providers"
    models = f"{schema}.llm_models"
    tenants = f"{schema}.tenants"
    # Stop before DDL unless the operator explicitly supplies the reviewed owner.
    amount = bind.execute(sa.text(f"SELECT count(*) FROM {providers}")).scalar_one()
    owner = context.get_x_argument(as_dictionary=True).get("llm_legacy_owner_id")
    if amount:
        if owner != "2":
            raise RuntimeError("Legacy catalog requires reviewed -x llm_legacy_owner_id=2; no inferred owner")
        active = bind.execute(sa.text(f"SELECT id FROM {schema}.users WHERE id=:owner AND is_active=true"), {"owner": 2}).scalar_one_or_none()
        if active is None:
            raise RuntimeError("Reviewed legacy owner 2 must exist and be active")
    invalid = bind.execute(sa.text(f"""
        SELECT count(*) FROM {schema}.agent_task_sources ats
        JOIN {schema}.agent_tasks t ON t.id=ats.agent_task_id
        JOIN {schema}.sources s ON s.id=ats.source_id
        WHERE t.tenant_id<>s.tenant_id OR (s.owner_task_id IS NOT NULL AND s.owner_task_id<>t.id)
    """)).scalar_one()
    invalid += bind.execute(sa.text(f"""
        SELECT count(*) FROM {schema}.agent_tasks t JOIN {schema}.agent_scenarios s ON s.id=t.agent_scenario_id
        WHERE t.tenant_id<>s.tenant_id
    """)).scalar_one()
    if invalid:
        raise RuntimeError("Existing cross-workspace/task-owned bindings require reviewed repair before upgrade")
    for table in ("llm_providers", "llm_models"):
        op.add_column(table, sa.Column("owner_user_id", sa.Integer(), nullable=True), schema=SCHEMA)
    op.add_column("llm_providers", sa.Column("uid", sa.String(100), nullable=True), schema=SCHEMA)
    if amount:
        bind.execute(sa.text(f"UPDATE {providers} SET owner_user_id=2, uid='legacy-' || id::text"))
        bind.execute(sa.text(f"UPDATE {models} m SET owner_user_id=p.owner_user_id FROM {providers} p WHERE p.id=m.provider_id"))
    for table in ("llm_providers", "llm_models"):
        op.alter_column(table, "owner_user_id", nullable=False, schema=SCHEMA)
        op.create_foreign_key(f"fk_{table}_owner_user", table, "users", ["owner_user_id"], ["id"],
                              source_schema=SCHEMA, referent_schema=SCHEMA, ondelete="RESTRICT")
        op.create_index(f"ix_{table}_owner_user_id", table, ["owner_user_id"], schema=SCHEMA)
    op.alter_column("llm_providers", "uid", nullable=False, schema=SCHEMA)
    inspector = sa.inspect(bind)
    for constraint in inspector.get_unique_constraints("llm_providers", schema=SCHEMA):
        if constraint["column_names"] == ["name"]:
            op.drop_constraint(constraint["name"], "llm_providers", type_="unique", schema=SCHEMA)
    op.create_unique_constraint("uq_llm_provider_owner_uid", "llm_providers", ["owner_user_id", "uid"], schema=SCHEMA)
    op.create_unique_constraint("uq_llm_provider_id_owner", "llm_providers", ["id", "owner_user_id"], schema=SCHEMA)
    for fk in inspector.get_foreign_keys("llm_models", schema=SCHEMA):
        if fk["constrained_columns"] == ["provider_id"]:
            op.drop_constraint(fk["name"], "llm_models", type_="foreignkey", schema=SCHEMA)
    op.create_foreign_key("fk_llm_model_provider_owner", "llm_models", "llm_providers",
                          ["provider_id", "owner_user_id"], ["id", "owner_user_id"],
                          source_schema=SCHEMA, referent_schema=SCHEMA, ondelete="CASCADE")
    op.execute(sa.text(f"""
        CREATE FUNCTION {schema}.guard_personal_catalog_owner() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.owner_user_id<>OLD.owner_user_id THEN
            RAISE EXCEPTION 'Personal catalog ownership is immutable' USING ERRCODE='23514';
          END IF;
          IF TG_TABLE_NAME='llm_models' THEN
            IF NEW.provider_id<>OLD.provider_id THEN
              RAISE EXCEPTION 'Model provider is immutable' USING ERRCODE='23514';
            END IF;
          END IF;
          RETURN NEW;
        END $$
    """))
    for table in ("llm_providers", "llm_models"):
        op.execute(sa.text(f"CREATE TRIGGER guard_personal_catalog_owner BEFORE UPDATE ON {schema}.{table} FOR EACH ROW EXECUTE FUNCTION {schema}.guard_personal_catalog_owner()"))
    op.add_column("tenants", sa.Column("agent_model_id", sa.Integer(), nullable=True), schema=SCHEMA)
    op.create_foreign_key("fk_tenants_agent_model_id", "tenants", "llm_models", ["agent_model_id"], ["id"],
                          source_schema=SCHEMA, referent_schema=SCHEMA, ondelete="SET NULL")
    # Preserve numeric identities; only unambiguous names in an authorized workspace are migrated.
    # NULL legacy selections remain automatic and require operator review, never a name lookup at runtime.
    bind.execute(sa.text(f"""
        UPDATE {tenants} t SET agent_model_id=(
            SELECT min(m.id) FROM {models} m WHERE m.name=t.agent_model AND m.owner_user_id=2
            HAVING count(*)=1
        ) WHERE t.agent_model IS NOT NULL AND EXISTS (
            SELECT 1 FROM {schema}.tenant_users tu
            WHERE tu.tenant_id=t.id AND tu.user_id=2 AND tu.is_active=true
        )
    """))
    op.execute(sa.text(f"""
        CREATE FUNCTION {schema}.guard_task_binding() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE task_tenant integer; source_tenant integer; source_owner integer;
        BEGIN
          IF TG_TABLE_NAME='agent_task_sources' THEN
            SELECT tenant_id INTO task_tenant FROM {schema}.agent_tasks WHERE id=NEW.agent_task_id FOR KEY SHARE;
            SELECT tenant_id, owner_task_id INTO source_tenant, source_owner FROM {schema}.sources WHERE id=NEW.source_id FOR KEY SHARE;
            IF task_tenant IS NULL OR source_tenant IS NULL OR task_tenant<>source_tenant
               OR (source_owner IS NOT NULL AND source_owner<>NEW.agent_task_id) THEN
              RAISE EXCEPTION 'Invalid workspace task source binding' USING ERRCODE='23514';
            END IF;
          ELSIF TG_TABLE_NAME='agent_tasks' THEN
            IF TG_OP='UPDATE' AND NEW.tenant_id<>OLD.tenant_id THEN
              RAISE EXCEPTION 'Workspace ownership is immutable' USING ERRCODE='23514';
            END IF;
            IF NEW.agent_scenario_id IS NOT NULL THEN
              PERFORM 1 FROM {schema}.agent_scenarios WHERE id=NEW.agent_scenario_id AND tenant_id=NEW.tenant_id FOR KEY SHARE;
              IF NOT FOUND THEN RAISE EXCEPTION 'Invalid workspace scenario binding' USING ERRCODE='23514'; END IF;
            END IF;
          ELSE
            IF TG_TABLE_NAME='sources' THEN
              IF NEW.owner_task_id IS NOT NULL THEN
              PERFORM 1 FROM {schema}.agent_tasks WHERE id=NEW.owner_task_id AND tenant_id=NEW.tenant_id FOR KEY SHARE;
              IF NOT FOUND THEN RAISE EXCEPTION 'Invalid source owning task' USING ERRCODE='23514'; END IF;
              IF EXISTS (SELECT 1 FROM {schema}.agent_task_sources WHERE source_id=NEW.id AND agent_task_id<>NEW.owner_task_id) THEN
                RAISE EXCEPTION 'Task-owned source has foreign links' USING ERRCODE='23514';
              END IF;
            END IF;
            END IF;
            IF NEW.tenant_id<>OLD.tenant_id THEN
              RAISE EXCEPTION 'Workspace ownership is immutable' USING ERRCODE='23514';
            END IF;
          END IF;
          RETURN NEW;
        END $$
    """))
    op.execute(sa.text(f"CREATE TRIGGER guard_task_source_binding BEFORE INSERT OR UPDATE ON {schema}.agent_task_sources FOR EACH ROW EXECUTE FUNCTION {schema}.guard_task_binding()"))
    op.execute(sa.text(f"CREATE TRIGGER guard_task_scenario_binding BEFORE INSERT OR UPDATE ON {schema}.agent_tasks FOR EACH ROW EXECUTE FUNCTION {schema}.guard_task_binding()"))
    op.execute(sa.text(f"CREATE TRIGGER guard_workspace_ownership BEFORE UPDATE OF tenant_id, owner_task_id ON {schema}.sources FOR EACH ROW EXECUTE FUNCTION {schema}.guard_task_binding()"))
    op.execute(sa.text(f"CREATE TRIGGER guard_workspace_ownership BEFORE UPDATE OF tenant_id ON {schema}.agent_scenarios FOR EACH ROW EXECUTE FUNCTION {schema}.guard_task_binding()"))


def downgrade():
    bind = op.get_bind()
    schema = bind.dialect.identifier_preparer.quote(SCHEMA)
    duplicates = bind.execute(sa.text(f"SELECT name FROM {schema}.llm_providers GROUP BY name HAVING count(*)>1 LIMIT 1")).first()
    if duplicates:
        raise RuntimeError("Global provider names collide; reviewed consolidation required before downgrade")
    for table, trigger in (("agent_task_sources", "guard_task_source_binding"), ("agent_tasks", "guard_task_scenario_binding"),
                           ("sources", "guard_workspace_ownership"), ("agent_scenarios", "guard_workspace_ownership")):
        op.execute(sa.text(f"DROP TRIGGER {trigger} ON {schema}.{table}"))
    op.execute(sa.text(f"DROP FUNCTION {schema}.guard_task_binding()"))
    for table in ("llm_providers", "llm_models"):
        op.execute(sa.text(f"DROP TRIGGER guard_personal_catalog_owner ON {schema}.{table}"))
    op.execute(sa.text(f"DROP FUNCTION {schema}.guard_personal_catalog_owner()"))
    op.drop_constraint("fk_tenants_agent_model_id", "tenants", type_="foreignkey", schema=SCHEMA)
    op.drop_column("tenants", "agent_model_id", schema=SCHEMA)
    op.drop_constraint("fk_llm_model_provider_owner", "llm_models", type_="foreignkey", schema=SCHEMA)
    op.create_foreign_key("llm_models_provider_id_fkey", "llm_models", "llm_providers", ["provider_id"], ["id"],
                          source_schema=SCHEMA, referent_schema=SCHEMA, ondelete="CASCADE")
    op.drop_constraint("uq_llm_provider_id_owner", "llm_providers", type_="unique", schema=SCHEMA)
    op.drop_constraint("uq_llm_provider_owner_uid", "llm_providers", type_="unique", schema=SCHEMA)
    op.create_unique_constraint("llm_providers_name_key", "llm_providers", ["name"], schema=SCHEMA)
    op.drop_column("llm_providers", "uid", schema=SCHEMA)
    for table in ("llm_models", "llm_providers"):
        op.drop_constraint(f"fk_{table}_owner_user", table, type_="foreignkey", schema=SCHEMA)
        op.drop_index(f"ix_{table}_owner_user_id", table_name=table, schema=SCHEMA)
        op.drop_column(table, "owner_user_id", schema=SCHEMA)
