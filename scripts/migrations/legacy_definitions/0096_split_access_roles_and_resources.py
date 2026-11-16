"""Separate platform and workspace roles; normalize permission identities.

Revision ID: 0096
Revises: 0095

This migration is deliberately forward-only. It retains existing user,
membership, invite, permission and association rows while normalizing their
identities. A downgrade needs a reviewed data-preservation plan.
"""

from __future__ import annotations

import ast

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0096"
down_revision = "0095"
branch_labels = None
depends_on = None

SCHEMA = settings.DB_SCHEMA

# Snapshot of app.core.access_catalog.RESOURCES at this revision. A migration
# must remain deterministic if the live catalog changes in a later release.
RESOURCES = {
    "users": ("identity.user", "user", "view create update delete", False, False),
    "roles": ("identity.platform_role", "platformrole", "view configure", False, False),
    "permissions": ("identity.permission", "permission", "view", False, False),
    "model_types": ("identity.model_type", "modeltype", "view", False, False),
    "user_credentials": ("identity.credential", "usercredential", "view create update delete configure", False, True),
    "tenants": ("tenancy.workspace", "tenant", "view update configure", True, False),
    "tenant_users": ("tenancy.membership", "tenantuser", "view create update delete", True, False),
    "workspace_roles": ("tenancy.workspace_role", "workspacerole", "view", False, False),
    "tenant_invites": ("tenancy.invite", "tenantinvite", "view create update delete", True, False),
    "tenant_channels": ("tenancy.channel", "tenantchannel", "view create update delete", True, False),
    "sources": ("collection.source", "source", "view create update delete analyze export", True, False),
    "platforms": ("collection.platform", "platform", "view create update delete", False, False),
    "collected_items": ("collection.collected_item", "collecteditem", "view create update delete", True, False),
    "agent_scenarios": ("analysis.scenario", "agentscenario", "view create update delete configure", True, False),
    "ai_analytics": ("analysis.result", "aianalytics", "view create update delete analyze export", True, False),
    "llm_providers": ("llm.provider", "llmprovider", "view create update delete configure", False, False),
    "llm_models": ("llm.model", "llmmodel", "view create update delete configure", False, False),
    "llm_attempts": ("llm.attempt", "llmattempt", "view create", True, False),
    "llm_budget_days": ("llm.budget_day", "llmbudgetday", "view", True, False),
    "llm_budget_reservations": ("llm.budget_reservation", "llmbudgetreservation", "view", True, False),
    "llm_dispatch_receipts": ("llm.dispatch_receipt", "llmdispatchreceipt", "view", True, False),
    "llm_outcome_events": ("llm.outcome_event", "llmoutcomeevent", "view", True, False),
    "agent_tasks": ("scheduling.task", "agenttask", "view create update delete configure", True, False),
    "jobs": ("scheduling.job", "job", "view update delete", True, False),
    "bot_actions": ("scheduling.action", "botaction", "view create update delete", True, False),
    "agent_sessions": ("agent.session", "agentsession", "view create update delete", True, True),
    "agent_messages": ("agent.message", "agentmessage", "view create update delete", True, True),
    "agent_memory": ("agent.memory", "agentmemory", "view create update delete", True, True),
    "agent_feedback": ("agent.feedback", "agentfeedback", "view create update delete", True, True),
    "notifications": ("notifications.notification", "notification", "view create update delete", True, False),
    "digest_runs": ("notifications.digest_run", "digestrun", "view create update delete export", True, False),
}

# Legacy ModelType registrations that described metadata rather than a current
# database resource. Retire only these allowlisted rows and their permission
# links; never drop a physical table or infer retirement for unknown entries.
RETIRED_MODEL_TABLES = frozenset({"oauth_pending"})

