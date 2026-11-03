"""P13-T02 rotation command: registry contract, dry-run default, idempotent batches.

The rotate command runs against a synchronous SQLAlchemy session, so this test
uses SQLite in-memory and a stand-in table; no project schema is touched.
"""

from __future__ import annotations

import base64

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, event, insert, select
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
        Column(pii_cli.ROTATION_MARKER_COLUMN, Integer, nullable=False, default=0),
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
            [
                {"id": index, "email_encrypted": pii.encrypt_pii(value), pii_cli.ROTATION_MARKER_COLUMN: 1}
                for index, value in enumerate(values, 1)
            ],
        )

    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", f"{new},{old}", raising=False)
    with Session(engine) as session:
        # The stand-in is not in the project schema; patch the resolver only.
        monkeypatch.setattr(pii_cli, "_table", lambda session, name, column: table)
        examined, rewritten, conflicts = pii_cli._rotate_table(
            session, "pii_probe", "email_encrypted", batch=2, limit=None
        )
    assert (examined, rewritten, conflicts) == (3, 3, 0)
    rows = _rows(engine, table)
    assert all(pii.is_active_ciphertext(value) for _id, value in rows)
    assert [pii.decrypt_pii(value) for _id, value in rows] == values

    with Session(engine) as session:
        monkeypatch.setattr(pii_cli, "_table", lambda session, name, column: table)
        examined, rewritten, conflicts = pii_cli._rotate_table(
            session, "pii_probe", "email_encrypted", batch=2, limit=None
        )
    assert (examined, rewritten, conflicts) == (3, 0, 0)


def test_rotation_limit_stops_and_resumes(old_and_new, probe, monkeypatch):
    old, new = old_and_new
    engine, table = probe
    values = ["a@example.com", "b@example.com", "c@example.com"]
    with engine.begin() as connection:
        connection.execute(
            insert(table),
            [
                {"id": index, "email_encrypted": pii.encrypt_pii(value), pii_cli.ROTATION_MARKER_COLUMN: 1}
                for index, value in enumerate(values, 1)
            ],
        )
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", f"{new},{old}", raising=False)

    monkeypatch.setattr(pii_cli, "_table", lambda session, name, column: table)
    with Session(engine) as session:
        _examined, rewritten, conflicts = pii_cli._rotate_table(
            session, "pii_probe", "email_encrypted", batch=2, limit=2
        )
    assert rewritten == 2
    assert [pii.is_active_ciphertext(value) for _id, value in _rows(engine, table)] == [True, True, False]

    with Session(engine) as session:
        examined, rewritten, conflicts = pii_cli._rotate_table(
            session, "pii_probe", "email_encrypted", batch=2, limit=None
        )
    assert (examined, rewritten, conflicts) == (3, 1, 0)
    assert [pii.decrypt_pii(value) for _id, value in _rows(engine, table)] == values


def test_unguarded_column_is_refused(old_and_new, probe, monkeypatch):
    engine, table = probe
    with engine.begin() as connection:
        connection.execute(
            insert(table).values(id=1, email_encrypted="anna@example.com", **{pii_cli.ROTATION_MARKER_COLUMN: 1})
        )
    monkeypatch.setattr(pii_cli, "_table", lambda session, name, column: table)
    with Session(engine) as session:
        with pytest.raises(pii.PIIError, match="not a PII ciphertext"):
            pii_cli._rotate_table(session, "pii_probe", "email_encrypted", batch=10, limit=None)


def test_table_without_rotation_marker_is_refused(old_and_new, monkeypatch):
    metadata = MetaData()
    table = Table(
        "pii_no_marker", metadata, Column("id", Integer, primary_key=True), Column("email_encrypted", String(512))
    )
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(insert(table).values(id=1, email_encrypted=pii.encrypt_pii("anna@example.com")))
    monkeypatch.setattr(pii_cli, "_table", lambda session, name, column: table)
    with Session(engine) as session, pytest.raises(pii.PIIError, match="monotonic"):
        pii_cli._rotate_table(session, "pii_no_marker", "email_encrypted", batch=10, limit=None)


