"""P2-T15 historical Alembic DDL cycle in a rollback-only private schema."""

from __future__ import annotations

import importlib.util
import secrets
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

from app.core.database import engine

pytestmark = pytest.mark.tenancy

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "scripts/migrations/legacy_definitions/0117_capability_entitlements.py"
BASELINE_SQL = ROOT / "migrations/versions/baseline_0118.sql"


def _load_migration(schema):
    spec = importlib.util.spec_from_file_location("_p2_t15_entitlement_migration", MIGRATION)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    migration.SCHEMA = schema
    return migration


def _usage_values(*, tenant_id=1, job_id=11, capability="connector.vk.read"):
    return {
        "tenant_id": tenant_id,
        "capability": capability,
        "job_id": job_id,
        "task_id": 21,
        "initiated_by_user_id": 31,
        "plan": "pro",
        "catalog_version": 1,
    }


def test_upgrade_downgrade_reapply_constraints_and_cleanup():
    schema = "test_p2t15_ent_" + secrets.token_hex(6)
    migration = _load_migration(schema)
    assert (migration.revision, migration.down_revision) == ("0117", "0116")

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            assert connection.scalar(sa.text("SELECT pg_try_advisory_xact_lock(1370093)")) is True
            connection.execute(sa.schema.CreateSchema(schema))
            connection.exec_driver_sql(f'CREATE TABLE "{schema}".tenants (id integer PRIMARY KEY, name text NOT NULL)')
            connection.exec_driver_sql(
                f"INSERT INTO \"{schema}\".tenants (id, name) VALUES (1, 'keep'), (2, 'cascade')"
            )

            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()

            inspector = sa.inspect(connection)
            assert set(inspector.get_table_names(schema=schema)) == {"tenants", "entitlement_usage"}
            tenant_columns = {column["name"] for column in inspector.get_columns("tenants", schema=schema)}
            assert tenant_columns == {"id", "name", "plan_expires_at"}
            usage_columns = {
                column["name"]: column for column in inspector.get_columns("entitlement_usage", schema=schema)
            }
            assert set(usage_columns) == {
                "id",
                "tenant_id",
                "capability",
                "job_id",
                "task_id",
                "initiated_by_user_id",
                "plan",
                "catalog_version",
                "admitted_at",
            }
            assert usage_columns["admitted_at"]["nullable"] is False
            assert usage_columns["admitted_at"]["default"] is not None
            assert {row["name"] for row in inspector.get_unique_constraints("entitlement_usage", schema=schema)} == {
                "uq_entitlement_usage_job_capability"
            }
            indexes = {row["name"]: row for row in inspector.get_indexes("entitlement_usage", schema=schema)}
            assert indexes["ix_entitlement_usage_daily"]["unique"] is False
            assert indexes["uq_entitlement_usage_job_capability"]["unique"] is True
            assert (
                indexes["uq_entitlement_usage_job_capability"]["duplicates_constraint"]
                == "uq_entitlement_usage_job_capability"
            )
            foreign_keys = inspector.get_foreign_keys("entitlement_usage", schema=schema)
            assert len(foreign_keys) == 1
            assert foreign_keys[0]["constrained_columns"] == ["tenant_id"]
            assert foreign_keys[0]["referred_table"] == "tenants"
            assert foreign_keys[0]["options"] == {"ondelete": "CASCADE"}

            metadata = sa.MetaData(schema=schema)
            metadata.reflect(connection)
            usage = metadata.tables[f"{schema}.entitlement_usage"]
            tenants = metadata.tables[f"{schema}.tenants"]
            connection.execute(usage.insert(), [_usage_values(), _usage_values(tenant_id=2, job_id=12)])
            with pytest.raises(sa.exc.IntegrityError):
                with connection.begin_nested():
                    connection.execute(usage.insert(), _usage_values())
            connection.execute(tenants.delete().where(tenants.c.id == 2))
            assert connection.scalar(sa.select(sa.func.count()).select_from(usage)) == 1

            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
            inspector = sa.inspect(connection)
            assert set(inspector.get_table_names(schema=schema)) == {"tenants"}
            assert {column["name"] for column in inspector.get_columns("tenants", schema=schema)} == {"id", "name"}
            assert connection.exec_driver_sql(f'SELECT name FROM "{schema}".tenants').scalar_one() == "keep"

            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            assert set(sa.inspect(connection).get_table_names(schema=schema)) == {"tenants", "entitlement_usage"}
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
        finally:
            transaction.rollback()

        assert connection.scalar(sa.text("SELECT to_regnamespace(:schema)"), {"schema": schema}) is None


def test_frozen_baseline_retains_entitlement_shape_and_current_graph_has_one_head():
    snapshot = BASELINE_SQL.read_text(encoding="utf-8")
    for fragment in (
        "plan_expires_at timestamp with time zone",
        "CREATE TABLE {{schema}}.entitlement_usage",
        "capability character varying(160) NOT NULL",
        "ADD CONSTRAINT uq_entitlement_usage_job_capability UNIQUE (tenant_id, job_id, capability)",
        "CREATE INDEX ix_entitlement_usage_daily",
        "FOREIGN KEY (tenant_id) REFERENCES {{schema}}.tenants(id) ON DELETE CASCADE",
    ):
        assert fragment in snapshot

    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    assert len(script.get_heads()) == 1
    assert script.get_revision("baseline_0118") is not None
