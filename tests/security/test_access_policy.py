"""Behavioral contracts for independent platform and workspace authority."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

__source_only__ = True

from app.core.access_catalog import RESOURCES, resolve_permission_key, resource_for, workspace_grants
from app.core.permissions import has_permission, has_permission_by_codename, has_role, permission_scope
from app.core.tenant_context import tenant_scope
from app.models import ModelType, Permission, PlatformRole, User, WorkspaceRole
from app.types import ActionType, PlatformRoleType, WorkspaceRoleType
from app.web.perms import WebPerms


def user(code="user", permissions=()):
    role = PlatformRole(id=2, codename=code, permissions=list(permissions))
    return User(id=7, username="actor", email="actor@example.invalid", is_active=True, role_id=2, role=role)


def member(role="viewer", **extra):
    return SimpleNamespace(
        id=3,
        tenant_id=11,
        user_id=7,
        channel="web",
        is_active=True,
        role_id=4,
        role=WorkspaceRole(id=4, codename=role),
        is_owner=role == "owner",
        ai_chat_enabled=extra.get("ai_chat_enabled", False),
    )


@pytest.mark.parametrize("platform", list(PlatformRoleType))
@pytest.mark.parametrize("role", list(WorkspaceRoleType))
@pytest.mark.parametrize("model,action", [("source", "delete"), ("tenant", "update"), ("aianalytics", "view")])
def test_web_and_core_agree_without_platform_elevation(platform, role, model, action):
    actor = user(platform.value)
    membership = member(role.value)
    grants = workspace_grants(role.value)
    perms = WebPerms(actor, [membership], 11, grants)
    # The personal LLM catalog (`e14c395`) follows the acting user: `configure`
    # on one's own provider never asks a role, and never reaches the web
    # console's workspace predicate — which answers workspace models only.
    personal_catalog = True
    with tenant_scope(11), permission_scope(actor, is_owner=membership.is_owner, member_grants=grants):
        assert has_permission(actor, model, action) == perms.can(model, action)
        assert has_permission(actor, "llmprovider", "configure") is personal_catalog
        assert not perms.can("llmprovider", "configure")
    if role == WorkspaceRoleType.VIEWER and action == "delete":
        assert not perms.can(model, action)


def test_switching_workspace_cannot_reuse_membership_grants():
    actor = user("admin")
    with tenant_scope(11), permission_scope(actor, member_grants=workspace_grants("manager")):
        assert has_permission(actor, "collection.source", "delete")
        with tenant_scope(12):
            assert not has_permission(actor, "source", "delete")
        assert has_permission(actor, "source", "delete")


@pytest.mark.parametrize("code,allowed", [("user", False), ("operator", True), ("admin", True)])
def test_operator_entry_and_inactive_account(code, allowed):
    actor = user(code)
    assert actor.has_admin_access() is allowed
    actor.is_active = False
    assert not actor.has_admin_access()
    assert not actor.has_perm_for("source", ActionType.VIEW)


def test_operator_global_data_context_does_not_bypass_action_permissions():
    permission = Permission(
        action_type=ActionType.VIEW,
        name="View sources",
        model_type=ModelType(resource_key="collection.source", model_name="source", table_name="sources"),
    )
    actor = user("operator", [permission])
    with tenant_scope(bypass=True), permission_scope(actor):
        assert has_permission(actor, "source", "view")
        assert not has_permission(actor, "source", "delete")
        assert not has_role(actor, PlatformRoleType.ADMIN)
        assert has_role(actor, PlatformRoleType.OPERATOR)


@pytest.mark.parametrize("role,ai,allowed", [("viewer", False, False), ("viewer", True, True), ("owner", False, True)])
def test_chat_permission_is_independent_from_platform_admin(role, ai, allowed):
    perms = WebPerms(user("admin"), [member(role, ai_chat_enabled=ai)], 11, workspace_grants(role))
    assert perms.can_open_chat is allowed
    assert not perms.can("llmprovider", "update")


def test_no_grants_from_missing_membership_or_unresolved_role():
    actor = user("admin")
    perms = WebPerms(actor, [], 11, workspace_grants("owner"))
    assert not perms.can("source", "delete")
    assert not perms.can_open_chat
    assert not workspace_grants(None)
    with tenant_scope(11), permission_scope(actor):
        assert not has_permission(actor, "source", "delete")


def test_role_names_are_fixed_strings_without_order_based_authority():
    assert {r.value for r in PlatformRoleType} == {"user", "operator", "admin", "superuser"}
    assert {r.value for r in WorkspaceRoleType} == {"owner", "admin", "manager", "analyst", "viewer"}
    assert all(isinstance(r.value, str) for r in (*PlatformRoleType, *WorkspaceRoleType))
    assert ("aianalytics", ActionType.DELETE) not in workspace_grants("analyst")
    assert ("digestrun", ActionType.UPDATE) not in workspace_grants("analyst")
    assert "name" not in PlatformRole.__table__.c
    assert "is_superuser" not in User.__table__.c


def test_codename_is_derived_and_old_explicit_alias_resolves_same_right():
    model_type = ModelType(resource_key="analysis.result", model_name="aianalytics", table_name="ai_analytics")
    permission = Permission(model_type=model_type, action_type=ActionType.VIEW, name="View results")
    assert permission.codename == "analysis.result.view"
    assert "codename" not in Permission.__table__.c
    assert resolve_permission_key("dashboard.AIAnalytics.view") == resolve_permission_key(permission.codename)
    assert resolve_permission_key("invented.AIAnalytics.view") is None
    assert resolve_permission_key("analysis.result.*") is None
    assert resolve_permission_key("analysis.result.wrong") is None


def test_migration_catalog_and_presets_match_runtime_without_database():
    path = (
        Path(__file__).resolve().parents[2]
        / "scripts/migrations/legacy_definitions/0096_split_access_roles_and_resources.py"
    )
    spec = importlib.util.spec_from_file_location("access_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    expected = {
        r.table_name: (r.key, r.model_name, set(a.db_value for a in r.actions), r.workspace, r.personal)
        for r in RESOURCES
    }
    actual = {
        table: (row[0], row[1], set(row[2].split()), row[3], row[4]) for table, row in migration.RESOURCES.items()
    }
    # P11-T12 added the action-audit resource and P11-T28 added one declared
    # workspace action after the 0096 snapshot. Keep that migration immutable
    # and make only these post-baseline deltas explicit; every other resource
    # identity, action and scope flag must still match.
    actual["action_audit_events"] = ("audit.action_audit", "actionauditevent", {"view", "export"}, True, False)
    key, model, actions, is_workspace, is_personal = actual["tenants"]
    actual["tenants"] = (key, model, actions | {"delete"}, is_workspace, is_personal)
    assert expected == actual
    for role, pairs in migration._workspace_grant_pairs().items():
        canonical = {(resource_for(model).key, action.db_value) for model, action in workspace_grants(role)}
        baseline_pairs = pairs | (
            {("audit.action_audit", "view"), ("audit.action_audit", "export")}
            if role in ("owner", "admin")
            else set()
        )
        assert canonical == baseline_pairs


def test_migration_permission_map_keeps_canonical_model_without_permissions(monkeypatch):
    import sqlite3

    path = (
        Path(__file__).resolve().parents[2]
        / "scripts/migrations/legacy_definitions/0096_split_access_roles_and_resources.py"
    )
    spec = importlib.util.spec_from_file_location("access_migration_permission_map", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    class CapturedPermissionMap(Exception):
        pass

    captured = []

    class Bind:
        def execute(self, statement, params=None):
            sql = str(statement)
            if "CREATE TEMP TABLE _access_permission_map" in sql:
                captured.append(sql.split(" AS\n", 1)[1].strip())
                raise CapturedPermissionMap
            return SimpleNamespace(all=lambda: [])

    bind = Bind()

    class MigrationOperations:
        @staticmethod
        def get_bind():
            return bind

        @staticmethod
        def add_column(*args, **kwargs):
            pass

    monkeypatch.setattr(migration, "op", MigrationOperations)
    monkeypatch.setattr(migration, "_qualified", lambda table: table)
    monkeypatch.setattr(migration, "_drop_unique_for_columns", lambda *args: None)
    monkeypatch.setattr(migration, "_normalize_action_enum", lambda: None)
    with pytest.raises(CapturedPermissionMap):
        migration._normalize_resources_and_permissions()

    assert len(captured) == 1
    select_sql = captured[0].replace("p.action_type::text", "p.action_type")
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(
            """
            CREATE TABLE model_types (id INTEGER PRIMARY KEY, resource_key TEXT NOT NULL);
            CREATE TABLE permissions (id INTEGER PRIMARY KEY, model_type_id INTEGER NOT NULL, action_type TEXT NOT NULL);
            CREATE TABLE _access_model_map (old_id INTEGER NOT NULL, keep_id INTEGER NOT NULL, resource_key TEXT NOT NULL);
            INSERT INTO model_types VALUES (1, 'collection.source'), (2, 'collection.source'), (3, 'collection.source');
            INSERT INTO _access_model_map VALUES
                (1, 1, 'collection.source'), (2, 1, 'collection.source'), (3, 1, 'collection.source');
            INSERT INTO permissions VALUES (10, 2, 'VIEW'), (11, 3, 'VIEW');
            """
        )
        rows = connection.execute(select_sql).fetchall()

    assert rows == [
        (10, 10, 1, "collection.source", "view"),
        (11, 10, 1, "collection.source", "view"),
    ]


def test_migration_retires_only_allowlisted_model_metadata_and_keeps_physical_data():
    import importlib.util

    from sqlalchemy import create_engine, text

    path = (
        Path(__file__).resolve().parents[2]
        / "scripts/migrations/legacy_definitions/0096_split_access_roles_and_resources.py"
    )
    spec = importlib.util.spec_from_file_location("access_migration_retirement", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE oauth_pending (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)"))
            connection.execute(text("CREATE TABLE model_types (id INTEGER PRIMARY KEY, table_name TEXT NOT NULL)"))
            connection.execute(
                text("CREATE TABLE permissions (id INTEGER PRIMARY KEY, model_type_id INTEGER NOT NULL)")
            )
            connection.execute(
                text("CREATE TABLE role_permission (role_id INTEGER NOT NULL, permission_id INTEGER NOT NULL)")
            )
            connection.execute(text("INSERT INTO oauth_pending VALUES (1, 'sentinel')"))
            connection.execute(
                text("INSERT INTO model_types VALUES (1, 'sources'), (2, 'oauth_pending'), (3, 'unexpected_legacy')")
            )
            connection.execute(text("INSERT INTO permissions VALUES (10, 1), (20, 2), (30, 3)"))
            connection.execute(text("INSERT INTO role_permission VALUES (7, 10), (7, 20), (7, 30)"))

            migration._retire_legacy_model_types(connection, "model_types", "permissions", "role_permission")

            assert connection.execute(text("SELECT id, table_name FROM model_types ORDER BY id")).all() == [
                (1, "sources"),
                (3, "unexpected_legacy"),
            ]
            assert connection.execute(text("SELECT id, model_type_id FROM permissions ORDER BY id")).all() == [
                (10, 1),
                (30, 3),
            ]
            role_permission_rows = connection.execute(
                text("SELECT role_id, permission_id FROM role_permission ORDER BY permission_id")
            ).all()
            assert role_permission_rows == [(7, 10), (7, 30)]
            assert connection.execute(text("SELECT id, payload FROM oauth_pending")).all() == [(1, "sentinel")]
    finally:
        engine.dispose()


def test_migration_seeds_missing_resource_catalog_rows_idempotently():
    import importlib.util
    from datetime import datetime, timezone

    from sqlalchemy import create_engine, event, text

    path = (
        Path(__file__).resolve().parents[2]
        / "scripts/migrations/legacy_definitions/0096_split_access_roles_and_resources.py"
    )
    spec = importlib.util.spec_from_file_location("access_migration_model_type_seed", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def register_sqlite_now(dbapi_connection, _connection_record):
        dbapi_connection.create_function("now", 0, lambda: datetime.now(timezone.utc).isoformat())

    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                CREATE TABLE model_types (
                    id INTEGER PRIMARY KEY,
                    app_name TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    table_name TEXT NOT NULL,
                    description TEXT,
                    is_managed BOOLEAN,
                    resource_key TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """
                )
            )
            connection.execute(
                text(
                    """
                INSERT INTO model_types
                    (id, app_name, model_name, table_name, description, is_managed,
                     resource_key, created_at, updated_at)
                VALUES
                    (41, 'legacy', 'OldSource', 'sources', 'keep this row', false, NULL, 'old', 'old'),
                    (99, 'legacy', 'Mystery', 'unexpected_legacy', 'keep unknown', true, NULL, 'old', 'old')
            """
                )
            )

            migration._seed_resource_model_types(connection, "model_types")
            after_first_seed = connection.execute(
                text(
                    """
                    SELECT id, app_name, model_name, table_name, description, is_managed,
                           resource_key, created_at, updated_at
                      FROM model_types ORDER BY table_name, id
                """
                )
            ).all()
            migration._seed_resource_model_types(connection, "model_types")
            after_second_seed = connection.execute(
                text(
                    """
                    SELECT id, app_name, model_name, table_name, description, is_managed,
                           resource_key, created_at, updated_at
                      FROM model_types ORDER BY table_name, id
                """
                )
            ).all()

            rows_by_table = {row[3]: row for row in after_second_seed}
            assert set(rows_by_table) == set(migration.RESOURCES) | {"unexpected_legacy"}
            assert after_second_seed == after_first_seed
            assert rows_by_table["sources"] == (
                41,
                "legacy",
                "OldSource",
                "sources",
                "keep this row",
                0,
                None,
                "old",
                "old",
            )
            assert rows_by_table["unexpected_legacy"][0:7] == (
                99,
                "legacy",
                "Mystery",
                "unexpected_legacy",
                "keep unknown",
                1,
                None,
            )
            model_type = rows_by_table["model_types"]
            assert model_type[1:7] == (
                "identity",
                "modeltype",
                "model_types",
                "Registered access resource",
                1,
                "identity.model_type",
            )
    finally:
        engine.dispose()


def test_operator_http_identity_loss_fails_closed_even_with_data_bypass():
    from app.core.permissions import platform_request_scope

    # The bypass is data access, never a grant: the console answers with the
    # operator's own `role_permission` rows (`docs/ADMIN.md`), and only the
    # platform owner holds the wildcard. Identity loss refuses either way.
    actor = user("admin")
    with tenant_scope(bypass=True), platform_request_scope(), permission_scope(actor):
        assert not has_permission(actor, "source", "delete")
        with permission_scope(None):
            assert not has_permission(None, "source", "delete")
            assert not has_permission(actor, "source", "delete")
            assert not has_role(None, PlatformRoleType.ADMIN)
            assert not has_role(actor, PlatformRoleType.ADMIN)


def test_platform_grant_identity_does_not_depend_on_orm_class_metadata():
    permission = Permission(
        action_type=ActionType.VIEW,
        name="View results",
        model_type=ModelType(
            resource_key="analysis.result", model_name="old_analytics_class", table_name="ai_analytics"
        ),
    )
    actor = user("operator", [permission])
    assert actor.has_perm_for("analysis.result", ActionType.VIEW)
    assert not actor.has_perm_for("llm.provider", ActionType.VIEW)
