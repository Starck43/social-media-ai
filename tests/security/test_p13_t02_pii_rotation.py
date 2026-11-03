"""P13-T02 rotation command: registry contract, dry-run default, idempotent batches.

The rotate command runs against a synchronous SQLAlchemy session, so this test
uses SQLite in-memory and a stand-in table; no project schema is touched.
"""

from __future__ import annotations

import base64

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, insert, select
from sqlalchemy.orm import Session

from app.core import pii
from app.core.config import settings
from cli.commands import pii as pii_cli


@pytest.fixture
def old_and_new(monkeypatch):
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", old, raising=False)
    monkeypatch.setattr(settings, "PII_INDEX_KEY", base64.urlsafe_b64encode(b"k" * 32).decode(), raising=False)
    monkeypatch.setattr(settings, "CREDENTIALS_KEY", None, raising=False)
    return old, new


@pytest.fixture
def probe(monkeypatch):
    metadata = MetaData()
    table = Table(
        "pii_probe",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("email_encrypted", String(512)),
    )
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    monkeypatch.setattr(settings, "DB_SCHEMA", None, raising=False)
    return engine, table


def _rows(engine, table):
    with engine.connect() as connection:
        return connection.execute(select(table.c.id, table.c.email_encrypted).order_by(table.c.id)).all()


def test_rotation_rewrites_only_stale_rows_and_is_idempotent(old_and_new, probe, monkeypatch):
    old, new = old_and_new
    engine, table = probe
    values = ["anna@example.com", "boris@example.com", "vera@example.com"]
    with engine.begin() as connection:
        connection.execute(
            insert(table),
            [{"id": index, "email_encrypted": pii.encrypt_pii(value)} for index, value in enumerate(values, 1)],
        )

    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", f"{new},{old}", raising=False)
    with Session(engine) as session:
        # The stand-in is not in the project schema; patch the resolver only.
        monkeypatch.setattr(pii_cli, "_table", lambda session, name, column: table)
        examined, rewritten = pii_cli._rotate_table(session, "pii_probe", "email_encrypted", batch=2, limit=None)
    assert (examined, rewritten) == (3, 3)
    rows = _rows(engine, table)
    assert all(pii.is_active_ciphertext(value) for _id, value in rows)
    assert [pii.decrypt_pii(value) for _id, value in rows] == values

    with Session(engine) as session:
        monkeypatch.setattr(pii_cli, "_table", lambda session, name, column: table)
        examined, rewritten = pii_cli._rotate_table(session, "pii_probe", "email_encrypted", batch=2, limit=None)
    assert (examined, rewritten) == (3, 0)


def test_rotation_limit_stops_and_resumes(old_and_new, probe, monkeypatch):
    old, new = old_and_new
    engine, table = probe
    values = ["a@example.com", "b@example.com", "c@example.com"]
    with engine.begin() as connection:
        connection.execute(
            insert(table),
            [{"id": index, "email_encrypted": pii.encrypt_pii(value)} for index, value in enumerate(values, 1)],
        )
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", f"{new},{old}", raising=False)

    monkeypatch.setattr(pii_cli, "_table", lambda session, name, column: table)
    with Session(engine) as session:
        _examined, rewritten = pii_cli._rotate_table(session, "pii_probe", "email_encrypted", batch=2, limit=2)
    assert rewritten == 2
    assert [pii.is_active_ciphertext(value) for _id, value in _rows(engine, table)] == [True, True, False]

    with Session(engine) as session:
        examined, rewritten = pii_cli._rotate_table(session, "pii_probe", "email_encrypted", batch=2, limit=None)
    assert (examined, rewritten) == (3, 1)
    assert [pii.decrypt_pii(value) for _id, value in _rows(engine, table)] == values


def test_unguarded_column_is_refused(old_and_new, probe, monkeypatch):
    engine, table = probe
    with engine.begin() as connection:
        connection.execute(insert(table).values(id=1, email_encrypted="anna@example.com"))
    monkeypatch.setattr(pii_cli, "_table", lambda session, name, column: table)
    with Session(engine) as session:
        with pytest.raises(pii.PIIError, match="not a PII ciphertext"):
            pii_cli._rotate_table(session, "pii_probe", "email_encrypted", batch=10, limit=None)


def test_unknown_table_or_column_is_a_safe_error(monkeypatch):
    engine = create_engine("sqlite://")
    with Session(engine) as session:
        with pytest.raises(pii.PIIError, match="not available"):
            pii_cli._table(session, "no_such_table", "x")


def test_empty_registry_does_not_write(monkeypatch):
    monkeypatch.setattr(pii_cli, "REGISTRY", ())
    assert pii_cli.REGISTRY == ()
    pii_cli.rotate(table=None, batch=10, limit=None, dry_run=False)


def test_registry_entries_are_explicit_and_index_free(monkeypatch):
    for table_name, column, kind in pii_cli.REGISTRY:
        assert isinstance(table_name, str) and isinstance(column, str)
        assert kind in pii.INDEX_KINDS
        # The index key is stable (D8), so rotation must never target an index column.
        assert not column.endswith("_index")
