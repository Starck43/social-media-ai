"""Prepared identity-layout regressions; Owner executes in the reviewed env.

Bodies issue no SQL, migrations or external calls. Shared conftest uses the
existing test schema; no concurrent pytest across worktrees sharing it.
Synthetic objects/crypto doubles contain no real credentials or customer data.
"""

from __future__ import annotations

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
    "User": ("user", "users", "UserManager", "identity"),
    "Role": ("role", "roles", "RoleManager", "identity"),
    "Permission": ("permission", "permissions", "PermissionManager", "identity"),
    "ModelType": ("model_type", "model_types", "BaseManager", "identity"),
    "UserCredential": ("user_credential", "user_credentials", "UserCredentialManager", "identity"),
}


@pytest.mark.parametrize("name", MODELS)
def test_identity_class_paths_single_mapper_global_scope_and_existing_manager(name):
    module, table, manager, label = MODELS[name]
    public = importlib.import_module("app.models")
    legacy = importlib.import_module(f"app.models.{module}")
    domain = importlib.import_module("app.models.identity")
    leaf = importlib.import_module(f"app.models.identity.{module}")
    cls = getattr(public, name)
    assert cls is getattr(legacy, name) is getattr(domain, name) is getattr(leaf, name)
    assert name in public.__all__ and name in domain.__all__
    assert cls.__module__ == f"app.models.identity.{module}"
    assert cls.__tablename__ == table
    assert cls.__table__ is public.Base.metadata.tables[cls.__table__.fullname]
    mapper_name = "PlatformRole" if name == "Role" else name
    assert sum(mapper.class_.__name__ == mapper_name for mapper in public.Base.registry.mappers) == 1
    assert not getattr(cls, "__tenant_scoped__", False) and "tenant_id" not in cls.__table__.c
    if manager is not None:
        if manager == "BaseManager":
            manager_module = importlib.import_module("app.models.managers.base_manager")
        else:
            manager_module = importlib.import_module(f"app.models.managers.{module}_manager")
        assert isinstance(cls.objects, getattr(manager_module, manager)) and cls.objects.model is cls
        if manager != "BaseManager":
            assert getattr(legacy, manager) is getattr(manager_module, manager)
    from scripts.migrations.deps import get_app_label_from_model, get_model_class_by_table_name

    # Pure registry/label helpers only; NEVER execute Alembic or registration writes.
    assert get_app_label_from_model(cls) == label
    assert get_model_class_by_table_name(table) is cls


def test_role_permission_association_has_one_canonical_declaration():
    from sqlalchemy.orm import configure_mappers

    from app.models import Base, Permission, PlatformRole, Role, User
    from app.models.identity import role_permission as domain_table
    from app.models.identity.role import role_permission as leaf_table
    from app.models.role import role_permission as legacy_table

    configure_mappers()
    assert domain_table is leaf_table is legacy_table is Base.metadata.tables[leaf_table.fullname]
    assert sum(table.name == "role_permission" for table in Base.metadata.tables.values()) == 1
    assert tuple(column.name for column in leaf_table.primary_key.columns) == ("role_id", "permission_id")
    assert PlatformRole is Role
    assert Role.__mapper__.relationships["permissions"].secondary is leaf_table
    assert Permission.__mapper__.relationships["roles"].secondary is leaf_table
    assert Role.__mapper__.relationships["users"].mapper.class_ is User
    assert User.__mapper__.relationships["role"].mapper.class_ is Role


def test_permission_model_type_relationship_and_platform_role_storage():
    from sqlalchemy import CheckConstraint

    from app.models.identity import ModelType, Permission, PlatformRole
    from app.types import ActionType, PlatformRoleType

    assert Permission.__mapper__.relationships["model_type"].mapper.class_ is ModelType
    assert Permission.__mapper__.relationships["model_type"].lazy == "selectin"
    assert ModelType.__mapper__.relationships["permissions"].mapper.class_ is Permission
    action = Permission.__table__.c.action_type
    assert action.type.enum_class is None
    assert action.type.impl.enums == [member.name for member in ActionType]
    assert action.type.process_bind_param(ActionType.VIEW, None) == ActionType.VIEW.name
    assert action.type.process_result_value(ActionType.VIEW.name, None) is ActionType.VIEW
    codename = PlatformRole.__table__.c.codename
    assert isinstance(codename.type.python_type, type)
    assert codename.type.python_type is str
    assert any(
        isinstance(constraint, CheckConstraint)
        and "codename IN ('user', 'operator', 'admin')" in str(constraint.sqltext)
        for constraint in PlatformRole.__table__.constraints
    )
    assert {member.value for member in PlatformRoleType} == {"user", "operator", "admin"}
    assert "name" not in PlatformRole.__table__.c
    assert "is_superuser" not in __import__("app.models.identity.user", fromlist=["User"]).User.__table__.c


def test_permission_codename_is_computed_from_stable_resource_key_and_action():
    from app.models.identity import ModelType, Permission
    from app.types import ActionType

    resource = ModelType(resource_key="collection.source", model_name="Source", table_name="sources")
    permission = Permission(name="View sources", model_type=resource, action_type=ActionType.VIEW)
    assert permission.codename == "collection.source.view"
    assert "codename" not in Permission.__table__.c
    assert "resource_key" in ModelType.__table__.c