PLATFORM_ROLES = {
    "user": "Registered user; workspace permissions come from membership.",
    "operator": "Platform operator; actions require explicit platform permissions.",
    "admin": "Platform administrator.",
}
WORKSPACE_ROLES = {
    "owner": "Workspace owner with full workspace and team administration.",
    "admin": "Workspace administrator.",
    "manager": "Manages sources, analysis scenarios and scheduled tasks.",
    "analyst": "Reviews and analyzes workspace results.",
    "viewer": "Read-only access to workspace sources and results.",
}

# Exact historical tuple strings written by scripts/setup/roles.py. Arbitrary
# administrator descriptions are left untouched.
LEGACY_ROLE_DESCRIPTIONS = {
    "('Basic read-only access to view content', 'Зритель', '👁️')": "Platform role; workspace permissions come from membership.",
    "('AI assistant with limited access to specific features', 'AI Бот', '🤖')": "Platform role; workspace permissions come from membership.",
    "('Can manage team members and basic content', 'Менеджер', '👔')": "Platform role; workspace permissions come from membership.",
    "('Can view and analyze data and reports', 'Аналитик', '📊')": "Platform role; workspace permissions come from membership.",
    "('Can moderate content and manage users', 'Модератор', '🛡️')": "Platform role; workspace permissions come from membership.",
    "('Administrator with almost full access', 'Администратор', '⚙️')": "Platform administrator.",
    "('Superuser with full system access', 'Суперпользователь', '👑')": "Platform administrator.",
}


def _qualified(table: str) -> str:
    prep = op.get_bind().dialect.identifier_preparer
    return f"{prep.quote_schema(SCHEMA)}.{prep.quote(table)}"


def _drop_unique_for_columns(table: str, columns: tuple[str, ...]) -> None:
    inspector = sa.inspect(op.get_bind())
    for item in inspector.get_unique_constraints(table, schema=SCHEMA):
        if tuple(item.get("column_names") or ()) == columns and item.get("name"):
            op.drop_constraint(item["name"], table, schema=SCHEMA, type_="unique")


def _drop_role_foreign_keys(table: str) -> None:
    inspector = sa.inspect(op.get_bind())
    for item in inspector.get_foreign_keys(table, schema=SCHEMA):
        if item.get("constrained_columns") == ["role_id"] and item.get("referred_table") == "roles":
            if item.get("name"):
                op.drop_constraint(item["name"], table, schema=SCHEMA, type_="foreignkey")


def _retire_legacy_model_types(bind, model_types: str, permissions: str, role_permission: str) -> None:
    """Remove allowlisted obsolete registry metadata without touching data tables."""
    for table_name in sorted(RETIRED_MODEL_TABLES):
        params = {"table_name": table_name}
        bind.execute(
            sa.text(f"""
                DELETE FROM {role_permission}
                 WHERE permission_id IN (
                     SELECT p.id FROM {permissions} p
                     JOIN {model_types} mt ON mt.id = p.model_type_id
                     WHERE mt.table_name = :table_name
                 )
            """),
            params,
        )
        bind.execute(
            sa.text(f"""
                DELETE FROM {permissions}
                 WHERE model_type_id IN (
                     SELECT id FROM {model_types} WHERE table_name = :table_name
                 )
            """),
            params,
        )
        bind.execute(
            sa.text(f"DELETE FROM {model_types} WHERE table_name = :table_name"),
            params,
        )


def _seed_resource_model_types(bind, model_types: str) -> None:
    """Insert missing registry rows from this revision's immutable catalog snapshot."""
    for table_name, (resource_key, model_name, _actions, _workspace, _personal) in RESOURCES.items():
        bind.execute(
            sa.text(f"""
                INSERT INTO {model_types}
                    (app_name, model_name, table_name, description, is_managed, resource_key, created_at, updated_at)
                SELECT :app_name, :model_name, :table_name, 'Registered access resource', true,
                       :resource_key, now(), now()
                 WHERE NOT EXISTS (
                       SELECT 1 FROM {model_types} WHERE table_name = :table_name
                 )
            """),
            {
                "app_name": resource_key.split(".", 1)[0],
                "model_name": model_name,
                "table_name": table_name,
                "resource_key": resource_key,
            },
        )


