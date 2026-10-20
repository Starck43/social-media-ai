"""Offline canonical ORM metadata audit. No app config, managers, engine or SQL execution.

Runs in its own process: only settings constants and manager binding are replaced.
Actual domain model declarations and SQLAlchemy mapper configuration are unchanged.
"""

from __future__ import annotations

import ast
import importlib.abc
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def load_models():
    class NoDatabaseImports(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "app.core.database" or fullname.startswith("app.models.managers"):
                raise AssertionError(f"Database dependency forbidden in offline audit: {fullname}")

    sys.meta_path.insert(0, NoDatabaseImports())
    sys.path.insert(0, str(ROOT))
    config = ModuleType("app.core.config")
    config.settings = SimpleNamespace(DB_SCHEMA="offline_audit", JOB_MAX_ATTEMPTS=3)
    sys.modules[config.__name__] = config
    facade = ModuleType("app.models")
    facade.__path__ = [str(ROOT / "app/models")]
    facade.__package__ = "app.models"
    sys.modules[facade.__name__] = facade
    path = ROOT / "app/models/__init__.py"
    tree = ast.parse(path.read_text())
    tree.body = [
        node
        for node in tree.body
        if not (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and getattr(node.value.func, "id", "") == "_bind_managers"
        )
    ]
    exec(compile(tree, str(path), "exec"), facade.__dict__)
    from sqlalchemy.orm import configure_mappers

    configure_mappers()
    return facade


def inventory():
    models = load_models()
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable

    tables, relationships, cascades = [], [], []
    for table in sorted(models.Base.metadata.tables.values(), key=lambda t: t.name):
        fks = []
        for fk in sorted(table.foreign_key_constraints, key=lambda c: tuple(c.column_keys)):
            fks.append(
                dict(
                    columns=list(fk.column_keys),
                    target_table=fk.referred_table.name,
                    target_columns=[e.column.name for e in fk.elements],
                    ondelete=fk.ondelete or "NO ACTION",
                    nullable=[e.parent.nullable for e in fk.elements],
                )
            )
        tables.append(
            dict(table=table.name, foreign_keys=fks, ddl=str(CreateTable(table).compile(dialect=postgresql.dialect())))
        )
    for mapper in sorted(models.Base.registry.mappers, key=lambda m: m.class_.__name__):
        for rel in mapper.relationships:
            relationships.append(
                dict(
                    model=mapper.class_.__name__,
                    table=mapper.local_table.name,
                    name=rel.key,
                    target=rel.mapper.class_.__name__,
                    cascade=sorted(rel.cascade),
                    passive_deletes=rel.passive_deletes,
                    direction=rel.direction.name,
                )
            )
            if rel.cascade.delete and rel.direction.name == "ONETOMANY":
                parent, child = mapper.class_(), rel.mapper.class_()
                getattr(parent, rel.key).append(child)
                # Real ORM cascade traversal, but NOT a DB delete or a flush.
                reached = list(mapper.cascade_iterator("delete", parent._sa_instance_state))
                cascades.append(
                    dict(
                        model=mapper.class_.__name__,
                        relationship=rel.key,
                        reaches_child=any(row[0] is child for row in reached),
                    )
                )
    return dict(
        tables=tables,
        relationships=relationships,
        cascades=cascades,
        models=sorted(mapper.class_.__name__ for mapper in models.Base.registry.mappers),
    )


if __name__ == "__main__":
    print(json.dumps(inventory(), ensure_ascii=False))
