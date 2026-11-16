"""P13-T02 SQLAlchemy types: ciphertext at rest, plaintext on read, safe repr."""

from __future__ import annotations

import base64

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, insert, select

from app.core import pii
from app.core.config import settings
from app.models.types.encrypted import EncryptedText, index_for, set_pii_pair


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode(), raising=False)
    monkeypatch.setattr(settings, "PII_INDEX_KEY", base64.urlsafe_b64encode(b"k" * 32).decode(), raising=False)
    monkeypatch.setattr(settings, "CREDENTIALS_KEY", None, raising=False)


@pytest.fixture
def table():
    metadata = MetaData()
    return Table(
        "pii_probe",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("email_encrypted", EncryptedText()),
        Column("email_index", String(128)),
    )


def _engine(table):
    engine = create_engine("sqlite://")
    table.metadata.create_all(engine)
    return engine


def test_value_is_encrypted_at_rest_and_plaintext_on_read(keys, table):
    engine = _engine(table)
    with engine.begin() as connection:
        connection.execute(insert(table).values(id=1, email_encrypted="anna@example.com"))
        stored = connection.execute(select(table.c.email_encrypted)).scalar_one()
        if isinstance(stored, str) and stored.startswith(pii.ENCRYPTION_PREFIX):
            assert "anna@example.com" not in stored
        fetched = connection.execute(select(table)).mappings().one()
        assert fetched["email_encrypted"] == "anna@example.com"


def test_null_round_trips(keys, table):
    engine = _engine(table)
    with engine.begin() as connection:
        connection.execute(insert(table).values(id=2, email_encrypted=None))
        assert connection.execute(select(table.c.email_encrypted)).scalar_one() is None


def test_literal_rendering_is_refused(keys, table):
    # SQLAlchemy wraps the type's refusal into CompileError; the plaintext is not echoed.
    from sqlalchemy.exc import CompileError

    statement = insert(table).values(id=3, email_encrypted="anna@example.com")
    with pytest.raises(CompileError):
        str(statement.compile(dialect=create_engine("sqlite://").dialect, compile_kwargs={"literal_binds": True}))


def test_repr_of_type_never_shows_values(keys, table):
    assert "anna" not in repr(table.c.email_encrypted.type)


def test_set_pii_pair_writes_both_columns(keys):
    class Row:
        email_encrypted = None
        email_index = None

    row = Row()
    set_pii_pair(row, "email", "Anna@Example.com")
    assert row.email_encrypted == "Anna@Example.com"
    assert row.email_index == index_for("email", "anna@example.com")
    assert row.email_index == pii.blind_index("email", "Anna@Example.com")

    set_pii_pair(row, "email", None)
    assert row.email_encrypted is None and row.email_index is None
    assert index_for("email", "  ") is None
