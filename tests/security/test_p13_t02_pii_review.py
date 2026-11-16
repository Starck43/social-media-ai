"""DB-exempt checks for the P13-T02 review fixes."""

import sys
from types import SimpleNamespace

import pytest
import typer
from sqlalchemy import Column, Integer, MetaData, String, Table
from sqlalchemy.sql.dml import Update

from app.core import pii
from cli.commands import pii as pii_cli


def _table():
    return Table(
        "probe",
        MetaData(),
        Column("id", Integer),
        Column("email_encrypted", String),
        Column("pii_updated_at", Integer),
    )


class _Session:
    def __init__(self, rowcount):
        self.rowcount = rowcount
        self.statements = []
        self.selects = 0
        self.commits = 0

    def execute(self, statement):
        self.statements.append(statement)
        if isinstance(statement, Update):
            return SimpleNamespace(rowcount=self.rowcount)
        self.selects += 1
        return SimpleNamespace(all=lambda: [(7, "old-ciphertext", 1)] if self.selects == 1 else [])

    def commit(self):
        self.commits += 1


@pytest.mark.parametrize("rowcount, expected", [(0, (1, 0, 1)), (1, (1, 1, 0))])
def test_rotation_conditions_write_on_snapshot(monkeypatch, rowcount, expected):
    monkeypatch.setattr(pii_cli, "_table", lambda *_: _table())
    monkeypatch.setattr(pii, "is_active_ciphertext", lambda _: False)
    monkeypatch.setattr(pii, "decrypt_pii", lambda _: "synthetic")
    monkeypatch.setattr(pii, "encrypt_pii", lambda _: "new-ciphertext")
    session = _Session(rowcount)
    assert pii_cli._rotate_table(session, "probe", "email_encrypted", batch=10, limit=None) == expected
    update = next(statement for statement in session.statements if isinstance(statement, Update))
    where = str(update.whereclause)
    assert "probe.id =" in where and "probe.email_encrypted =" in where
    parameters = update.compile().params
    assert 7 in parameters.values() and "old-ciphertext" in parameters.values()
    assert session.commits == 1


def test_rotation_refuses_unknown_update_result(monkeypatch):
    monkeypatch.setattr(pii_cli, "_table", lambda *_: _table())
    monkeypatch.setattr(pii, "is_active_ciphertext", lambda _: False)
    monkeypatch.setattr(pii, "decrypt_pii", lambda _: "synthetic")
    monkeypatch.setattr(pii, "encrypt_pii", lambda _: "new-ciphertext")
    with pytest.raises(pii.PIIError, match="could not verify"):
        pii_cli._rotate_table(_Session(-1), "probe", "email_encrypted", batch=10, limit=None)


def test_conflicts_fail_command_and_close_session(monkeypatch):
    session = SimpleNamespace(closed=False)
    session.close = lambda: setattr(session, "closed", True)
    monkeypatch.setitem(sys.modules, "app.core.database", SimpleNamespace(SessionLocal=lambda: session))
    monkeypatch.setattr(pii_cli, "REGISTRY", (("probe", "email_encrypted", "email"),))
    monkeypatch.setattr(pii_cli, "_rotate_table", lambda *args, **kwargs: (1, 0, 1))
    with pytest.raises(typer.Exit) as error:
        pii_cli.rotate(table=None, batch=10, limit=None, dry_run=False)
    assert error.value.exit_code == 1
    assert session.closed


def test_index_rejected_before_table_lookup(monkeypatch):
    monkeypatch.setattr(pii_cli, "_table", lambda *_: pytest.fail("must not load a table"))
    with pytest.raises(pii.PIIError, match="blind-index"):
        pii_cli._rotate_table(None, "probe", "email_index", batch=10, limit=None)
