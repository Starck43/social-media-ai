"""0145 schema contract without PostgreSQL; no migration is applied here."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import UniqueConstraint

from app.models.planner.events import PlannerEventRun, PlannerEventSettings

__source_only__ = True


def migration():
    path = Path(__file__).resolve().parents[2] / "migrations/versions/0145_planner_event_planning.py"
    spec = importlib.util.spec_from_file_location("p16_t05_0145", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_and_database_identity_constraints():
    rev = migration()
    assert (rev.revision, rev.down_revision) == ("0145", "0144")
    assert PlannerEventSettings.__table__.c.owner_user_id.nullable
    assert PlannerEventRun.__table__.c.contact_id.nullable
    contact_fks = {
        tuple(f.parent.name for f in fk.elements): fk for fk in PlannerEventRun.__table__.foreign_key_constraints
    }
    assert contact_fks[("contact_id",)].ondelete == "SET NULL"
    assert contact_fks[("tenant_id", "contact_id")].deferrable
    assert contact_fks[("tenant_id", "contact_id")].initially == "DEFERRED"
    annual = next(c for c in PlannerEventRun.__table__.constraints if c.name == "uq_planner_event_year")
    assert annual.dialect_options["postgresql"]["nulls_not_distinct"] is not True
    unique = {
        tuple(column.name for column in constraint.columns)
        for constraint in PlannerEventRun.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("tenant_id", "contact_id", "event_type", "year") in unique
    assert ("tenant_id", "greeting_task_id") in unique


@pytest.mark.parametrize("has_data", [True, False])
def test_downgrade_locks_and_refuses_data_loss(monkeypatch, has_data):
    rev = migration()
    connection = MagicMock()
    connection.dialect.identifier_preparer.quote_schema.side_effect = lambda x: '"' + x + '"'
    connection.execute.return_value.scalar.return_value = has_data
    op = SimpleNamespace(get_bind=lambda: connection, drop_table=MagicMock())
    monkeypatch.setattr(rev, "op", op)
    if has_data:
        with pytest.raises(RuntimeError, match="discard"):
            rev.downgrade()
        op.drop_table.assert_not_called()
    else:
        rev.downgrade()
        assert op.drop_table.call_count == 2
    assert "LOCK TABLE" in str(connection.execute.call_args_list[0].args[0])


def test_upgrade_columns_and_constraints_match_models(monkeypatch):
    import sqlalchemy as sa

    rev = migration()
    captured = {}

    def create_table(name, *elements, **kwargs):
        captured[name] = (elements, kwargs)

    monkeypatch.setattr(rev, "op", SimpleNamespace(create_table=create_table))
    rev.upgrade()
    assert set(captured) == {"planner_event_settings", "planner_event_runs"}
    for model in (PlannerEventSettings, PlannerEventRun):
        table = model.__table__
        elements, kwargs = captured[table.name]
        columns = {element.name: element for element in elements if isinstance(element, sa.Column)}
        assert set(columns) == set(table.c.keys())
        assert kwargs["schema"] == table.schema
        for column in table.columns:
            migration_column = columns[column.name]
            assert type(column.type) is type(migration_column.type)
            assert column.nullable == migration_column.nullable
            assert column.primary_key == migration_column.primary_key
        declared = {element.name for element in elements if isinstance(element, sa.Constraint) and element.name}
        model_named = {constraint.name for constraint in table.constraints if constraint.name}
        assert declared == model_named
