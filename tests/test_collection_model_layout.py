"""Collection-domain compatibility regressions; prepared, NOT run by agent.

Bodies issue no SQL, but shared conftest initializes/seeds the test schema.
Use only reviewed owner setup; tests are sequential across ALL worktrees.
No schema reset/drop/stamp/migration or provider/messenger actions here.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import json
import pickle
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODELS = {
    "Platform": ("platform", "platforms", "PlatformManager", False),
    "Source": ("source", "sources", "SourceManager", True),
    "CollectedItem": ("collected_item", "collected_items", "CollectedItemManager", True),
}
# Schema expression snapshots from dev a983a156; no migration accepted here.
SCHEMA_FINGERPRINTS = {'CollectedItem': 'f6e2a3f3e0fcb200cbbaffc1998711148388b8b2050f008c653b20b6135ea334', 'Platform': '4a9416530007127cfc34ec0f987a61622b15ae4373c899933a640c4c5438564f', 'Source': '17dda3650de809cc517af68c2b1676df1f25c8e6f395a80419f2a9c2f9a54c4a'}


def _canonical_ast(node):
    # Explicit empty/None fields avoid Python3.12/3.13 ast.dump differences.
    if isinstance(node, ast.AST):
        return [type(node).__name__, [[field, _canonical_ast(value)] for field, value in ast.iter_fields(node)]]
    if isinstance(node, list):
        return [_canonical_ast(value) for value in node]
    return node


@pytest.mark.parametrize("name", MODELS)
def test_public_legacy_domain_and_leaf_paths_share_one_model_and_manager(name):
    module, table_name, manager_name, scoped = MODELS[name]
    public = importlib.import_module("app.models")
    legacy = importlib.import_module(f"app.models.{module}")
    domain = importlib.import_module("app.models.collection")
    leaf = importlib.import_module(f"app.models.collection.{module}")
    manager_module = importlib.import_module(f"app.models.managers.{module}_manager")
    cls = getattr(public, name)
    assert cls is getattr(legacy, name) is getattr(domain, name) is getattr(leaf, name)
    assert name in public.__all__ and name in domain.__all__
    assert domain.__dict__[name] is cls
    assert cls.__module__ == f"app.models.collection.{module}"
    assert cls._app_label == "social"
    assert cls.__tablename__ == table_name
    assert cls.__table__ is public.Base.metadata.tables[cls.__table__.fullname]
    assert sum(mapper.class_.__name__ == name for mapper in public.Base.registry.mappers) == 1
    assert isinstance(cls.objects, getattr(manager_module, manager_name))
    assert cls.objects.model is cls
    assert getattr(legacy, manager_name) is getattr(manager_module, manager_name)
    assert bool(getattr(cls, "__tenant_scoped__", False)) is scoped


@pytest.mark.parametrize("module,symbol", [
    ("platform", "PlatformType"), ("source", "SourceType"),
    ("platform", "Base"), ("source", "TenantScopedMixin"),
    ("collected_item", "TimestampMixin"),
])
def test_existing_legacy_imported_symbols_remain_available(module, symbol):
    legacy = importlib.import_module(f"app.models.{module}")
    leaf = importlib.import_module(f"app.models.collection.{module}")
    assert getattr(legacy, symbol) is getattr(leaf, symbol)


@pytest.mark.parametrize("name", MODELS)
def test_declarative_schema_expressions_unchanged(name):
    module = MODELS[name][0]
    tree = ast.parse((ROOT / f"app/models/collection/{module}.py").read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name)
    statements = [
        node for node in cls.body
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id in ("__tablename__", "__table_args__")
                    for target in node.targets)
        ) or (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.annotation, ast.Subscript)
            and isinstance(node.annotation.value, ast.Name)
            and node.annotation.value.id == "Mapped"
        )
    ]
    payload = _canonical_ast(ast.Module(body=statements, type_ignores=[]))
    fingerprint = hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert fingerprint == SCHEMA_FINGERPRINTS[name]


@pytest.mark.parametrize("name", MODELS)
def test_legacy_and_current_serialized_class_references_resolve(name):
    module = MODELS[name][0]
    cls = getattr(importlib.import_module(f"app.models.{module}"), name)
    # Locally constructed trusted GLOBAL reference, never external pickle input.
    old = f"capp.models.{module}\n{name}\n.".encode("ascii")
    assert pickle.loads(old) is cls
    assert pickle.loads(pickle.dumps(cls)) is cls


def test_relationships_soft_links_and_tenant_unique_source_contract_unchanged():
    from sqlalchemy import UniqueConstraint
    from sqlalchemy.orm import configure_mappers
    from app.models import AIAnalytics, CollectedItem, Platform, Source, Tenant

    configure_mappers()
    assert Source.__mapper__.relationships["platform"].mapper.class_ is Platform
    assert Platform.__mapper__.relationships["sources"].mapper.class_ is Source
    assert Source.__mapper__.relationships["tenant"].mapper.class_ is Tenant
    assert Source.__mapper__.relationships["analytics"].mapper.class_ is AIAnalytics
    assert AIAnalytics.__mapper__.relationships["source"].mapper.class_ is Source
    constraint = next(
        value for value in Source.__table__.constraints
        if isinstance(value, UniqueConstraint) and value.name == "uq_source_tenant_platform_external"
    )
    assert tuple(column.name for column in constraint.columns) == ("tenant_id", "platform_id", "external_id")
    assert not CollectedItem.__table__.c.source_id.foreign_keys
    assert not CollectedItem.__table__.c.run_id.foreign_keys


@pytest.mark.parametrize("value,expected", [
    ("https://example.org/group/", "group"), ("group", "group"), ("", ""),
])
def test_source_external_id_cleanup_is_preserved(value, expected):
    from app.models.collection import Source
    assert Source._clean_external_id(value) == expected


@pytest.mark.parametrize("metrics", [
    {}, {"views": None, "metric_availability": {"views": "unknown"}},
    {"views": 0, "comments": 0}, {"reactions": 3, "views": 7},
])
def test_staged_item_payload_preserves_metric_availability_and_zero(metrics):
    from app.models.collection import CollectedItem
    before = dict(metrics)
    row = SimpleNamespace(
        metrics=metrics, platform="telegram", external_id="post-1", text=None,
        published_at=None, media_type=None, author={"name": "example"},
        permalink="https://example.org/post-1", content_hash="hash",
    )
    result = CollectedItem.as_agent_item(row)
    assert result["text"] == ""
    assert result["published_at"] is result["date"] is None
    assert result["permalink"] == row.permalink
    assert result["content_hash"] == row.content_hash
    assert result["metrics"] == metrics and result["metrics"] is not metrics
    assert result["author"] == row.author and result["author"] is not row.author
    for key in ("reactions", "comments", "views", "metric_availability"):
        assert (key in result) == (key in metrics)
        if key in metrics:
            assert result[key] == metrics[key]
    result["metrics"]["new"] = 1
    assert metrics == before


def test_domain_unknown_export_raises_attribute_error():
    domain = importlib.import_module("app.models.collection")
    with pytest.raises(AttributeError):
        getattr(domain, "UnknownCollectionModel")


@pytest.mark.parametrize("first", [
    "app.models.collection",
    "app.models.platform", "app.models.source", "app.models.collected_item",
    "app.models.collection.platform", "app.models.collection.source", "app.models.collection.collected_item",
    "app.models.managers.platform_manager", "app.models.managers.source_manager",
    "app.models.managers.collected_item_manager",
])
def test_fresh_process_entry_points_complete_imports_and_manager_binding(first):
    # Inherit conftest's already redirected environment; no DB connection/SQL.
    code = """
import importlib
import json
import sys
importlib.import_module(sys.argv[1])
public = importlib.import_module('app.models')
domain = importlib.import_module('app.models.collection')
for name, module in json.loads(sys.argv[2]).items():
    cls = getattr(public, name)
    legacy = importlib.import_module(f'app.models.{module}')
    leaf = importlib.import_module(f'app.models.collection.{module}')
    assert cls is getattr(legacy, name) is getattr(domain, name) is getattr(leaf, name)
    assert cls.objects.model is cls
    assert sum(mapper.class_.__name__ == name for mapper in public.Base.registry.mappers) == 1
"""
    result = subprocess.run(
        [sys.executable, "-c", code, first, json.dumps({name: values[0] for name, values in MODELS.items()})],
        cwd=ROOT, text=True, capture_output=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stderr
