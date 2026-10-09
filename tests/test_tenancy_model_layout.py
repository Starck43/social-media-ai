"""Tenancy layout compatibility regressions; prepared, NOT run by the agent.

No test body issues SQL, but normal pytest loads the shared DB conftest. Use
only the owner's reviewed environment and an isolated, preconfigured test
schema. The source fingerprints cover existing declarative schema expressions;
intentional future schema changes must review/update these expectations.
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

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODELS = {
    "Tenant": ("tenant", "tenants", "TenantManager", "tenants"),
    "TenantUser": ("tenant_user", "tenant_users", "TenantUserManager", "tenant_users"),
    "TenantInvite": ("tenant_invite", "tenant_invites", "TenantInviteManager", "tenant_invites"),
    "TenantChannel": ("tenant_channel", "tenant_channels", "TenantChannelManager", "tenant_channels"),
}
# Fresh-dev b4fd0f0b (unchanged at cb1a0a04): schema-only snapshots, including defaults/FKs/indexes.
SCHEMA_FINGERPRINTS = {'Tenant': 'c5cb9fedf76000a00c625a499bf4f777848e6ca1c21398c19d436756276607a9', 'TenantUser': '22bc9b69f69ab56d91f0cb4c510b17c04099c12609073ac2d6abaa6446c7650b', 'TenantInvite': '8fae1388ca938f992fa91c509e85d19b31bdaa767143962d4a4c199398afedcf', 'TenantChannel': '0a171a5d6bf6ea2a95efc08bfd4f6a7252457600b918fdb82d6a583ebf2b84b4'}


def _canonical_ast(node):
    """Include empty/None fields explicitly; do not depend on ast.dump defaults.

    Python 3.13 omits optional empty fields by default; 3.12 does not. The
    selected schema-expression nodes have the same fields on both versions.
    Ignore source locations, but retain node types, field names and all values.
    """
    if isinstance(node, ast.AST):
        return [type(node).__name__, [[field, _canonical_ast(value)] for field, value in ast.iter_fields(node)]]
    if isinstance(node, list):
        return [_canonical_ast(value) for value in node]
    return node


@pytest.mark.parametrize("name", MODELS)
def test_public_legacy_and_domain_paths_share_one_mapped_class(name):
    public = importlib.import_module("app.models")
    legacy = importlib.import_module("app.models.tenant")
    domain = importlib.import_module("app.models.tenancy")
    module, table_name, manager_name, singleton_name = MODELS[name]
    leaf = importlib.import_module(f"app.models.tenancy.{module}")
    cls = getattr(public, name)
    assert cls is getattr(legacy, name) is getattr(domain, name) is getattr(leaf, name)
    assert name in public.__all__ and name in domain.__all__
    assert cls._app_label == "account"
    assert cls.__tablename__ == table_name
    assert cls.__table__ is public.Base.metadata.tables[cls.__table__.fullname]
    assert sum(mapper.class_ is cls for mapper in public.Base.registry.mappers) == 1
    assert sum(mapper.class_.__name__ == name for mapper in public.Base.registry.mappers) == 1
    managers = importlib.import_module("app.models.managers.tenant_manager")
    assert isinstance(cls.objects, getattr(managers, manager_name))
    assert cls.objects.model is cls
    assert getattr(managers, singleton_name).model is cls
    # Preserve the old module's explicit manager-class exports as well.
    assert getattr(legacy, manager_name) is getattr(managers, manager_name)


@pytest.mark.parametrize("name", MODELS)
def test_existing_declarative_schema_expressions_are_unchanged(name):
    module = MODELS[name][0]
    tree = ast.parse((ROOT / f"app/models/tenancy/{module}.py").read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name)
    statements = [
        node for node in cls.body
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id in ("__tablename__", "__table_args__")
                    for target in node.targets)
        ) or (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
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
def test_legacy_serialized_class_reference_still_resolves(name):
    # Only a locally constructed GLOBAL opcode; never deserialize user input.
    old_reference = f"capp.models.tenant\n{name}\n.".encode("ascii")
    cls = getattr(importlib.import_module("app.models.tenant"), name)
    assert pickle.loads(old_reference) is cls
    assert pickle.loads(pickle.dumps(cls)) is cls


def test_relationships_and_chat_binding_contract_remain_registered():
    from sqlalchemy import UniqueConstraint
    from sqlalchemy.orm import configure_mappers

    from app.models import Role, TenantChannel, TenantUser

    configure_mappers()
    assert TenantUser.__mapper__.relationships["role"].mapper.class_ is Role
    unique = next(
        constraint for constraint in TenantChannel.__table__.constraints
        if isinstance(constraint, UniqueConstraint) and constraint.name == "uq_tenant_channel_chat"
    )
    assert tuple(column.name for column in unique.columns) == ("channel", "chat_id")


@pytest.mark.parametrize("first", [
    "app.models.tenant",
    "app.models.tenancy",
    "app.models.tenancy.tenant",
    "app.models.tenancy.tenant_user",
    "app.models.tenancy.tenant_invite",
    "app.models.tenancy.tenant_channel",
    "app.models.managers.tenant_manager",
])
def test_fresh_process_entry_points_complete_manager_binding(first):
    # Fresh interpreters expose cycles hidden by pytest's already-loaded modules.
    # Environment is inherited AFTER the shared conftest's test-schema redirect.
    # These import/model assertions neither connect to DB nor issue SQL.
    code = """
import importlib
import json
import sys
importlib.import_module(sys.argv[1])
public = importlib.import_module('app.models')
legacy = importlib.import_module('app.models.tenant')
domain = importlib.import_module('app.models.tenancy')
managers = importlib.import_module('app.models.managers.tenant_manager')
for name, singleton in json.loads(sys.argv[2]).items():
    cls = getattr(public, name)
    assert cls is getattr(legacy, name) is getattr(domain, name)
    assert cls.objects.model is cls
    assert getattr(managers, singleton).model is cls
    assert sum(mapper.class_.__name__ == name for mapper in public.Base.registry.mappers) == 1
"""
    result = subprocess.run(
        [sys.executable, "-c", code, first, json.dumps({name: values[3] for name, values in MODELS.items()})],
        cwd=ROOT, text=True, capture_output=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_schema_snapshot_encoding_preserves_explicit_empty_fields():
    assert _canonical_ast(ast.Module(body=[], type_ignores=[])) == [
        "Module", [["body", []], ["type_ignores", []]],
    ]
