"""Synthetic PostgreSQL freshness, rotation and retained-data Alembic checks.

Root conftest validates the task-owned candidate schema. Each test additionally
uses a transaction-scoped schema; no public/reference rows or real secrets.
"""

from __future__ import annotations

import importlib.util
import secrets
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.core.database import engine
from scripts.ops.rotate_credentials_key import RotationError, rotate_in_transaction

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session", autouse=True)
def exclusive_task_db():
    assert settings.DB_SCHEMA.startswith("test_p21t11_")
    with engine.connect() as lock:
        assert lock.execute(
            sa.text("SELECT pg_try_advisory_lock(1370093)")
        ).scalar_one(), "Another DB runner owns the target"
        try:
            yield
        finally:
            lock.rollback()
            lock.execute(sa.text("SELECT pg_advisory_unlock(1370093)"))
            lock.commit()


@pytest.fixture
def synthetic_schema():
    name = "test_p21t11_" + secrets.token_hex(6)
    with engine.connect() as conn:
        tx = conn.begin()
        try:
            conn.exec_driver_sql(f'CREATE SCHEMA "{name}"')
            conn.exec_driver_sql(f'CREATE TABLE "{name}".alembic_version (version_num TEXT NOT NULL)')
            conn.exec_driver_sql(f'CREATE TABLE "{name}".personal_llm_dispatches (dispatch_id TEXT PRIMARY KEY)')
            conn.exec_driver_sql(f"INSERT INTO \"{name}\".alembic_version VALUES ('p17t08_onec_history_status')")
            conn.exec_driver_sql(f"""CREATE TABLE "{name}".llm_providers (
                id INTEGER PRIMARY KEY, owner_user_id INTEGER, encrypted_api_key TEXT,
                base_url TEXT, auth_header TEXT, api_format TEXT, is_active BOOLEAN,
                name TEXT, uid TEXT, description TEXT)""")
            conn.exec_driver_sql(f"""CREATE TABLE "{name}".llm_models (
                id INTEGER PRIMARY KEY, provider_id INTEGER, owner_user_id INTEGER,
                custom_endpoint_path TEXT, model_id TEXT, is_active BOOLEAN)""")
            spec = importlib.util.spec_from_file_location(
                "_p21t11_migration", ROOT / "migrations/versions/0153_p21t11_credential_version.py"
            )
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)
            with patch.object(settings, "DB_SCHEMA", name), Operations.context(MigrationContext.configure(conn)):
                yield conn, name, migration
        finally:
            tx.rollback()
        assert conn.execute(sa.text("SELECT to_regnamespace(:name)"), {"name": name}).scalar() is None


def _provider(conn, schema):
    conn.exec_driver_sql(f"""INSERT INTO "{schema}".llm_providers
        (id, owner_user_id, base_url, api_format, is_active, name)
        VALUES (1, 2, 'https://example.org/v1', 'openai', true, 'Synthetic')""")
    conn.exec_driver_sql(f"""INSERT INTO "{schema}".llm_models
        VALUES (1, 1, 2, '/chat/completions', 'synthetic-model', true)""")


def _version(conn, schema):
    return conn.exec_driver_sql(f'SELECT credential_version FROM "{schema}".llm_providers WHERE id=1').scalar_one()


def test_alembic_empty_cycle_and_retained_data_refusal(synthetic_schema):
    conn, schema, migration = synthetic_schema
    migration.upgrade()
    migration.downgrade()
    _provider(conn, schema)
    migration.upgrade()
    assert _version(conn, schema) == 1
    assert conn.exec_driver_sql(f'SELECT name FROM "{schema}".llm_providers WHERE id=1').scalar_one() == "Synthetic"
    conn.exec_driver_sql(f'UPDATE "{schema}".llm_providers SET is_active=false WHERE id=1')
    with pytest.raises(RuntimeError, match="freshness has been used"):
        migration.downgrade()
    assert _version(conn, schema) == 2


