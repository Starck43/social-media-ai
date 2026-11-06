"""Offline structural checks only. Not an upgrade/downgrade execution."""

import ast
import runpy
from pathlib import Path
from types import SimpleNamespace

import sqlalchemy as sa

from app.models.communications.outbox import OutboxMessage, OutboxTarget

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "migrations/versions/0141_outbox.py"
__source_only__ = True


def test_single_owner_assigned_revision_and_literal_parent():
    tree = ast.parse(PATH.read_text())
    assignments = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
    }
    assert assignments["revision"] == "0141" and assignments["down_revision"] == "0140"
    assert [p.name for p in PATH.parent.glob("0141*.py")] == ["0141_outbox.py"]


def test_migration_model_parity_indexes_triggers_and_clean_downgrade(monkeypatch):
    from alembic import op
    from sqlalchemy.dialects import postgresql

    tables = {}
    indexes = []
    statements = []
    registry_statements = []
    drops = []

    def create_table(name, *items, **kwargs):
        tables[name] = sa.Table(name, sa.MetaData(), *items, **kwargs)

    monkeypatch.setattr(op, "create_table", create_table)
    monkeypatch.setattr(op, "create_index", lambda *a, **kw: indexes.append((a, kw)))
    monkeypatch.setattr(op, "execute", lambda sql: statements.append(str(sql)))
    monkeypatch.setattr(op, "drop_table", lambda name, **kw: drops.append(name))
    bind = SimpleNamespace(
        dialect=postgresql.dialect(),
        execute=lambda sql, *args, **kwargs: registry_statements.append((str(sql), args, kwargs)),
    )
    monkeypatch.setattr(op, "get_bind", lambda: bind)
    module = runpy.run_path(str(PATH))
    module["upgrade"]()
    assert set(tables) == {"outbox_messages", "outbox_targets"}
    for model in (OutboxMessage, OutboxTarget):
        actual = tables[model.__tablename__]
        expected = model.__table__
        assert set(actual.columns.keys()) == set(expected.columns.keys())
        for name in expected.columns.keys():
            left, right = actual.columns[name], expected.columns[name]
            assert left.nullable == right.nullable
            assert str(left.type.compile()) == str(right.type.compile())

        def constraints(table):
            return sorted(
                (type(c).__name__, tuple(col.name for col in c.columns), str(getattr(c, "sqltext", "")))
                for c in table.constraints
            )

        assert constraints(actual) == constraints(expected)
    assert {entry[0][0] for entry in indexes} == {"ix_outbox_due", "ix_outbox_target_message"}
    assert len(statements) == 2
    assert sum("CREATE TRIGGER contact_pii_marker" in stmt for stmt in statements) == 2
    assert len(registry_statements) == 15
    migration_text = PATH.read_text().lower()
    assert "insert into" in migration_text and "comms.outbox" in migration_text
    assert "alter table" not in migration_text
    module["downgrade"]()
    assert drops == ["outbox_targets", "outbox_messages"]


def test_registry_owns_exact_outbox_matrix_and_generic_hook_is_skipped():
    source = PATH.read_text()
    assert 'resource_key = "comms.outbox"' in source
    assert "model_name, table_name" in source
    assert "ON CONFLICT (resource_key) DO NOTHING" in source
    assert '"0141"' in (ROOT / "migrations/env.py").read_text()
    assert "WORKSPACE_ROLE_PRESETS" in source
    presets = {
        "owner": ("VIEW", "UPDATE", "CONFIGURE"),
        "admin": ("VIEW", "UPDATE", "CONFIGURE"),
        "manager": ("VIEW", "UPDATE"),
        "analyst": ("VIEW",),
        "viewer": ("VIEW",),
    }
    namespace = {"__name__": "migration_probe"}
    exec(compile(source, str(PATH), "exec"), namespace)  # noqa: S102 - the revision module itself
    assert namespace["WORKSPACE_ROLE_PRESETS"] == presets
