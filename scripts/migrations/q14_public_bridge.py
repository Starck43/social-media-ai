"""Preserve public data while applying the old 0118 delta and stamping the baseline.

This is an explicit deployment command. It accepts only the local
social_manager/public schema at revision 0117 and runs the DDL, catalog inserts,
Alembic stamp, and postconditions in one transaction.
"""

from __future__ import annotations

import argparse
import importlib.util
from collections import Counter
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
LOCK_KEY = 1370014
_ALLOWED_REGISTRY_TABLES = {"model_types", "permissions", "workspace_role_permission"}


def _load_0118_bridge():
    path = ROOT / "migrations/bridges/action_audit_0118.py"
    spec = importlib.util.spec_from_file_location("q14_action_audit_0118", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load the reviewed 0118 transition bridge")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _table_fingerprints(connection, schema: str) -> dict[str, Counter[str]]:
    preparer = connection.dialect.identifier_preparer
    return {
        table: Counter(
            connection.execute(
                text(
                    f"SELECT md5(row_to_json(r)::text) "
                    f"FROM {preparer.quote_schema(schema)}.{preparer.quote(table)} AS r"
                )
            )
            .scalars()
            .all()
        )
        for table in inspect(connection).get_table_names(schema=schema)
        if table != "alembic_version"
    }


def _require_local_target(connection, schema: str, *, validation_test_schema: bool = False) -> None:
    database, address = connection.execute(text("SELECT current_database(), inet_server_addr()::text")).one()
    allowed_schema = schema == "public" or (validation_test_schema and schema == "test_schema")
    if not allowed_schema or database != "social_manager" or address not in (None, "127.0.0.1", "::1"):
        raise RuntimeError(
            "Refusing transition: expected local social_manager/public or explicit test_schema validation"
        )


def apply_public_transition(
    connection, config: Config, schema: str = "public", *, validation_test_schema: bool = False, expected_catalog=None
) -> None:
    """Apply the retained 0118 forward delta and baseline stamp atomically."""
    from app.core.config import settings

    if settings.DB_SCHEMA != schema:
        raise RuntimeError(f"Configured DB_SCHEMA {settings.DB_SCHEMA!r} does not match target {schema!r}")
    _require_local_target(connection, schema, validation_test_schema=validation_test_schema)
    if not connection.execute(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": LOCK_KEY}).scalar_one():
        raise RuntimeError("Another Q-14 validator or schema writer owns the database lock")
    other_sessions = (
        connection.execute(
            text(
                "SELECT pid FROM pg_stat_activity WHERE datname=current_database() "
                "AND pid<>pg_backend_pid() AND backend_type='client backend'"
            )
        )
        .scalars()
        .all()
    )
    if other_sessions:
        raise RuntimeError(f"Stop other database sessions before the schema transition: {other_sessions}")
    revisions = connection.execute(text(f'SELECT version_num FROM "{schema}".alembic_version')).scalars().all()
    if revisions != ["0117"]:
        raise RuntimeError(f"Expected exactly revision 0117, found {revisions!r}")
    if inspect(connection).has_table("action_audit_events", schema=schema):
        raise RuntimeError("action_audit_events already exists; inspect partial/foreign work before continuing")
    function_exists = connection.execute(
        text("SELECT to_regprocedure(:name) IS NOT NULL"),
        {"name": f"{schema}.guard_action_audit_mutation()"},
    ).scalar_one()
    if function_exists:
        raise RuntimeError("guard_action_audit_mutation already exists; inspect partial/foreign work")

    from app.models import Base
    from scripts.migrations.q14_compare_schemas import _catalog, compare

    connection.execute(text("SET LOCAL search_path TO pg_catalog"))
    if not validation_test_schema:
        if expected_catalog is not None:
            raise RuntimeError("Public validation must use the actual prepared test_schema catalog")

        baseline_revision = (
            connection.execute(text("SELECT version_num FROM test_schema.alembic_version")).scalars().all()
        )
        if baseline_revision != ["baseline_0118"]:
            raise RuntimeError("Prepare and validate test_schema at baseline_0118 before public transition")
        expected_catalog = _catalog(connection, "test_schema")
        compatibility = compare(
            _catalog(connection, "public"),
            expected_catalog,
            before_bridge=True,
            allow_reconciliation=True,
            model_tables={table.name for table in Base.metadata.sorted_tables},
        )
        if compatibility["status"] != "PASS":
            raise RuntimeError("Schema compatibility failed; run q14_compare_schemas --before-bridge and investigate")
    elif expected_catalog is None:
        raise RuntimeError("Rehearsal must supply the baseline catalog captured before cloning public")

    connection.execute(text(f'LOCK TABLE "{schema}".tenant_channels, "{schema}".bot_actions IN ACCESS EXCLUSIVE MODE'))
    legacy_table = inspect(connection).has_table("oauth_pending", schema=schema)
    if legacy_table:
        connection.execute(text(f'LOCK TABLE "{schema}".oauth_pending IN ACCESS EXCLUSIVE MODE'))
    before = _table_fingerprints(connection, schema)
    if legacy_table and before["oauth_pending"]:
        raise RuntimeError("oauth_pending is not empty; preserve its rows and stop the alignment")
    duplicate_chats = connection.execute(
        text(
            f'SELECT count(*) FROM (SELECT channel, chat_id FROM "{schema}".tenant_channels '
            "GROUP BY channel, chat_id HAVING count(*) > 1) AS duplicate_chats"
        )
    ).scalar_one()
    if duplicate_chats:
        raise RuntimeError("Duplicate messenger chats prevent global uniqueness; no rows may be removed automatically")
    bridge = _load_0118_bridge()
    bridge.SCHEMA = schema
    migration_context = MigrationContext.configure(connection, opts={"version_table_schema": schema})
    with Operations.context(migration_context):
        operations = Operations(migration_context)
        foreign_keys = [
            fk
            for fk in inspect(connection).get_foreign_keys("bot_actions", schema=schema)
            if fk["constrained_columns"] == ["agent_task_id"]
        ]
        if len(foreign_keys) != 1 or foreign_keys[0]["referred_table"] != "agent_tasks":
            raise RuntimeError("Unexpected bot_actions task binding constraint")
        foreign_key = foreign_keys[0]
        if foreign_key.get("options", {}).get("ondelete") != "SET NULL":
            if foreign_key.get("options", {}).get("ondelete") != "CASCADE":
                raise RuntimeError("Unexpected task binding deletion rule")
            operations.drop_constraint(foreign_key["name"], "bot_actions", schema=schema, type_="foreignkey")
            operations.create_foreign_key(
                foreign_key["name"],
                "bot_actions",
                "agent_tasks",
                ["agent_task_id"],
                ["id"],
                source_schema=schema,
                referent_schema=schema,
                ondelete="SET NULL",
            )
        uniques = inspect(connection).get_unique_constraints("tenant_channels", schema=schema)
        binding = [item for item in uniques if item["name"] == "uq_tenant_channel_chat"]
        if len(binding) != 1:
            raise RuntimeError("Unexpected tenant channel uniqueness constraint")
        if binding[0]["column_names"] == ["tenant_id", "channel", "chat_id"]:
            operations.drop_constraint("uq_tenant_channel_chat", "tenant_channels", schema=schema, type_="unique")
            operations.create_unique_constraint(
                "uq_tenant_channel_chat", "tenant_channels", ["channel", "chat_id"], schema=schema
            )
        elif binding[0]["column_names"] != ["channel", "chat_id"]:
            raise RuntimeError("Unexpected tenant channel uniqueness columns")
        if legacy_table:
            operations.drop_table("oauth_pending", schema=schema)
        operations.execute(f'DROP TYPE IF EXISTS "{schema}".analyze_type')
        bridge.upgrade()

    aligned = compare(
        _catalog(connection, schema),
        expected_catalog,
        allow_legacy=False,
        model_tables={table.name for table in Base.metadata.sorted_tables},
    )
    if aligned["status"] != "PASS":
        raise RuntimeError("Aligned schema differs from the tested baseline; rolling back the entire transition")

    config.attributes["connection"] = connection
    config.attributes["skip_model_type_registration_on_stamp"] = True
    command.stamp(config, "baseline_0118", purge=True)

    after = _table_fingerprints(connection, schema)
    for table, fingerprints in before.items():
        actual = after.get(table)
        if table in _ALLOWED_REGISTRY_TABLES:
            if actual is None or not fingerprints <= actual:
                raise RuntimeError(f"Existing registry row fingerprints changed in {table}")
        elif table == "oauth_pending" and not fingerprints and actual is None:
            continue
        elif actual != fingerprints:
            raise RuntimeError(f"Existing row fingerprints changed in {table}")
    if after.get("action_audit_events") != Counter():
        raise RuntimeError("New action audit table is expected to be empty after the bridge")
    revision = connection.execute(text(f'SELECT version_num FROM "{schema}".alembic_version')).scalar_one()
    if revision != "baseline_0118":
        raise RuntimeError(f"Expected baseline_0118 after stamp, found {revision!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply-public-transition", action="store_true", required=True)
    args = parser.parse_args()

    from app.core.config import settings

    url = make_url(settings.POSTGRES_URL)
    expected_schema = "public"
    if (
        settings.DB_SCHEMA != expected_schema
        or url.database != "social_manager"
        or url.host
        not in (
            None,
            "",
            "localhost",
            "127.0.0.1",
            "/tmp",
        )
    ):
        raise SystemExit("Refusing schema transition: expected local social_manager and the selected schema")
    config = Config(str(ROOT / "alembic.ini"))
    from app.core.database import engine

    with engine.begin() as connection:
        apply_public_transition(
            connection,
            config,
            expected_schema,
        )
    print(f"{expected_schema} transition complete: 0117 -> baseline_0118; existing row fingerprints preserved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
