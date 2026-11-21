"""Migration operation checks with doubles; real PostgreSQL cycle is separate."""

from importlib import import_module
from unittest.mock import MagicMock

import pytest
from sqlalchemy import Column

from app.core.config import settings
from app.models import Base

__source_only__ = True
migration = import_module("migrations.versions.p22t04_task_runs")


def test_create_table_columns_match_models(monkeypatch):
    op = MagicMock()
    monkeypatch.setattr(migration, "op", op)
    migration.upgrade()
    tables = {call.args[0]: call.args[1:] for call in op.create_table.call_args_list}
    for name in ("task_triggers", "task_runs"):
        actual = {item.name: item for item in tables[name] if isinstance(item, Column)}
        expected = Base.metadata.tables[f"{settings.DB_SCHEMA}.{name}"].columns
        assert set(actual) == set(expected.keys())
        for column in expected:
            created = actual[column.name]
            assert (str(created.type), created.nullable, created.primary_key) == (
                str(column.type),
                column.nullable,
                column.primary_key,
            )
    op.create_foreign_key.assert_called_once()
    assert op.create_foreign_key.call_args.args[0] == "fk_job_task_run_scope"


@pytest.mark.parametrize("runs,triggers", [(True, False), (False, True)])
def test_populated_downgrade_refuses_to_drop(monkeypatch, runs, triggers):
    bind = MagicMock()
    bind.dialect.identifier_preparer.quote_schema = lambda schema: '"' + schema + '"'
    bind.scalar.side_effect = [runs, triggers]
    op = MagicMock()
    op.get_bind.return_value = bind
    monkeypatch.setattr(migration, "op", op)
    with pytest.raises(RuntimeError, match="Downgrade refused"):
        migration.downgrade()
    op.drop_table.assert_not_called()
    op.drop_column.assert_not_called()


def test_empty_downgrade_drops_job_reference_before_run(monkeypatch):
    bind = MagicMock()
    bind.dialect.identifier_preparer.quote_schema = lambda schema: '"' + schema + '"'
    bind.scalar.side_effect = [False, False]
    op = MagicMock()
    op.get_bind.return_value = bind
    monkeypatch.setattr(migration, "op", op)
    migration.downgrade()
    calls = [call[0] for call in op.method_calls]
    assert calls.index("drop_constraint") < calls.index("drop_table")
    assert [call.args[0] for call in op.drop_table.call_args_list] == ["task_runs", "task_triggers"]