def _normalize_action_enum() -> None:
    """Align the persisted action enum with ActionType's stored member names."""
    bind = op.get_bind()
    labels = tuple(
        bind.execute(
            sa.text("""
                SELECT e.enumlabel
                  FROM pg_type t
                  JOIN pg_namespace n ON n.oid = t.typnamespace
                  JOIN pg_enum e ON e.enumtypid = t.oid
                 WHERE n.nspname = :schema AND t.typname = 'action_type'
                 ORDER BY e.enumsortorder
            """),
            {"schema": SCHEMA},
        ).scalars()
    )
    target_labels = ("VIEW", "CREATE", "UPDATE", "DELETE", "ANALYZE", "MODERATE", "EXPORT", "CONFIGURE")
    if labels == target_labels:
        return

    normalized = {"UPDATE" if label.lower() == "edit" else label.upper() for label in labels}
    if normalized != set(target_labels):
        raise RuntimeError("Access migration found an unsupported action_type enum definition")

    preparer = bind.dialect.identifier_preparer
    schema = preparer.quote_schema(SCHEMA)
    old_type = f"{schema}.{preparer.quote('action_type')}"
    new_type_name = preparer.quote("action_type_access_new")
    new_type = f"{schema}.{new_type_name}"
    literals = ", ".join(f"'{label}'" for label in target_labels)
    bind.execute(sa.text(f"CREATE TYPE {new_type} AS ENUM ({literals})"))
    permissions = _qualified("permissions")
    bind.execute(sa.text(f"""
        ALTER TABLE {permissions} ALTER COLUMN action_type TYPE {new_type}
        USING (CASE lower(action_type::text)
                   WHEN 'edit' THEN 'UPDATE'
                   ELSE upper(action_type::text)
               END)::{new_type}
    """))
    bind.execute(sa.text(f"DROP TYPE {old_type}"))
    bind.execute(sa.text(f"ALTER TYPE {new_type} RENAME TO action_type"))