def test_versions_increment_only_sensitive_changes_and_cannot_reset(synthetic_schema):
    conn, schema, migration = synthetic_schema
    migration.upgrade()
    _provider(conn, schema)
    for sql in ("name='Renamed'", "uid='renamed'", "description='Cosmetic'", "base_url=base_url"):
        conn.exec_driver_sql(f'UPDATE "{schema}".llm_providers SET {sql} WHERE id=1')
        assert _version(conn, schema) == 1
    for index, sql in enumerate(
        (
            "encrypted_api_key='SYNTHETIC_CIPHERTEXT'",  # pragma: allowlist secret (synthetic)
            "encrypted_api_key=NULL",
            "base_url='https://other.example/v1'",
            "auth_header='x-api-key'",
            "api_format='custom'",
            "is_active=false",
            "is_active=true",
            "owner_user_id=3",
        ),
        2,
    ):
        conn.exec_driver_sql(f'UPDATE "{schema}".llm_providers SET {sql} WHERE id=1')
        assert _version(conn, schema) == index
    for sql in ("credential_version=1", "credential_version=credential_version+1"):
        with pytest.raises(sa.exc.DBAPIError), conn.begin_nested():
            conn.exec_driver_sql(f'UPDATE "{schema}".llm_providers SET {sql} WHERE id=1')


def test_admitted_version_evidence_blocks_downgrade_without_fabricating_history(synthetic_schema):
    conn, schema, migration = synthetic_schema
    conn.exec_driver_sql(f"INSERT INTO \"{schema}\".personal_llm_dispatches VALUES ('historical')")
    migration.upgrade()
    assert (
        conn.exec_driver_sql(f'SELECT credential_version FROM "{schema}".personal_llm_dispatches').scalar_one() is None
    )
    conn.exec_driver_sql(f"INSERT INTO \"{schema}\".personal_llm_dispatches VALUES ('accepted', 1)")
    with pytest.raises(RuntimeError, match="freshness has been used"):
        migration.downgrade()


def test_model_endpoint_change_invalidates_provider_version(synthetic_schema):
    conn, schema, migration = synthetic_schema
    migration.upgrade()
    _provider(conn, schema)
    conn.exec_driver_sql(f"UPDATE \"{schema}\".llm_models SET custom_endpoint_path='/other' WHERE id=1")
    assert _version(conn, schema) == 2
    conn.exec_driver_sql(f"UPDATE \"{schema}\".llm_models SET model_id='replacement' WHERE id=1")
    assert _version(conn, schema) == 3
    conn.exec_driver_sql(f'UPDATE "{schema}".llm_models SET is_active=false WHERE id=1')
    assert _version(conn, schema) == 4


def _rotation_stores(conn, schema, old):
    conn.exec_driver_sql(f"""CREATE TABLE "{schema}".user_credentials (
        id INTEGER PRIMARY KEY, platform TEXT, kind TEXT, meta JSON,
        secret_encrypted TEXT, refresh_secret_encrypted TEXT,
        CONSTRAINT ck_user_credentials_vk_refresh_secret CHECK
        (platform <> 'vk' OR meta IS NULL OR NOT (meta::jsonb ? 'refresh_token')))""")
    conn.exec_driver_sql(
        f'CREATE TABLE "{schema}".workspace_connection_secrets (connection_id INTEGER PRIMARY KEY, secret_encrypted TEXT)'
    )
    for table in ("personal_connector_authorizations", "telegram_login_attempts"):
        conn.exec_driver_sql(f'CREATE TABLE "{schema}".{table} (id INTEGER PRIMARY KEY, secret_encrypted TEXT)')
        conn.exec_driver_sql(
            f"INSERT INTO \"{schema}\".{table} VALUES (1,%s),(2,'')", (old.encrypt(b"SYNTHETIC_BUNDLE").decode(),)
        )
    conn.exec_driver_sql(
        f'CREATE TABLE "{schema}".telegram_login_request_keys (owner_user_id INTEGER, request_key TEXT)'
    )
    for table in (
        "file_insight_mounts",
        "file_insight_object_states",
        "file_insight_attempts",
        "file_private_insights",
    ):
        conn.exec_driver_sql(f'CREATE TABLE "{schema}".{table} (id INTEGER)')
    conn.exec_driver_sql(
        f"INSERT INTO \"{schema}\".user_credentials VALUES (1,'vk','user_token',NULL,%s,%s)",
        (old.encrypt(b"SYNTHETIC_ACCESS").decode(), old.encrypt(b"SYNTHETIC_REFRESH").decode()),
    )
    conn.exec_driver_sql(
        f'INSERT INTO "{schema}".workspace_connection_secrets VALUES (1,%s)',
        (old.encrypt(b"SYNTHETIC_CONNECTION").decode(),),
    )
    conn.exec_driver_sql(
        f'UPDATE "{schema}".llm_providers SET encrypted_api_key=%s WHERE id=1',
        (old.encrypt(b"SYNTHETIC_PROVIDER").decode(),),
    )


