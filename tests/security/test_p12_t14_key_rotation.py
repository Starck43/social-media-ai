"""Shared-key rotation against a transaction-scoped synthetic PostgreSQL schema."""

from __future__ import annotations

import json
import secrets

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet, InvalidToken

from app.core.database import engine
from scripts.ops.rotate_credentials_key import RotationError, main, rotate_in_transaction


class RotationFixture:
    def __init__(self, conn, schema, old_key, new_key):
        self.values = (conn, schema, old_key, new_key)

    def __iter__(self):
        return iter(self.values)

    def __repr__(self):
        return "<redacted synthetic rotation fixture>"


@pytest.fixture
def rotation_schema():
    schema = "test_p12t14_rotate_" + secrets.token_hex(6)
    old_key, new_key = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    old = Fernet(old_key.encode())
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            conn.exec_driver_sql(f'CREATE TABLE "{schema}".alembic_version (version_num TEXT NOT NULL)')
            conn.exec_driver_sql(f'INSERT INTO "{schema}".alembic_version VALUES (%s)', ("0124",))
            conn.exec_driver_sql(
                f'CREATE TABLE "{schema}".user_credentials ('
                "id INTEGER PRIMARY KEY, platform TEXT NOT NULL, kind TEXT NOT NULL, meta JSON, "
                "secret_encrypted TEXT NOT NULL, refresh_secret_encrypted TEXT, "
                "CONSTRAINT ck_user_credentials_vk_refresh_secret CHECK ("
                "platform <> 'vk' OR kind <> 'user_token' OR ("
                "(meta IS NULL OR NOT (CAST(meta AS jsonb) ? 'refresh_token')) AND "
                "((COALESCE(CAST(meta AS jsonb) -> 'refresh_available', 'false'::jsonb) = 'true'::jsonb) "
                "= (refresh_secret_encrypted IS NOT NULL)))))"
            )
            conn.exec_driver_sql(
                f'CREATE TABLE "{schema}".workspace_connection_secrets '
                "(connection_id INTEGER PRIMARY KEY, secret_encrypted TEXT NOT NULL)"
            )
            conn.exec_driver_sql(
                f'CREATE TABLE "{schema}".llm_providers (id INTEGER PRIMARY KEY, encrypted_api_key TEXT)'
            )
            conn.exec_driver_sql(
                f'INSERT INTO "{schema}".user_credentials VALUES (%s,%s,%s,%s::json,%s,%s)',
                (
                    1,
                    "vk",
                    "user_token",
                    json.dumps({"refresh_available": True}),
                    old.encrypt(b"SYNTH_ACCESS").decode(),
                    old.encrypt(b"SYNTH_REFRESH").decode(),
                ),
            )
            conn.exec_driver_sql(
                f'INSERT INTO "{schema}".workspace_connection_secrets VALUES (%s,%s),(%s,%s)',
                (1, old.encrypt(b"SYNTH_WORKSPACE").decode(), 2, ""),
            )
            conn.exec_driver_sql(
                f'INSERT INTO "{schema}".llm_providers VALUES (%s,%s),(%s,%s)',
                (1, old.encrypt(b"SYNTH_LLM").decode(), 2, None),
            )
            yield RotationFixture(conn, schema, old_key, new_key)
        finally:
            transaction.rollback()
        assert conn.execute(sa.text("SELECT to_regnamespace(:name)"), {"name": schema}).scalar() is None


def _ciphertexts(conn, schema):
    access, refresh = conn.exec_driver_sql(
        f'SELECT secret_encrypted, refresh_secret_encrypted FROM "{schema}".user_credentials WHERE id=1'
    ).one()
    workspace = conn.exec_driver_sql(
        f'SELECT secret_encrypted FROM "{schema}".workspace_connection_secrets WHERE connection_id=1'
    ).scalar_one()
    llm = conn.exec_driver_sql(f'SELECT encrypted_api_key FROM "{schema}".llm_providers WHERE id=1').scalar_one()
    return access, refresh, workspace, llm


