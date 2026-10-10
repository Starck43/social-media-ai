"""Prepared identity-layout regressions; Owner executes in the reviewed env.

Bodies issue no SQL, migrations or external calls. Shared conftest uses the
existing test schema; no concurrent pytest across worktrees sharing it.
Synthetic objects/crypto doubles contain no real credentials or customer data.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import importlib.util
import inspect
import json
import pickle
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODELS = {
    "User": ("user", "users", "UserManager", "account"),
    "Role": ("role", "roles", "RoleManager", "app"),
    "Permission": ("permission", "permissions", "PermissionManager", "app"),
    "ModelType": ("model_type", "model_types", None, "app"),
    "UserCredential": ("user_credential", "user_credentials", "UserCredentialManager", "account"),
}
# Dev 0be47bbf: relative imports resolved, docstring indentation normalized.
CONTRACT_FINGERPRINTS = {
    "ModelType": "10b3b6ddcc59dfee012b62376726ca714e2fd8e05c9fa87d8fca3126a88502b7",
    "Permission": "aa7b9618dd210a93e21768036515aff5dd24aac26888b8fa2a267d65c80e543e",
    "Role": "1177ee6c382b646992d4e0f877a2b613c2030d76203c077449372f6271d056d0",
    "User": "8ecef16dd8c74f8052ce651d2c9fd4dbedfc4665db26e9c0576846b7057b438b",
    "UserCredential": "d29d012720c55ea3286980e99ce2c811abc1e03aa7a19d9de27ccddf22360997",
    "role_permission": "9402e87ac4a5567a90035e3eb1c41b600f55701dde23ff2b6670a29eae569c54",
}


def _canonical_ast(node):
    if isinstance(node, ast.AST):
        return [type(node).__name__, [[field, _canonical_ast(value)] for field, value in ast.iter_fields(node)]]
    if isinstance(node, list):
        return [_canonical_ast(value) for value in node]
    return node


def _fingerprint(node):
    for owner in ast.walk(node):
        body = getattr(owner, "body", None)
        if (
            isinstance(body, list)
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body[0].value.value = inspect.cleandoc(body[0].value.value)
    for value in ast.walk(node):
        if isinstance(value, ast.ImportFrom) and value.level:
            value.module = importlib.util.resolve_name("." * value.level + (value.module or ""), "app.models.identity")
            value.level = 0
    payload = json.dumps(_canonical_ast(node), ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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
    assert sum(mapper.class_.__name__ == name for mapper in public.Base.registry.mappers) == 1
    assert not getattr(cls, "__tenant_scoped__", False) and "tenant_id" not in cls.__table__.c
    if manager is None:
        assert cls.objects is None  # Do not invent a ModelType manager during a file move.
    else:
        manager_module = importlib.import_module(f"app.models.managers.{module}_manager")
        assert isinstance(cls.objects, getattr(manager_module, manager)) and cls.objects.model is cls
        assert getattr(legacy, manager) is getattr(manager_module, manager)
    from scripts.migrations.deps import get_app_label_from_model, get_model_class_by_table_name

    # Pure registry/label helpers only; NEVER execute Alembic or registration writes.
    assert get_app_label_from_model(cls) == label
    assert get_model_class_by_table_name(table) is cls


@pytest.mark.parametrize("name", MODELS)
def test_entire_class_contract_preserved_with_formatting_and_import_depth_only(name):
    module = MODELS[name][0]
    tree = ast.parse((ROOT / f"app/models/identity/{module}.py").read_text(encoding="utf-8"))
    cls = next(value for value in tree.body if isinstance(value, ast.ClassDef) and value.name == name)
    assert _fingerprint(cls) == CONTRACT_FINGERPRINTS[name]


def test_role_permission_association_has_one_canonical_declaration_and_same_contract():
    from app.models import Base, Permission, Role, User
    from app.models.identity import role_permission as domain_table
    from app.models.identity.role import role_permission as leaf_table
    from app.models.role import role_permission as legacy_table
    from sqlalchemy.orm import configure_mappers

    configure_mappers()
    assert domain_table is leaf_table is legacy_table is Base.metadata.tables[leaf_table.fullname]
    assert sum(table.name == "role_permission" for table in Base.metadata.tables.values()) == 1
    assert tuple(column.name for column in leaf_table.primary_key.columns) == ("role_id", "permission_id")
    assert Role.__mapper__.relationships["permissions"].secondary is leaf_table
    assert Permission.__mapper__.relationships["roles"].secondary is leaf_table
    assert Role.__mapper__.relationships["users"].mapper.class_ is User
    assert User.__mapper__.relationships["role"].mapper.class_ is Role
    tree = ast.parse((ROOT / "app/models/identity/role.py").read_text(encoding="utf-8"))
    association = next(
        value
        for value in tree.body
        if isinstance(value, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "role_permission" for target in value.targets)
    )
    assert _fingerprint(association) == CONTRACT_FINGERPRINTS["role_permission"]


def test_permission_model_type_relationship_and_existing_enums_remain():
    from app.models.identity import ModelType, Permission, Role
    from app.types import ActionType, UserRoleType

    assert Permission.__mapper__.relationships["model_type"].mapper.class_ is ModelType
    assert Permission.__mapper__.relationships["model_type"].lazy == "selectin"
    assert ModelType.__mapper__.relationships["permissions"].mapper.class_ is Permission
    action = Permission.__table__.c.action_type
    assert action.type.enum_class is None
    assert action.type.impl.enums == [member.name for member in ActionType]
    assert action.type.process_bind_param(ActionType.VIEW, None) == ActionType.VIEW.name
    assert action.type.process_result_value(ActionType.VIEW.name, None) is ActionType.VIEW
    codename = Role.__table__.c.codename
    assert codename.type.enum_class is None
    assert codename.type.enums == [member.name for member in UserRoleType]


@pytest.mark.parametrize(
    "module,symbol",
    [
        ("user", "ActionType"),
        ("user", "UserRoleType"),
        ("role", "UserRoleType"),
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

    assert Permission.split_codename("dashboard.AIAnalytics.view") == ("dashboard.AIAnalytics", "view")
    assert Permission.action_from_token("view") is ActionType.VIEW
    with pytest.raises(ValueError):
        Permission.action_from_token("not_an_action")


def test_structured_user_permissions_and_cache_preserve_existing_behavior():
    from app.models.identity import ModelType, Permission, Role, User
    from app.types import ActionType, UserRoleType

    grant = Permission(action_type=ActionType.VIEW, model_type=ModelType(model_name="AgentTask"))
    user = User(is_superuser=False, role=Role(codename=UserRoleType.VIEWER, permissions=[grant]))
    assert user._model_perms_cache is None
    assert user.model_permissions("agenttask") == {ActionType.VIEW}
    assert user.has_perm_for("AGENTTASK", ActionType.VIEW)
    assert not user.has_perm_for("agenttask", ActionType.DELETE)
    assert user.has_admin_access()
    assert user.model_permissions("source") == set()


@pytest.mark.parametrize("codename", ["SUPERUSER", "VIEWER", "unknown"])
def test_user_superuser_role_shape_preserved(codename):
    from app.models.identity import Role, User

    user = User(is_superuser=False, role=Role(codename=codename, permissions=[]))
    assert user._is_superuser_role() is (codename == "SUPERUSER")
    user.is_superuser = True
    assert user._is_superuser_role()


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
    assert sum(mapper.class_.__name__ == name for mapper in public.Base.registry.mappers) == 1
    if name == 'ModelType':
        assert cls.objects is None
    else:
        assert cls.objects.model is cls
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
