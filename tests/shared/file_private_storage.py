"""AST-isolated real ORM declarations plus fake sessions; never settings/DB engines."""

import ast
import hashlib
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import Column, ForeignKey, Integer, MetaData, Table
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column


def source_module(path, name, inject, denied):
    tree = ast.parse(Path(path).read_text(), filename=path)
    tree.body = [
        node
        for node in tree.body
        if not (
            isinstance(node, ast.ImportFrom)
            and node.module
            and any(node.module.startswith(prefix) for prefix in denied)
        )
    ]
    module = types.ModuleType(name)
    module.__dict__.update(inject)
    sys.modules[name] = module
    exec(compile(tree, path, "exec"), module.__dict__)
    return module


def storage_modules():
    class Base(DeclarativeBase):
        metadata = MetaData(schema="private_test")

    class TenantScopedMixin:
        @declared_attr
        def tenant_id(cls) -> Mapped[int]:
            return mapped_column(ForeignKey("private_test.tenants.id"), nullable=False)

    class TimestampMixin:
        @declared_attr
        def created_at(cls):
            from sqlalchemy import DateTime

            return mapped_column(DateTime(timezone=True))

        @declared_attr
        def updated_at(cls):
            from sqlalchemy import DateTime

            return mapped_column(DateTime(timezone=True))

    for name, columns in {
        "tenants": ("id",),
        "users": ("id",),
        "workspace_connections": ("id", "tenant_id"),
        "file_catalog_objects": ("id", "tenant_id", "connection_id"),
        "workspace_connection_secrets": ("connection_id",),
    }.items():
        Table(
            name,
            Base.metadata,
            *(Column(c, Integer, primary_key=c in ("id", "connection_id") and c == columns[0]) for c in columns),
        )
    models = source_module(
        "app/models/file_workspace/private_insights.py",
        "_private_storage_models",
        dict(
            Base=Base,
            TenantScopedMixin=TenantScopedMixin,
            TimestampMixin=TimestampMixin,
            settings=SimpleNamespace(DB_SCHEMA="private_test"),
        ),
        ("app.core", "app.models"),
    )

    # Declarative placeholders only; they never connect or issue SQL.
    class FileCatalogObject(Base):
        __table__ = Base.metadata.tables["private_test.file_catalog_objects"]

    class WorkspaceConnectionSecret(Base):
        __table__ = Base.metadata.tables["private_test.workspace_connection_secrets"]

    inject = {
        n: getattr(models, n)
        for n in ("FileInsightMount", "FileInsightObjectState", "FileInsightAttempt", "FilePrivateInsight")
    }
    inject.update(
        FileCatalogObject=FileCatalogObject,
        WorkspaceConnectionSecret=WorkspaceConnectionSecret,
        _authorize=AsyncMock(),
        _connection=AsyncMock(),
        _cloud_policy=lambda c: (SimpleNamespace(permits=lambda *a: True), "disk:/work"),
        _key=lambda s: hashlib.sha256(s.encode()).hexdigest(),
        _policy_key=lambda c: "policy-key",
        async_session_maker=None,
    )
    manager = source_module(
        "app/models/managers/file_insight_manager.py", "_private_storage_manager", inject, ("app.core", "app.models")
    )
    workflow = source_module(
        "app/services/file_workspace/private_insights.py",
        "_private_storage_workflow",
        dict(PrivateInsightUnavailable=manager.PrivateInsightUnavailable),
        ("app.models",),
    )
    return models, manager, workflow


class FakeSession:
    def __init__(self, scalars=()):
        self.queue = list(scalars)
        self.added = []
        self.executed = []
        self.flushed = 0

    async def scalar(self, query):
        self.executed.append(query)
        return self.queue.pop(0)

    async def execute(self, query):
        self.executed.append(query)
        return SimpleNamespace(one=lambda: (0, 0))

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        self.flushed += 1