def test_current_generation_rotation_all_stores_wrong_old_keys_and_no_logs(synthetic_schema, caplog):
    conn, schema, migration = synthetic_schema
    migration.upgrade()
    _provider(conn, schema)
    conn.exec_driver_sql(f"UPDATE \"{schema}\".alembic_version SET version_num='0153'")
    old_key, new_key = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    old, new = Fernet(old_key.encode()), Fernet(new_key.encode())
    _rotation_stores(conn, schema, old)
    before = _version(conn, schema)
    counts = rotate_in_transaction(conn, schema=schema, old_key=old_key, new_key=new_key, apply=False)
    assert _version(conn, schema) == before
    assert all(
        counts[k] == 1
        for k in (
            "personal_access",
            "personal_refresh",
            "workspace_connections",
            "llm_providers",
            "personal_authorizations",
            "telegram_login_attempts",
        )
    )
    with pytest.raises(RotationError, match="cannot be decrypted"), conn.begin_nested():
        rotate_in_transaction(conn, schema=schema, old_key=Fernet.generate_key().decode(), new_key=new_key, apply=True)
    assert _version(conn, schema) == before
    assert rotate_in_transaction(conn, schema=schema, old_key=old_key, new_key=new_key, apply=True) == counts
    assert _version(conn, schema) == before + 1
    columns = (
        ("user_credentials", "secret_encrypted"),
        ("user_credentials", "refresh_secret_encrypted"),
        ("workspace_connection_secrets", "secret_encrypted"),
        ("llm_providers", "encrypted_api_key"),
        ("personal_connector_authorizations", "secret_encrypted"),
        ("telegram_login_attempts", "secret_encrypted"),
    )
    for table, column in columns:
        identity = "connection_id" if table == "workspace_connection_secrets" else "id"
        ciphertext = conn.exec_driver_sql(f'SELECT {column} FROM "{schema}".{table} WHERE {identity}=1').scalar_one()
        assert new.decrypt(ciphertext.encode()).startswith(b"SYNTHETIC_")
        with pytest.raises(InvalidToken):
            old.decrypt(ciphertext.encode())
        assert ciphertext not in caplog.text
    assert "SYNTHETIC_" not in caplog.text and old_key not in caplog.text and new_key not in caplog.text


def test_private_rotation_purge_needs_existing_explicit_approval(synthetic_schema):
    conn, schema, migration = synthetic_schema
    migration.upgrade()
    _provider(conn, schema)
    conn.exec_driver_sql(f"UPDATE \"{schema}\".alembic_version SET version_num='0153'")
    old_key, new_key = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    _rotation_stores(conn, schema, Fernet(old_key.encode()))
    conn.exec_driver_sql(f'INSERT INTO "{schema}".file_insight_mounts VALUES (1)')
    before = _version(conn, schema)
    with pytest.raises(RotationError, match="explicit approval"):
        rotate_in_transaction(conn, schema=schema, old_key=old_key, new_key=new_key, apply=True)
    assert _version(conn, schema) == before