@pytest.mark.parametrize(
    "module,symbol",
    [
        ("user", "ActionType"),
        ("permission", "ActionType"),
        ("model_type", "Base"),
        ("user_credential", "TimestampMixin"),
    ],
)
def test_legacy_imported_symbols_are_still_available(module, symbol):
    legacy = importlib.import_module(f"app.models.{module}")
    leaf = importlib.import_module(f"app.models.identity.{module}")
    assert getattr(legacy, symbol) is getattr(leaf, symbol)


@pytest.mark.parametrize("name", MODELS)
def test_legacy_and_current_class_serialization_resolve(name):
    cls = getattr(importlib.import_module("app.models.identity"), name)
    module = MODELS[name][0]
    assert pickle.loads(f"capp.models.{module}\n{name}\n.".encode("ascii")) is cls
    assert pickle.loads(pickle.dumps(cls)) is cls


def test_permission_codename_token_contract_remains():
    from app.models.identity import Permission
    from app.types import ActionType

    assert Permission.split_codename("analysis.result.view") == ("analysis.result", "view")
    # The catalog accepts this legacy alias but resolves it to the canonical key.
    assert Permission.split_codename("dashboard.AIAnalytics.view") == ("analysis.result", "view")
    assert Permission.action_from_token("view") is ActionType.VIEW
    with pytest.raises(ValueError):
        Permission.action_from_token("not_an_action")


def test_structured_user_permissions_and_cache_preserve_existing_behavior():
    from app.models.identity import ModelType, Permission, PlatformRole, User
    from app.types import ActionType, PlatformRoleType

    grant = Permission(
        action_type=ActionType.VIEW,
        model_type=ModelType(resource_key="scheduling.task", model_name="agenttask"),
    )
    user = User(is_active=True, role=PlatformRole(codename=PlatformRoleType.OPERATOR.value, permissions=[grant]))
    assert user._model_perms_cache is None
    assert user.model_permissions("scheduling.task") == {ActionType.VIEW}
    assert user.has_perm_for("AGENTTASK", ActionType.VIEW)
    assert not user.has_perm_for("agenttask", ActionType.DELETE)
    assert user.has_admin_access()
    assert user.model_permissions("source") == set()


@pytest.mark.parametrize("codename", ["admin", "operator", "user", "unknown"])
def test_platform_role_shape_does_not_grant_web_admin_bypass(codename):
    from app.models.identity import PlatformRole, User

    user = User(is_active=True, role=PlatformRole(codename=codename, permissions=[]))
    assert user.has_admin_access() is (codename in {"admin", "operator"})
    assert user._is_superuser_role() is (codename == "admin")


def test_vault_reveal_uses_crypto_double_and_preserves_global_ownership(monkeypatch):
    from app.models.identity import UserCredential
    from app.models.managers.user_credential_manager import user_credentials
    from app.utils import crypto

    calls = []

    def fake_decrypt(value):
        calls.append(value)
        return "synthetic-token"

    monkeypatch.setattr(crypto, "decrypt_secret", fake_decrypt)
    assert UserCredential.reveal(SimpleNamespace(secret_encrypted="synthetic-ciphertext")) == "synthetic-token"
    assert calls == ["synthetic-ciphertext"]
    assert user_credentials.model is UserCredential
    assert "user_id" in UserCredential.__table__.c and "tenant_id" not in UserCredential.__table__.c
    assert {fk.column.table.name for fk in UserCredential.__table__.c.user_id.foreign_keys} == {"users"}


def test_unknown_identity_export_raises_attribute_error():
    with pytest.raises(AttributeError):
        getattr(importlib.import_module("app.models.identity"), "UnknownIdentityModel")


@pytest.mark.parametrize(
    "first",
    [
        "app.models.identity",
        "app.models.user",
        "app.models.role",
        "app.models.permission",
        "app.models.model_type",
        "app.models.user_credential",
        "app.models.identity.user",
        "app.models.identity.role",
        "app.models.identity.permission",
        "app.models.identity.model_type",
        "app.models.identity.user_credential",
        "app.models.managers.user_manager",
        "app.models.managers.role_manager",
        "app.models.managers.permission_manager",
        "app.models.managers.user_credential_manager",
    ],
)
def test_fresh_process_imports_preserve_manager_and_association_identity(first):
    code = """
import importlib
import json
import sys
importlib.import_module(sys.argv[1])
public = importlib.import_module('app.models')
domain = importlib.import_module('app.models.identity')
for name, module in json.loads(sys.argv[2]).items():
    cls = getattr(public, name)
    assert cls is getattr(domain, name) is getattr(importlib.import_module(f'app.models.{module}'), name)
    assert cls.objects.model is cls
    mapper_name = 'PlatformRole' if name == 'Role' else name
    assert sum(mapper.class_.__name__ == mapper_name for mapper in public.Base.registry.mappers) == 1
legacy = importlib.import_module('app.models.role')
assert domain.role_permission is legacy.role_permission
"""
    result = subprocess.run(
        [sys.executable, "-c", code, first, json.dumps({name: values[0] for name, values in MODELS.items()})],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