def _normalize_roles() -> None:
    bind = op.get_bind()
    roles = _qualified("roles")
    users = _qualified("users")
    tenant_users = _qualified("tenant_users")
    tenant_invites = _qualified("tenant_invites")
    role_permission = _qualified("role_permission")
    workspace_roles = _qualified("workspace_roles")

    op.create_table(
        "workspace_roles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("codename", sa.String(32), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "codename IN ('owner', 'admin', 'manager', 'analyst', 'viewer')", name="ck_workspace_role_codename"
        ),
        sa.UniqueConstraint("codename", name="uq_workspace_roles_codename"),
        schema=SCHEMA,
    )
    op.create_table(
        "workspace_role_permission",
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("permission_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["role_id"], [f"{SCHEMA}.workspace_roles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["permission_id"], [f"{SCHEMA}.permissions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("role_id", "permission_id"),
        schema=SCHEMA,
    )
    for code, description in WORKSPACE_ROLES.items():
        bind.execute(
            sa.text(f"INSERT INTO {workspace_roles} (codename, description) VALUES (:code, :description)"),
            {"code": code, "description": description},
        )

    # Keep the old role identity long enough to safely map memberships and
    # preserve only explicit administrative grants.
    bind.execute(sa.text(f"""
        CREATE TEMP TABLE _access_old_role_map ON COMMIT DROP AS
        SELECT id AS old_id, upper(codename::text) AS old_code,
               CASE WHEN upper(codename::text) IN ('ADMIN', 'SUPERUSER') THEN 'admin' ELSE 'user' END AS platform_code,
               CASE upper(codename::text)
                   WHEN 'SUPERUSER' THEN 'owner'
                   WHEN 'ADMIN' THEN 'admin'
                   WHEN 'MANAGER' THEN 'manager'
                   WHEN 'ANALYST' THEN 'analyst'
                   WHEN 'VIEWER' THEN 'viewer'
                   ELSE NULL
               END AS workspace_code
          FROM {roles}
    """))
    bind.execute(sa.text(f"""
        CREATE TEMP TABLE _access_admin_permission_ids ON COMMIT DROP AS
        SELECT DISTINCT rp.permission_id
          FROM {role_permission} rp
          JOIN _access_old_role_map rm ON rm.old_id = rp.role_id
         WHERE rm.platform_code = 'admin'
    """))

    for legacy, description in LEGACY_ROLE_DESCRIPTIONS.items():
        # Parse only the fixed migration literals, never an administrator value.
        text, label, emoji = ast.literal_eval(legacy)
        bind.execute(
            sa.text(f"""UPDATE {roles} SET description = :description
                       WHERE description = :legacy OR description =
                         CAST(ROW(CAST(:text AS text), CAST(:label AS text), CAST(:emoji AS text)) AS text)"""),
            {"description": description, "legacy": legacy, "text": text, "label": label, "emoji": emoji},
        )

    # Workspace membership and invitation references have distinct semantics
    # from platform roles. Unknown/null legacy roles fail closed as NULL.
    _drop_role_foreign_keys("tenant_users")
    _drop_role_foreign_keys("tenant_invites")
    bind.execute(sa.text(f"""
        UPDATE {tenant_users} tu
           SET role_id = (
               SELECT wr.id FROM _access_old_role_map rm
               JOIN {workspace_roles} wr ON wr.codename = rm.workspace_code
               WHERE rm.old_id = tu.role_id
           )
         WHERE tu.role_id IN (SELECT old_id FROM _access_old_role_map)
    """))
    bind.execute(sa.text(f"""
        UPDATE {tenant_invites} ti
           SET role_id = (
               SELECT wr.id FROM _access_old_role_map rm
               JOIN {workspace_roles} wr ON wr.codename = rm.workspace_code
               WHERE rm.old_id = ti.role_id
           )
         WHERE ti.role_id IN (SELECT old_id FROM _access_old_role_map)
    """))
    op.create_foreign_key(
        "fk_tenant_users_workspace_role",
        "tenant_users",
        "workspace_roles",
        ["role_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_tenant_invites_workspace_role",
        "tenant_invites",
        "workspace_roles",
        ["role_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )
    op.add_column(
        "tenant_users",
        sa.Column("ai_chat_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema=SCHEMA,
    )
    bind.execute(sa.text(f"""
        UPDATE {tenant_users} tu SET ai_chat_enabled = true
         FROM {workspace_roles} wr
        WHERE tu.role_id = wr.id AND wr.codename = 'owner'
    """))

    # Convert the enum column before applying the independent platform role
    # vocabulary. Existing users marked is_superuser are promoted explicitly.
    op.alter_column(
        "roles",
        "codename",
        schema=SCHEMA,
        existing_nullable=False,
        type_=sa.String(32),
        postgresql_using="lower(codename::text)",
    )
    bind.execute(sa.text(f"""
        UPDATE {roles} r SET codename = rm.platform_code
          FROM _access_old_role_map rm WHERE r.id = rm.old_id
    """))
    for code, description in PLATFORM_ROLES.items():
        bind.execute(
            sa.text(f"""
                INSERT INTO {roles} (name, codename, description, created_at, updated_at)
                SELECT :seed_name, :code, :description, now(), now()
                 WHERE NOT EXISTS (SELECT 1 FROM {roles} WHERE codename = :code)
            """),
            {"seed_name": f"__access_{code}__", "code": code, "description": description},
        )
    bind.execute(sa.text(f"""
        CREATE TEMP TABLE _access_platform_role_ids ON COMMIT DROP AS
        SELECT codename, min(id) AS id FROM {roles} GROUP BY codename
    """))
    bind.execute(sa.text(f"""
        UPDATE {users} u
           SET role_id = CASE WHEN coalesce(u.is_superuser, false) THEN elevated.id ELSE ordinary.id END
          FROM _access_old_role_map rm
          JOIN _access_platform_role_ids ordinary ON ordinary.codename = rm.platform_code
          JOIN _access_platform_role_ids elevated ON elevated.codename = 'admin'
         WHERE u.role_id = rm.old_id
    """))
    # Any user attached to an existing role has been mapped above; keep the
    # fallback explicit and least-privileged if legacy data was inconsistent.
    bind.execute(sa.text(f"""
        UPDATE {users} u SET role_id = pr.id
          FROM _access_platform_role_ids pr
         WHERE pr.codename = 'user' AND NOT EXISTS (
               SELECT 1 FROM {roles} r WHERE r.id = u.role_id)
    """))


def _normalize_resources_and_permissions() -> None:
    bind = op.get_bind()
    model_types = _qualified("model_types")
    permissions = _qualified("permissions")
    role_permission = _qualified("role_permission")
    workspace_role_permission = _qualified("workspace_role_permission")
    workspace_roles = _qualified("workspace_roles")

    _retire_legacy_model_types(op.get_bind(), model_types, permissions, role_permission)

    # Old permission uniqueness included the denormalized codename. Drop it
    # before replacing the legacy enum and rebuild the structured constraint
    # after duplicate permissions are merged.
    _drop_unique_for_columns("permissions", ("codename", "model_type_id"))
    _drop_unique_for_columns("permissions", ("codename", "action_type", "model_type_id"))
    _normalize_action_enum()

    op.add_column("model_types", sa.Column("resource_key", sa.String(100), nullable=True), schema=SCHEMA)
    _seed_resource_model_types(bind, model_types)
    model_rows = bind.execute(sa.text(f"SELECT id, table_name FROM {model_types} ORDER BY id")).all()
    for model_id, table_name in model_rows:
        try:
            resource_key, model_name, *_ = RESOURCES[table_name]
        except KeyError as exc:
            raise RuntimeError(
                f"Access migration has no resource mapping for model_types.table_name={str(table_name)[:80]!r}"
            ) from exc
        bind.execute(
            sa.text(
                f"UPDATE {model_types} SET resource_key = :resource_key, model_name = :model_name WHERE id = :model_id"
            ),
            {"resource_key": resource_key, "model_name": model_name, "model_id": model_id},
        )

    # Normalize duplicate model registrations and permission rows by the
    # stable resource/action pair. Preserve explicit ADMIN permission grants.
    bind.execute(sa.text(f"""
        CREATE TEMP TABLE _access_model_map ON COMMIT DROP AS
        SELECT id AS old_id, min(id) OVER (PARTITION BY resource_key) AS keep_id,
               resource_key
          FROM {model_types}
    """))
    bind.execute(sa.text(f"""
        CREATE TEMP TABLE _access_permission_map ON COMMIT DROP AS
        SELECT p.id AS old_id,
               min(p.id) OVER (PARTITION BY mt.resource_key, p.action_type) AS keep_id,
               mm.keep_id AS model_keep_id,
               mt.resource_key, lower(p.action_type::text) AS action_token
          FROM {permissions} p
          JOIN {model_types} mt ON mt.id = p.model_type_id
          JOIN _access_model_map mm ON mm.old_id = mt.id
    """))
    bind.execute(sa.text(f"""
        CREATE TEMP TABLE _access_admin_grants ON COMMIT DROP AS
        SELECT DISTINCT pm.keep_id AS permission_id
          FROM _access_admin_permission_ids ag
          JOIN _access_permission_map pm ON pm.old_id = ag.permission_id
    """))

    # Remove role links before deleting duplicate permission records, then
    # reconstruct the ADMIN links against their surviving permission rows.
    bind.execute(sa.text(f"DELETE FROM {role_permission}"))
    bind.execute(sa.text(f"""
        DELETE FROM {permissions} p USING _access_permission_map pm
         WHERE p.id = pm.old_id AND p.id <> pm.keep_id
    """))
    bind.execute(sa.text(f"""
        UPDATE {permissions} p SET model_type_id = pm.model_keep_id
          FROM _access_permission_map pm
         WHERE p.id = pm.keep_id
    """))
    bind.execute(sa.text(f"""
        DELETE FROM {model_types} mt USING _access_model_map mm
         WHERE mt.id = mm.old_id AND mt.id <> mm.keep_id
    """))
    bind.execute(sa.text(f"ALTER TABLE {permissions} DROP CONSTRAINT IF EXISTS check_codename_format"))
    op.drop_column("permissions", "codename", schema=SCHEMA)
    op.create_unique_constraint(
        "uq_permission_resource_action", "permissions", ["model_type_id", "action_type"], schema=SCHEMA
    )
    op.drop_column("model_types", "app_name", schema=SCHEMA)
    op.alter_column("model_types", "resource_key", schema=SCHEMA, nullable=False)
    op.create_unique_constraint("uq_model_types_resource_key", "model_types", ["resource_key"], schema=SCHEMA)

    # Seed only resource/action pairs explicitly present in this revision's
    # catalog snapshot. Old setup data did not always contain the full matrix.
    model_id_by_key = dict(bind.execute(sa.text(f"SELECT resource_key, id FROM {model_types}")).all())
    action_type = f"{bind.dialect.identifier_preparer.quote_schema(SCHEMA)}.action_type"
    seed_rows = []
    for table, (resource_key, model_name, action_tokens, _workspace, _personal) in RESOURCES.items():
        model_id = model_id_by_key.get(resource_key)
        if model_id is None:
            raise RuntimeError(f"Access migration is missing model_types resource_key={resource_key!r}")
        for action in action_tokens.split():
            seed_rows.append({"model_id": model_id, "action": action.upper(), "name": f"Can {action} {model_name}"})
    for row in seed_rows:
        bind.execute(
            sa.text(f"""
                INSERT INTO {permissions} (model_type_id, action_type, name, created_at, updated_at)
                VALUES (:model_id, CAST(:action AS {action_type}), :name, now(), now())
                ON CONFLICT (model_type_id, action_type) DO NOTHING
            """),
            row,
        )

    # Rebuild platform grants only for the canonical admin. Ordinary users and
    # operators start without inherited privileges; operator grants are explicit.
    roles = _qualified("roles")
    admin_id = bind.execute(
        sa.text(f"SELECT id FROM {roles} WHERE codename = 'admin' ORDER BY id LIMIT 1")
    ).scalar_one()
    # The canonical platform role table is physically `roles`; its ID lookup is
    # intentionally expressed through the same configured schema.
    bind.execute(
        sa.text(f"""
        INSERT INTO {role_permission} (role_id, permission_id)
        SELECT :role_id, permission_id FROM _access_admin_grants
        ON CONFLICT DO NOTHING
    """),
        {"role_id": admin_id},
    )

    grants = _workspace_grant_pairs()
    role_ids = dict(bind.execute(sa.text(f"SELECT codename, id FROM {workspace_roles}")).all())
    permission_ids = {
        (resource_key, action_token): permission_id
        for permission_id, resource_key, action_token in bind.execute(sa.text(f"""
            SELECT p.id, mt.resource_key, lower(p.action_type::text)
              FROM {permissions} p JOIN {model_types} mt ON mt.id = p.model_type_id
        """))
    }
    rows = [
        {"role_id": role_ids[role], "permission_id": permission_ids[pair]}
        for role, pairs in grants.items()
        for pair in pairs
        if pair in permission_ids
    ]
    if rows:
        bind.execute(
            sa.text(
                f"INSERT INTO {workspace_role_permission} (role_id, permission_id) VALUES (:role_id, :permission_id)"
            ),
            rows,
        )


def _workspace_grant_pairs() -> dict[str, set[tuple[str, str]]]:
    grants: dict[str, set[tuple[str, str]]] = {role: set() for role in WORKSPACE_ROLES}
    for table, (resource_key, _model_name, actions, workspace, personal) in RESOURCES.items():
        if not workspace:
            continue
        allowed = set(actions.split())
        for role in ("owner", "admin"):
            selected = allowed
            grants[role].update((resource_key, action) for action in selected)
        if personal:
            for role in ("manager", "analyst", "viewer"):
                grants[role].update((resource_key, action) for action in allowed)
        if table == "sources":
            grants["manager"].update((resource_key, action) for action in allowed)
            grants["analyst"].update((resource_key, action) for action in ("view", "analyze", "export"))
            grants["viewer"].add((resource_key, "view"))
        elif table == "agent_scenarios" or table == "agent_tasks":
            grants["manager"].update((resource_key, action) for action in allowed)
        elif table in ("ai_analytics", "digest_runs"):
            grants["manager"].add((resource_key, "view"))
            analyst_actions = {"view", "analyze", "export"} if table == "ai_analytics" else {"view", "create", "export"}
            grants["analyst"].update((resource_key, action) for action in analyst_actions & allowed)
            grants["viewer"].add((resource_key, "view"))
        elif table == "notifications":
            grants["manager"].add((resource_key, "view"))
    return grants


def upgrade() -> None:
    bind = op.get_bind()
    schema_q = bind.dialect.identifier_preparer.quote_schema(SCHEMA)
    _normalize_roles()
    _normalize_resources_and_permissions()

    roles = _qualified("roles")
    users = _qualified("users")
    tenant_users = _qualified("tenant_users")
    tenant_invites = _qualified("tenant_invites")
    model_types = _qualified("model_types")
    permissions = _qualified("permissions")
    role_permission = _qualified("role_permission")

    # All user assignments now point at the canonical platform role and all
    # membership assignments point at workspace_roles, so duplicate legacy
    # platform rows can be removed safely.
    bind.execute(sa.text(f"""
        DELETE FROM {roles} r USING (
            SELECT id, min(id) OVER (PARTITION BY codename) AS keep_id FROM {roles}
        ) duplicates
        WHERE r.id = duplicates.id AND r.id <> duplicates.keep_id
    """))

    # The old user-only role vocabulary is no longer persisted as a PG enum.
    _drop_unique_for_columns("roles", ("name",))
    op.drop_column("roles", "name", schema=SCHEMA)
    op.drop_column("users", "is_superuser", schema=SCHEMA)
    op.create_check_constraint(
        "ck_platform_role_codename", "roles", "codename IN ('user', 'operator', 'admin')", schema=SCHEMA
    )
    op.create_unique_constraint("uq_roles_codename", "roles", ["codename"], schema=SCHEMA)
    bind.execute(sa.text(f"DROP TYPE IF EXISTS {schema_q}.user_role_type"))

    # Workspace assignment references are now isolated from platform roles.
    op.alter_column("tenant_users", "role_id", schema=SCHEMA, existing_type=sa.Integer(), nullable=True)
    op.alter_column("tenant_invites", "role_id", schema=SCHEMA, existing_type=sa.Integer(), nullable=True)

    # Personal memory remains separate from the NULL-user legacy workspace
    # bucket. The memory owner controls visibility in the application layer.
    op.add_column(
        "agent_memory",
        sa.Column("user_id", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_agent_memory_user_id_users",
        "agent_memory",
        "users",
        ["user_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="CASCADE",
    )
    op.drop_constraint("uq_agent_memory_tenant_scope_key", "agent_memory", schema=SCHEMA, type_="unique")
    op.create_index(
        "uq_agent_memory_tenant_user_scope_key",
        "agent_memory",
        ["tenant_id", "user_id", "scope", "key"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_agent_memory_tenant_scope_key_legacy",
        "agent_memory",
        ["tenant_id", "scope", "key"],
        unique=True,
        postgresql_where=sa.text("user_id IS NULL"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    raise RuntimeError(
        "Revision 0096 is forward-only: workspace memberships, role identities, permission keys and personal memory "
        "cannot be safely restored to the shared legacy role schema without an owner-reviewed preservation plan."
    )
