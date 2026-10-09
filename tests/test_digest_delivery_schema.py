"""Exercise the migration against an isolated schema in the sandbox test DB."""

import importlib.util
from pathlib import Path
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.dialects import postgresql, sqlite

from app.core.database import engine
from app.models import DigestRun


def test_digest_checkpoint_column_is_nullable_jsonb():
    column = DigestRun.__table__.c.delivery_state
    assert column.nullable is True
    assert column.type.compile(dialect=postgresql.dialect()) == "JSONB"
    assert column.type.compile(dialect=sqlite.dialect()) == "JSON"
    assert column.default is None and column.server_default is None


def test_upgrade_and_downgrade_preserve_old_rows(monkeypatch):
    path = Path(__file__).parents[1] / "migrations/versions/0087_add_digest_delivery_state.py"
    spec = importlib.util.spec_from_file_location("digest_checkpoint_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    schema = f"receipt_migration_{uuid4().hex}"
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        try:
            connection.execute(text(f'CREATE TABLE "{schema}".digest_runs (id integer PRIMARY KEY, content text)'))
            connection.execute(text(f"INSERT INTO \"{schema}\".digest_runs VALUES (1, 'existing report')"))
            monkeypatch.setattr(migration, "schema", schema)
            monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
            migration.upgrade()
            old = connection.execute(text(f'SELECT content, delivery_state FROM "{schema}".digest_runs')).one()
            assert old == ("existing report", None)
            connection.execute(
                text(f'UPDATE "{schema}".digest_runs SET delivery_state=CAST(:state AS jsonb)'),
                {"state": '{"version":1}'},
            )
            assert connection.execute(text(f'SELECT delivery_state FROM "{schema}".digest_runs')).scalar() == {
                "version": 1
            }
            migration.downgrade()
            assert connection.execute(text(f'SELECT id, content FROM "{schema}".digest_runs')).one() == (
                1,
                "existing report",
            )
            columns = (
                connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns WHERE table_schema=:schema AND table_name='digest_runs'"
                    ),
                    {"schema": schema},
                )
                .scalars()
                .all()
            )
            assert "delivery_state" not in columns
            migration.upgrade()
            assert connection.execute(text(f'SELECT delivery_state FROM "{schema}".digest_runs')).scalar() is None
        finally:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
