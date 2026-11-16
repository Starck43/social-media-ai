"""Actual login migration DDL and rollback in a transaction-only private schema."""

import importlib.util
import secrets
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.core.database import engine

pytestmark = pytest.mark.tenancy


def test_login_ddl_constraints_and_rollback_leave_authorizations_untouched():
    schema = "test_p12t14_login_" + secrets.token_hex(6)
    spec = importlib.util.spec_from_file_location(
        "_login_revision", Path(__file__).resolve().parents[2] / "migrations/versions/p12t14_telegram_login.py"
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    migration.settings = SimpleNamespace(DB_SCHEMA=schema)
    with engine.connect() as conn:
        tx = conn.begin()
        try:
            conn.execute(sa.schema.CreateSchema(schema))
            for table in ("users", "tenants", "personal_connector_authorizations", "workspace_authorization_grants"):
                conn.exec_driver_sql(f'CREATE TABLE "{schema}".{table} (id integer PRIMARY KEY)')
                conn.exec_driver_sql(f'INSERT INTO "{schema}".{table} VALUES (1), (2)')
            before = set(sa.inspect(conn).get_table_names(schema=schema))
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
            assert set(sa.inspect(conn).get_table_names(schema=schema)) == before | {"telegram_login_attempts"}
            metadata = sa.MetaData(schema=schema)
            metadata.reflect(conn)
            attempts = metadata.tables[f"{schema}.telegram_login_attempts"]
            values = dict(
                id=str(uuid4()),
                tenant_id=1,
                owner_user_id=1,
                request_key=str(uuid4()),
                state="processing",
                secret_encrypted="synthetic-ciphertext",
                expires_at=datetime.now(timezone.utc),
            )
            conn.execute(attempts.insert(), values)
            for extra in (
                {"id": str(uuid4()), "tenant_id": 2, "request_key": str(uuid4())},
                {"id": str(uuid4()), "state": "completed"},
                {"id": str(uuid4()), "request_key": str(uuid4()), "state": "unknown"},
                {"id": str(uuid4()), "owner_user_id": 2, "revision": 0},
                {"id": str(uuid4()), "owner_user_id": 999},
            ):
                with pytest.raises(sa.exc.IntegrityError), conn.begin_nested():
                    conn.execute(attempts.insert(), values | extra)
            conn.execute(attempts.update().values(state="cancelled", secret_encrypted=""))
            conn.execute(attempts.insert(), values | {"id": str(uuid4()), "request_key": str(uuid4())})
            with Operations.context(MigrationContext.configure(conn)):
                migration.downgrade()
            assert set(sa.inspect(conn).get_table_names(schema=schema)) == before
            assert (
                conn.exec_driver_sql(f'SELECT count(*) FROM "{schema}".personal_connector_authorizations').scalar_one()
                == 2
            )
        finally:
            tx.rollback()
        assert conn.execute(sa.text("SELECT to_regnamespace(:schema)"), {"schema": schema}).scalar_one() is None