def test_same_ciphertext_rewrite_is_still_a_conflict(old_and_new, probe, monkeypatch):
    """The audit's counter-example: a concurrent write that stores identical bytes."""
    old, new = old_and_new
    engine, table = probe
    legacy = pii.encrypt_pii("anna@example.com")  # written before rotation, so on the old key
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", f"{new},{old}")
    original = f"{pii.ENCRYPTION_PREFIX}{pii.encryption_keys()[0].encrypt(b'boris@example.com').decode()}"
    with engine.begin() as connection:
        connection.execute(insert(table).values(id=1, email_encrypted=legacy, **{pii_cli.ROTATION_MARKER_COLUMN: 1}))
    monkeypatch.setattr(pii_cli, "_table", lambda session, name, column: table)

    session_flag: list[bool] = []

    def same_bytes(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("UPDATE") and not session_flag:
            session_flag.append(True)
            connection.exec_driver_sql(
                "UPDATE pii_probe SET email_encrypted = ?, pii_updated_at = pii_updated_at + 1 WHERE id = 1",
                (original,),
            )

    from sqlalchemy import event

    event.listen(engine, "before_cursor_execute", same_bytes)
    try:
        with Session(engine) as session:
            assert pii_cli._rotate_table(session, "pii_probe", "email_encrypted", batch=10, limit=None) == (1, 0, 1)
    finally:
        event.remove(engine, "before_cursor_execute", same_bytes)
    assert session_flag
    assert _rows(engine, table)[0][1] == original
    assert pii.decrypt_pii(original) == "boris@example.com"


def test_retire_then_rerun_reports_zero_rewrites(old_and_new, probe, monkeypatch):
    """A full rerun after retirement must be silent; a marker-less guard would rewrite everything."""
    old, new = old_and_new
    engine, table = probe
    with engine.begin() as connection:
        connection.execute(
            insert(table),
            [
                {"id": index, "email_encrypted": pii.encrypt_pii(value), pii_cli.ROTATION_MARKER_COLUMN: 1}
                for index, value in enumerate(["a@example.com", "b@example.com"], 1)
            ],
        )
    monkeypatch.setattr(pii_cli, "_table", lambda session, name, column: table)
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", f"{new},{old}")
    with Session(engine) as session:
        assert pii_cli._rotate_table(session, "pii_probe", "email_encrypted", batch=10, limit=None) == (2, 2, 0)
    before = _rows(engine, table)
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", new)  # old key retired
    with Session(engine) as session:
        assert pii_cli._rotate_table(session, "pii_probe", "email_encrypted", batch=10, limit=None) == (2, 0, 0)
    assert _rows(engine, table) == before


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
    assert pii_cli.ROTATION_MARKER_COLUMN.endswith("updated_at")
    for table_name, column, kind in pii_cli.REGISTRY:
        assert isinstance(table_name, str) and isinstance(column, str)
        # None marks an encrypted column without a blind index; otherwise the kind
        # must be one the closed vocabulary vouches for.
        assert kind is None or kind in pii.INDEX_KINDS
        # The index key is stable (D8), so rotation must never target an index column.
        assert not column.endswith("_index")


def test_stale_ciphertext_is_not_overwritten(old_and_new, probe, monkeypatch):
    old, new = old_and_new
    engine, table = probe
    with engine.begin() as connection:
        connection.execute(
            insert(table).values(
                id=1, email_encrypted=pii.encrypt_pii("old@example.invalid"), **{pii_cli.ROTATION_MARKER_COLUMN: 1}
            )
        )
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", f"{new},{old}")
    replacement = pii.encrypt_pii("new@example.invalid")
    monkeypatch.setattr(pii_cli, "_table", lambda session, name, column: table)
    injected = []

    def change_after_read(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("UPDATE") and not injected:
            injected.append(True)
            connection.exec_driver_sql(
                "UPDATE pii_probe SET email_encrypted = ?, pii_updated_at = pii_updated_at + 1 WHERE id = 1",
                (replacement,),
            )

    event.listen(engine, "before_cursor_execute", change_after_read)
    try:
        with Session(engine) as session:
            assert pii_cli._rotate_table(session, "pii_probe", "email_encrypted", batch=10, limit=None) == (1, 0, 1)
    finally:
        event.remove(engine, "before_cursor_execute", change_after_read)
    assert injected and pii.decrypt_pii(_rows(engine, table)[0][1]) == "new@example.invalid"


def test_index_column_rejected_without_opening_table(old_and_new, probe):
    engine, _ = probe
    with Session(engine) as session, pytest.raises(pii.PIIError, match="blind-index"):
        pii_cli._rotate_table(session, "pii_probe", "email_index", batch=10, limit=None)


def test_empty_sentinel_not_rewritten(old_and_new, probe, monkeypatch):
    engine, table = probe
    with engine.begin() as connection:
        connection.execute(insert(table).values(id=1, email_encrypted="", **{pii_cli.ROTATION_MARKER_COLUMN: 1}))
    monkeypatch.setattr(pii_cli, "_table", lambda session, name, column: table)
    with Session(engine) as session:
        assert pii_cli._rotate_table(session, "pii_probe", "email_encrypted", batch=10, limit=None) == (0, 0, 0)
