"""Actual user router/schema/dependency code on explicit in-memory boundaries.

No application factory, middleware, DB engine, auth/token service or provider is
loaded. HTTP here means an isolated TestClient router harness, not full ASGI/DB
acceptance. Existing role/profile implementation is observed, never redesigned.
"""

import ast
import builtins
import importlib.abc
import json
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[2]
HASH = "synthetic-password-hash-never-an-actual-credential"
PRIVATE = {"hashed_password", "password_hash", "password", "role", "role_id", "is_superuser"}
PUBLIC = {"id", "username", "email", "is_active", "created_at", "updated_at"}
CASES = [
    ("GET", "/api/v1/users", None, "get_active_users"),
    ("GET", "/api/v1/users/me", None, None),
    ("PUT", "/api/v1/users/me", {"username": "renamed"}, "update_user"),
    ("PUT", "/api/v1/users/12", {"username": "renamed"}, "update_user"),
    ("PUT", "/api/v1/users/12/platform-role", {"role": "operator"}, "set_platform_role"),
]
TOKEN = {"Authorization": "Bearer synthetic-no-real-token"}


@pytest.fixture(autouse=True)
def no_runtime_imports_or_network(monkeypatch):
    class NoApplicationImports(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split(".")[0] in {"app", "cli"}:
                raise AssertionError(f"Unreviewed application import: {fullname}")
            return None

    guard = NoApplicationImports()
    assert "app.core.database" not in sys.modules
    sys.meta_path.insert(0, guard)

    def denied(*args, **kwargs):
        raise AssertionError("Network is forbidden in the isolated router harness")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    try:
        yield
    finally:
        sys.meta_path.remove(guard)


def policy_class(types, catalog):
    # Reuse the four exact User policy method bodies, without ORM construction.
    path = ROOT / "app/models/identity/user.py"
    original = next(n for n in ast.parse(path.read_text()).body if isinstance(n, ast.ClassDef) and n.name == "User")
    names = {"is_platform_admin", "has_admin_access", "model_permissions", "has_perm_for"}
    methods = [n for n in original.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(methods) == len(names)
    node = ast.ClassDef(name="PolicyUser", bases=[], keywords=[], body=methods, decorator_list=[])
    normal_import = builtins.__import__

    def imports(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "app.core.access_catalog":
            return catalog
        return normal_import(name, globals, locals, fromlist, level)

    namespace = {
        "__name__": __name__,
        "ActionType": types.ActionType,
        "PlatformRoleType": types.PlatformRoleType,
        "__builtins__": dict(vars(builtins), __import__=imports),
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), str(path), "exec"), namespace)
    return namespace["PolicyUser"]


@pytest.fixture
def harness():
    helpers = load_isolated_source(
        "_response_enum_helpers",
        ROOT / "app/utils/db_enums.py",
        {"app.core.config": NS(settings=NS(DB_SCHEMA="synthetic_no_database"))},
    )
    types = load_isolated_source(
        "_response_identity_types", ROOT / "app/types/enums/user_types.py", {"app.utils.db_enums": helpers}
    )
    catalog = load_isolated_source("_response_catalog", ROOT / "app/core/access_catalog.py", {"app.types": types})
    policy = load_isolated_source(
        "_response_permissions",
        ROOT / "app/core/permissions.py",
        {
            "app.types": types,
            "app.core.access_catalog": catalog,
            "app.core.tenant_context": NS(current_tenant_id=lambda: None, is_bypass=lambda: False),
        },
    )
    user_type = policy_class(types, catalog)

    def user(role="admin", actions=(), active=True, user_id=7):
        result = user_type()
        result.__dict__.update(
            id=user_id,
            username="sample-user",
            email="sample@example.com",
            is_active=active,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            hashed_password=HASH,
            password_hash=HASH,
            password=HASH,
            role_id=900,
            is_superuser=True,
            role=NS(
                codename=role,
                permissions=[NS(action_type=action, model_type=NS(resource_key="identity.user")) for action in actions],
            ),
            _model_perms_cache=None,
        )
        return result

    actor, target = user(), user(user_id=12)
    fresh = AsyncMock(return_value=actor)
    storage = NS(
        get_active_users=AsyncMock(return_value=[target]),
        update_user=AsyncMock(return_value=target),
        set_platform_role=AsyncMock(return_value=target),
        prefetch_related=Mock(return_value=NS(get=fresh)),
    )
    # The real authentication/token/DB path is deliberately outside this scope.
    identity = NS(actor=actor)

    async def authenticate(authorization: str | None = Header(default=None)):
        if authorization != TOKEN["Authorization"]:
            raise HTTPException(status_code=401, detail="Not authenticated")
        return identity.actor

    user_model = type("UserBoundary", (), {"objects": storage})
    dependencies = load_isolated_source(
        "_response_api_dependencies",
        ROOT / "app/api/deps.py",
        {
            "app.models": NS(User=user_model),
            "app.services.user.auth": NS(get_authenticated_user=authenticate),
            "app.types": types,
            "app.core.permissions": policy,
            "app.core.access_catalog": catalog,
        },
    )
    schemas = load_isolated_source("_response_user_schemas", ROOT / "app/schemas/user.py", {"app.types": types})
    routes = load_isolated_source(
        "_response_user_routes",
        ROOT / "app/api/v1/endpoints/user.py",
        {
            "app.api.deps": dependencies,
            "app.models": NS(User=user_model),
            "app.schemas.user": schemas,
            "app.services.user.auth": NS(get_authenticated_user=authenticate),
            "app.types": types,
        },
    )
    app = FastAPI()
    # Same router-wide authentication declaration as entry.py; no real app startup.
    app.include_router(routes.router, prefix="/api/v1/users", dependencies=[Depends(authenticate)])
    return NS(
        client=TestClient(app),
        app=app,
        storage=storage,
        fresh=fresh,
        identity=identity,
        actor=actor,
        target=target,
        user=user,
        types=types,
        schemas=schemas,
        authenticate=authenticate,
        routes=routes,
    )


def assert_profile(data, user):
    assert set(data) == PUBLIC
    assert data["id"] == user.id
    assert data["username"] == user.username
    assert data["email"] == user.email
    assert data["is_active"] is user.is_active
    assert data["created_at"] == "2026-01-01T00:00:00Z"
    assert data["updated_at"] == "2026-01-02T00:00:00Z"
    assert not PRIVATE.intersection(data)
    assert HASH not in json.dumps(data)


@pytest.mark.parametrize("method,path,payload,operation", CASES)
@pytest.mark.parametrize("representation", ["attributes", "dict"])
def test_all_profile_http_responses_exclude_secrets(harness, method, path, payload, operation, representation):
    returned = harness.target if representation == "attributes" else vars(harness.target).copy()
    harness.storage.get_active_users.return_value = [returned]
    harness.storage.update_user.return_value = returned
    harness.storage.set_platform_role.return_value = returned
    if operation is None:
        # /me obtains the actor directly from the authentication boundary.
        harness.identity.actor = returned
    response = harness.client.request(method, path, json=payload, headers=TOKEN)
    assert response.status_code == 200, response.text
    assert HASH not in response.text
    body = response.json()
    assert_profile(body[0] if isinstance(body, list) else body, harness.target)
    if operation:
        getattr(harness.storage, operation).assert_awaited_once()


@pytest.mark.parametrize("name", ["UserInDB", "User"])
def test_schema_never_reads_secret_attributes(harness, name):
    class RefuseSecrets:
        def __getattr__(self, key):
            if key in PRIVATE:
                raise AssertionError(f"Serializer attempted to read {key}")
            return getattr(harness.target, key)

    schema = getattr(harness.schemas, name)
    assert_profile(schema.model_validate(RefuseSecrets()).model_dump(mode="json"), harness.target)
    assert set(schema.model_json_schema()["properties"]) == PUBLIC


def test_openapi_all_profile_response_shapes(harness):
    document = harness.app.openapi()
    assert "hashed_password" not in json.dumps(document)
    assert "password_hash" not in json.dumps(document)
    assert HASH not in json.dumps(document)
    for method, path, _, _ in CASES:
        path = path.replace("/12", "/{user_id}")
        response = document["paths"][path][method.lower()]["responses"]["200"]["content"]["application/json"]["schema"]
        if method == "GET" and path == "/api/v1/users":
            assert response["type"] == "array"
            response = response["items"]
        ref = response["$ref"].rsplit("/", 1)[1]
        schema = document["components"]["schemas"][ref]
        assert set(schema["properties"]) == PUBLIC
        assert not PRIVATE.intersection(schema["properties"])


@pytest.mark.parametrize("method,path,payload,operation", CASES)
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer invalid-synthetic"}])
def test_authentication_is_not_bypassed(harness, method, path, payload, operation, headers):
    response = harness.client.request(method, path, json=payload, headers=headers)
    assert response.status_code == 401
    assert HASH not in response.text
    harness.fresh.assert_not_awaited()
    for name in ("get_active_users", "update_user", "set_platform_role"):
        getattr(harness.storage, name).assert_not_awaited()


@pytest.mark.parametrize(
    "role,grants,method,path,payload,expected",
    [
        ("user", (), "GET", "/api/v1/users", None, 403),
        ("operator", (), "GET", "/api/v1/users", None, 403),
        ("operator", ("VIEW",), "GET", "/api/v1/users", None, 200),
        ("operator", ("UPDATE",), "GET", "/api/v1/users", None, 403),
        ("admin", (), "GET", "/api/v1/users", None, 200),
        ("operator", ("VIEW",), "PUT", "/api/v1/users/12", {"username": "renamed"}, 403),
        ("operator", ("UPDATE",), "PUT", "/api/v1/users/7", {"username": "renamed"}, 200),
        ("operator", ("UPDATE",), "PUT", "/api/v1/users/12/platform-role", {"role": "user"}, 403),
        ("admin", (), "PUT", "/api/v1/users/12/platform-role", {"role": "operator"}, 200),
        ("user", (), "GET", "/api/v1/users/me", None, 200),
        ("user", (), "PUT", "/api/v1/users/me", {"username": "renamed"}, 200),
    ],
)
def test_existing_route_rights_matrix(harness, role, grants, method, path, payload, expected):
    actor = harness.user(role=role, actions=[harness.types.ActionType[g] for g in grants])
    harness.identity.actor = actor
    harness.fresh.return_value = actor
    if path == "/api/v1/users/7":
        harness.storage.update_user.return_value = actor
    response = harness.client.request(method, path, json=payload, headers=TOKEN)
    assert response.status_code == expected, response.text
    assert HASH not in response.text
    if expected == 403:
        for name in ("get_active_users", "update_user", "set_platform_role"):
            getattr(harness.storage, name).assert_not_awaited()


@pytest.mark.parametrize("case", [CASES[0], CASES[3], CASES[4]])
@pytest.mark.parametrize("fresh_state", ["deleted", "inactive", "revoked"])
def test_operator_guards_recheck_fresh_rights(harness, case, fresh_state):
    method, path, payload, _ = case
    harness.fresh.return_value = (
        None if fresh_state == "deleted" else harness.user(role="user", active=fresh_state != "inactive")
    )
    response = harness.client.request(method, path, json=payload, headers=TOKEN)
    assert response.status_code == 403
    harness.storage.prefetch_related.assert_called_once_with("role.permissions")
    harness.fresh.assert_awaited_once_with(id=harness.actor.id)
    for name in ("get_active_users", "update_user", "set_platform_role"):
        getattr(harness.storage, name).assert_not_awaited()


@pytest.mark.parametrize("case", CASES[2:])
def test_missing_profile_update_keeps_not_found(harness, case):
    method, path, payload, operation = case
    getattr(harness.storage, operation).return_value = None
    response = harness.client.request(method, path, json=payload, headers=TOKEN)
    assert response.status_code == 404
    assert response.json() == {"detail": "User not found"}


def test_router_auth_declarations_and_manager_forwarding(harness):
    routes = {r.endpoint.__name__: r for r in harness.routes.router.routes if isinstance(r, APIRoute)}
    assert routes["get_current_user"].dependant.dependencies[0].call is harness.authenticate
    assert routes["update_my_profile"].dependant.dependencies[0].call is harness.authenticate
    response = harness.client.put("/api/v1/users/me", json={"username": "renamed"}, headers=TOKEN)
    assert response.status_code == 200
    harness.storage.update_user.assert_awaited_once_with(
        user_id=harness.actor.id, update_data={"username": "renamed"}, current_user=harness.actor
    )
    # Pin production's router-wide auth registration without loading entry.py.
    tree = ast.parse((ROOT / "app/api/v1/entry.py").read_text())
    declaration = next(
        n
        for n in tree.body
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "API_AUTH" for t in n.targets)
    )
    assert ast.unparse(declaration.value) == "[Depends(get_authenticated_user)]"
    registration = next(
        n.value
        for n in tree.body
        if isinstance(n, ast.Expr)
        and isinstance(n.value, ast.Call)
        and isinstance(n.value.func, ast.Attribute)
        and n.value.func.attr == "include_router"
        and n.value.args
        and ast.unparse(n.value.args[0]) == "user.router"
    )
    keywords = {kw.arg: ast.unparse(kw.value) for kw in registration.keywords}
    assert keywords["prefix"] == "'/users'"
    assert keywords["dependencies"] == "API_AUTH"