def test_dry_run_and_atomic_rotation_covers_all_stores(rotation_schema):
    conn, schema, old_key, new_key = rotation_schema
    before = _ciphertexts(conn, schema)
    counts = rotate_in_transaction(conn, schema=schema, old_key=old_key, new_key=new_key, apply=False)
    assert counts == {"personal_access": 1, "personal_refresh": 1, "workspace_connections": 1, "llm_providers": 1}
    assert _ciphertexts(conn, schema) == before
    assert rotate_in_transaction(conn, schema=schema, old_key=old_key, new_key=new_key, apply=True) == counts
    after = _ciphertexts(conn, schema)
    fresh, old = Fernet(new_key.encode()), Fernet(old_key.encode())
    assert [fresh.decrypt(value.encode()) for value in after] == [
        b"SYNTH_ACCESS",
        b"SYNTH_REFRESH",
        b"SYNTH_WORKSPACE",
        b"SYNTH_LLM",
    ]
    for value in after:
        with pytest.raises(InvalidToken):
            old.decrypt(value.encode())
    assert (
        conn.exec_driver_sql(
            f'SELECT secret_encrypted FROM "{schema}".workspace_connection_secrets WHERE connection_id=2'
        ).scalar_one()
        == ""
    )


def test_wrong_key_mid_rotation_rolls_back_all_writes(rotation_schema, caplog):
    conn, schema, old_key, new_key = rotation_schema
    wrong = Fernet(Fernet.generate_key()).encrypt(b"SYNTH_BAD").decode()
    conn.exec_driver_sql(f'UPDATE "{schema}".user_credentials SET refresh_secret_encrypted=%s WHERE id=1', (wrong,))
    before = _ciphertexts(conn, schema)
    with pytest.raises(RotationError, match="cannot be decrypted"), conn.begin_nested():
        rotate_in_transaction(conn, schema=schema, old_key=old_key, new_key=new_key, apply=True)
    assert _ciphertexts(conn, schema) == before
    assert "SYNTH_BAD" not in caplog.text
    assert old_key not in caplog.text and new_key not in caplog.text


def test_missing_guard_and_old_revision_fail_closed(rotation_schema):
    conn, schema, old_key, new_key = rotation_schema
    before = _ciphertexts(conn, schema)
    conn.exec_driver_sql(f'UPDATE "{schema}".alembic_version SET version_num=%s', ("0123",))
    with pytest.raises(RotationError, match="migration first"):
        rotate_in_transaction(conn, schema=schema, old_key=old_key, new_key=new_key, apply=True)
    conn.exec_driver_sql(f'UPDATE "{schema}".alembic_version SET version_num=%s', ("0124",))
    conn.exec_driver_sql(
        f'ALTER TABLE "{schema}".user_credentials DROP CONSTRAINT ck_user_credentials_vk_refresh_secret'
    )
    with pytest.raises(RotationError, match="guard is missing"):
        rotate_in_transaction(conn, schema=schema, old_key=old_key, new_key=new_key, apply=True)
    assert _ciphertexts(conn, schema) == before


def test_cli_requires_approval_and_backup_before_connecting(capsys):
    with pytest.raises(SystemExit) as first:
        main([])
    assert first.value.code == 2
    with pytest.raises(SystemExit) as second:
        main(["--target-approved", "--writers-stopped", "--apply"])
    assert second.value.code == 2
    assert "--backup-confirmed" in capsys.readouterr().err


def test_same_key_rejected_without_writes(rotation_schema):
    conn, schema, old_key, _ = rotation_schema
    before = _ciphertexts(conn, schema)
    with pytest.raises(RotationError, match="different keys"):
        rotate_in_transaction(conn, schema=schema, old_key=old_key, new_key=old_key, apply=True)
    assert _ciphertexts(conn, schema) == before


