"""Real PostgreSQL migration tests against a rollback-only synthetic schema."""

from __future__ import annotations

import importlib
import secrets

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from cryptography.fernet import Fernet
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.database import engine


@pytest.fixture
def migration_schema(monkeypatch):
    # The project pytest conftest has already redirected engine to the isolated
    # test DB. A newly created transaction-owned schema is rolled back, even on
    # failures, and no real user/credential row is touched.
    schema = "test_p12t14_" + secrets.token_hex(6)
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "DB_SCHEMA", schema)
    monkeypatch.setattr(settings, "CREDENTIALS_KEY", key)
    migration = importlib.import_module("migrations.versions.0124_vk_encrypted_refresh")
    monkeypatch.setattr(migration.context, "is_offline_mode", lambda: False)
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            conn.exec_driver_sql(
                f'CREATE TABLE "{schema}".user_credentials ('
                "id INTEGER PRIMARY KEY, platform TEXT NOT NULL, kind TEXT NOT NULL, "
                "meta JSON, secret_encrypted TEXT NOT NULL)"
            )
            table = migration._table()
            with Operations.context(MigrationContext.configure(conn)):
                yield conn, table, migration, Fernet(key.encode())
        finally:
            transaction.rollback()
        assert conn.execute(sa.text("SELECT to_regnamespace(:name)"), {"name": schema}).scalar() is None


def _seed(conn, table, cipher, *, metadata=None):
    conn.execute(
        table.insert().values(
            id=1,
            platform="vk",
            kind="user_token",
            meta=metadata if metadata is not None else {"refresh_token": "SYNTHETIC_REFRESH", "custom": "kept"},
            secret_encrypted=cipher,
        )
    )


def test_real_upgrade_guarded_downgrade_and_reupgrade(migration_schema):
    conn, table, migration, fernet = migration_schema
    _seed(conn, table, fernet.encrypt(b"SYNTHETIC_ACCESS").decode())
    migration.upgrade()
    row = conn.execute(sa.select(table.c.meta, table.c.refresh_secret_encrypted)).one()
    assert row.meta == {"custom": "kept", "refresh_available": True}
    assert fernet.decrypt(row.refresh_secret_encrypted.encode()) == b"SYNTHETIC_REFRESH"
    with pytest.raises(IntegrityError), conn.begin_nested():
        conn.execute(table.update().where(table.c.id == 1).values(meta={"refresh_token": "SYNTHETIC_FORBIDDEN"}))
    with pytest.raises(IntegrityError), conn.begin_nested():
        conn.execute(table.update().where(table.c.id == 1).values(meta={"refresh_available": False}))
    with pytest.raises(RuntimeError, match="would discard"):
        migration.downgrade()
    conn.execute(table.delete())
    migration.downgrade()
    assert "refresh_secret_encrypted" not in {
        col["name"] for col in sa.inspect(conn).get_columns("user_credentials", schema=settings.DB_SCHEMA)
    }
    migration.upgrade()
    assert "refresh_secret_encrypted" in {
        col["name"] for col in sa.inspect(conn).get_columns("user_credentials", schema=settings.DB_SCHEMA)
    }


def test_wrong_key_rolls_back_migration_without_leaking_secret(migration_schema, caplog):
    conn, table, migration, _ = migration_schema
    wrong = Fernet(Fernet.generate_key()).encrypt(b"SYNTHETIC_ACCESS").decode()
    _seed(conn, table, wrong)
    with pytest.raises(RuntimeError, match="Cannot validate or encrypt"), conn.begin_nested():
        migration.upgrade()
    assert "refresh_secret_encrypted" not in {
        col["name"] for col in sa.inspect(conn).get_columns("user_credentials", schema=settings.DB_SCHEMA)
    }
    assert "SYNTHETIC_REFRESH" not in caplog.text
    assert "SYNTHETIC_ACCESS" not in caplog.text


def test_malformed_legacy_refresh_aborts_without_discarding_it(migration_schema):
    conn, table, migration, fernet = migration_schema
    _seed(conn, table, fernet.encrypt(b"SYNTHETIC_ACCESS").decode(), metadata={"refresh_token": ["bad"]})
    with pytest.raises(RuntimeError, match="Malformed VK refresh"), conn.begin_nested():
        migration.upgrade()
    assert "refresh_secret_encrypted" not in {
        col["name"] for col in sa.inspect(conn).get_columns("user_credentials", schema=settings.DB_SCHEMA)
    }
    assert conn.execute(sa.select(table.c.meta)).scalar_one() == {"refresh_token": ["bad"]}


def test_verbose_sql_logging_prevents_migration(migration_schema, monkeypatch):
    import logging

    conn, table, migration, fernet = migration_schema
    _seed(conn, table, fernet.encrypt(b"SYNTHETIC_ACCESS").decode())
    logger = logging.getLogger("sqlalchemy.engine.Engine")
    old_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        with pytest.raises(RuntimeError, match="Disable SQL"):
            migration.upgrade()
    finally:
        logger.setLevel(old_level)
    assert "refresh_secret_encrypted" not in {
        col["name"] for col in sa.inspect(conn).get_columns("user_credentials", schema=settings.DB_SCHEMA)
    }