def test_verbose_sql_logging_fails_closed(rotation_schema, monkeypatch):
    import logging

    conn, schema, old_key, new_key = rotation_schema
    before = _ciphertexts(conn, schema)
    logger = logging.getLogger("sqlalchemy.engine.Engine")
    old_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        with pytest.raises(RotationError, match="Disable SQL"):
            rotate_in_transaction(conn, schema=schema, old_key=old_key, new_key=new_key, apply=True)
    finally:
        logger.setLevel(old_level)
    assert _ciphertexts(conn, schema) == before


def _authorization_store(conn, schema, old_key):
    conn.exec_driver_sql(f'UPDATE "{schema}".alembic_version SET version_num=%s', ("p12t14_accounts",))
    conn.exec_driver_sql(
        f'CREATE TABLE "{schema}".personal_connector_authorizations '
        "(id INTEGER PRIMARY KEY, secret_encrypted TEXT NOT NULL)"
    )
    conn.exec_driver_sql(
        f'INSERT INTO "{schema}".personal_connector_authorizations VALUES (%s,%s),(%s,%s)',
        (1, Fernet(old_key.encode()).encrypt(b"SYNTH_PERSONAL_AUTHORIZATION_ENVELOPE").decode(), 2, ""),
    )


def test_unified_head_rotates_personal_authorization_without_copying_grants(rotation_schema):
    conn, schema, old_key, new_key = rotation_schema
    _authorization_store(conn, schema, old_key)
    before = (
        conn.exec_driver_sql(f'SELECT secret_encrypted FROM "{schema}".personal_connector_authorizations ORDER BY id')
        .scalars()
        .all()
    )
    counts = rotate_in_transaction(conn, schema=schema, old_key=old_key, new_key=new_key, apply=False)
    assert counts["personal_authorizations"] == 1
    assert (
        conn.exec_driver_sql(f'SELECT secret_encrypted FROM "{schema}".personal_connector_authorizations ORDER BY id')
        .scalars()
        .all()
        == before
    )
    assert rotate_in_transaction(conn, schema=schema, old_key=old_key, new_key=new_key, apply=True) == counts
    after = (
        conn.exec_driver_sql(f'SELECT secret_encrypted FROM "{schema}".personal_connector_authorizations ORDER BY id')
        .scalars()
        .all()
    )
    assert after[1] == ""  # locally revoked material stays revoked
    assert Fernet(new_key.encode()).decrypt(after[0].encode()) == b"SYNTH_PERSONAL_AUTHORIZATION_ENVELOPE"
    with pytest.raises(InvalidToken):
        Fernet(old_key.encode()).decrypt(after[0].encode())


def test_unreadable_personal_authorization_rolls_back_all_earlier_stores(rotation_schema):
    conn, schema, old_key, new_key = rotation_schema
    _authorization_store(conn, schema, old_key)
    wrong = Fernet(Fernet.generate_key()).encrypt(b"SYNTH_WRONG_AUTHORIZATION").decode()
    conn.exec_driver_sql(
        f'UPDATE "{schema}".personal_connector_authorizations SET secret_encrypted=%s WHERE id=1', (wrong,)
    )
    before = _ciphertexts(conn, schema)
    with pytest.raises(RotationError, match="cannot be decrypted"), conn.begin_nested():
        rotate_in_transaction(conn, schema=schema, old_key=old_key, new_key=new_key, apply=True)
    assert _ciphertexts(conn, schema) == before
    assert (
        conn.exec_driver_sql(
            f'SELECT secret_encrypted FROM "{schema}".personal_connector_authorizations WHERE id=1'
        ).scalar_one()
        == wrong
    )


@pytest.mark.parametrize("revision", ["9999", "unknown", "p12t14_accounts"])
def test_future_or_incomplete_store_generation_is_refused(rotation_schema, revision):
    conn, schema, old_key, new_key = rotation_schema
    conn.exec_driver_sql(f'UPDATE "{schema}".alembic_version SET version_num=%s', (revision,))
    before = _ciphertexts(conn, schema)
    with pytest.raises(RotationError):
        rotate_in_transaction(conn, schema=schema, old_key=old_key, new_key=new_key, apply=True)
    assert _ciphertexts(conn, schema) == before
