"""DB-exempt: isolated FastAPI apps, fake membership context, no providers."""

import secrets
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.v1.conventions import (
    BUSINESS_TAGS,
    ERRORS,
    BusinessRoute,
    business_router,
    openapi_options,
    require_business_scope,
    validate_business_routes,
)
from app.core.request_context import RequestContextMiddleware
from app.core.tenant_context import tenant_scope
from app.schemas.business import BusinessError, BusinessPage, BusinessSchema


class Item(BusinessSchema):
    id: int


async def allowed():
    return None


def sample(*, authenticated=True, bypass=False, permission=allowed):
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.middleware("http")
    async def synthetic_membership(request, call_next):
        request.state.api_user = SimpleNamespace(is_active=authenticated)
        request.state.tenant_id = 7
        with tenant_scope(7, bypass=bypass):
            return await call_next(request)

    router = business_router("files", permission=permission)

    @router.get("/items", response_model=BusinessPage[Item])
    async def items(limit: int = 50):
        if limit == 13:
            raise HTTPException(409, detail="private-provider-content")
        return BusinessPage[Item](items=[Item(id=1)], limit=limit)

    @router.post("/commands", response_model=Item, openapi_extra={"x-creates-command": True})
    async def command(item: Item):
        return item

    validate_business_routes(router)
    app.include_router(router, prefix="/api/v1")
    return app, router


def test_sample_router_contract_and_openapi():
    app, router = sample()
    assert all(isinstance(route, BusinessRoute) for route in router.routes)
    schema = app.openapi()
    for path, operations in schema["paths"].items():
        assert path.startswith("/api/v1/files/")
        for operation in operations.values():
            assert operation["tags"] == [BUSINESS_TAGS["files"]]
            assert set(map(str, ERRORS)) <= operation["responses"].keys()
            assert "$ref" in operation["responses"]["200"]["content"]["application/json"]["schema"]
    with TestClient(app) as client:
        assert client.get("/api/v1/files/items").json() == {"items": [{"id": 1}], "limit": 50, "next_before_id": None}


@pytest.mark.parametrize("authenticated,bypass,status", [(False, False, 401), (True, True, 403)])
def test_scope_fails_closed(authenticated, bypass, status):
    app, _ = sample(authenticated=authenticated, bypass=bypass)
    with TestClient(app) as client:
        response = client.get("/api/v1/files/items", headers={"X-Tenant-ID": "99"})
    assert response.status_code == status
    assert set(response.json()) == {"code", "message", "request_id"}


async def denied():
    raise HTTPException(403, detail="private permission information")


def test_per_action_permission_is_not_only_authentication():
    app, _ = sample(permission=denied)
    with TestClient(app) as client:
        response = client.get("/api/v1/files/items")
    assert response.status_code == 403
    assert "private" not in response.text


@pytest.mark.parametrize("query,status", [("?limit=13", 409), ("?limit=secret", 422)])
def test_errors_are_safe_and_correlated(query, status):
    app, _ = sample()
    with TestClient(app) as client:
        response = client.get("/api/v1/files/items" + query, headers={"X-Request-ID": "synthetic-request"})
    assert response.status_code == status
    assert response.json()["request_id"] == response.headers["x-request-id"] == "synthetic-request"
    assert "private" not in response.text and "secret" not in response.text


@pytest.mark.parametrize("key", [None, "", "invalid key", "x" * 129])
def test_command_rejects_missing_or_unbounded_idempotency_key(key):
    app, _ = sample()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/files/commands", json={"id": 1}, headers={} if key is None else {"Idempotency-Key": key}
        )
    assert response.status_code == 400


def test_command_accepts_bounded_key_but_does_not_claim_replay_execution():
    app, _ = sample()
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/v1/files/commands", json={"id": 1}, headers={"Idempotency-Key": "synthetic-command-1"}
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/v1/files/commands",
                json={"id": 1},
                headers=[("Idempotency-Key", "one"), ("Idempotency-Key", "two")],
            ).status_code
            == 400
        )


@pytest.mark.parametrize("domain", ["unknown", "../files", "files/secrets"])
def test_unknown_domain_fails(domain):
    with pytest.raises(ValueError, match="domain"):
        business_router(domain, permission=allowed)


def test_missing_response_model_and_tag_and_command_method_fail():
    with pytest.raises(ValueError, match="response model"):
        business_router("crm", permission=allowed).get("/items", response_model=None)(allowed)
    with pytest.raises(ValueError, match="tag"):
        BusinessRoute("/items", allowed, response_model=Item)
    with pytest.raises(ValueError, match="POST"):
        business_router("crm", permission=allowed).get(
            "/items", response_model=Item, openapi_extra={"x-creates-command": True}
        )(allowed)
    router = APIRouter()
    router.get("/unreviewed")(allowed)
    with pytest.raises(ValueError, match="BusinessRoute"):
        validate_business_routes(router)


def test_contract_gate_detects_mutated_routes():
    _, router = sample()
    route = router.routes[0]
    route.response_model = None
    with pytest.raises(ValueError, match="response model"):
        validate_business_routes(router)
    route.response_model = Item
    route.responses.clear()
    with pytest.raises(ValueError, match="errors"):
        validate_business_routes(router)


@pytest.mark.parametrize(
    "environment,flag,exposed",
    [
        ("development", None, True),
        ("test", None, True),
        ("production", None, False),
        ("Production ", None, False),
        ("staging", None, False),
        ("production", True, True),
        ("development", False, False),
    ],
)
def test_openapi_exposure_in_real_app(environment, flag, exposed):
    app = FastAPI(**openapi_options(environment, flag))
    with TestClient(app) as client:
        for path in ("/openapi.json", "/docs", "/redoc"):
            assert client.get(path).status_code == (200 if exposed else 404)


@pytest.mark.parametrize(
    "fields",
    [
        {"limit": 0},
        {"limit": 101},
        {"limit": True},
        {"next_before_id": -1},
        {"payload": "private"},
        {"items": [{"id": i} for i in range(101)]},
    ],
)
def test_page_bounds_and_extra_fields(fields):
    with pytest.raises(ValidationError):
        BusinessPage[Item].model_validate({"items": [], **fields})


def test_strict_error_schema():
    with pytest.raises(ValidationError):
        BusinessError(code="invalid code", message="x", request_id="x")
    with pytest.raises(ValidationError):
        BusinessError(code="invalid_request", message="x", request_id="invalid id")


@pytest.mark.asyncio
async def test_missing_membership_and_mismatched_context_fail():
    from starlette.requests import Request

    for state in ({}, {"api_user": SimpleNamespace(is_active=True), "tenant_id": 8}):
        request = Request({"type": "http", "state": state, "headers": []})
        with tenant_scope(7), pytest.raises(HTTPException):
            await require_business_scope(request)


def test_internal_error_does_not_expose_provider_text():
    async def exploding():
        raise RuntimeError("private-provider-content")

    app, _ = sample(permission=exploding)
    with TestClient(app) as client:
        response = client.get("/api/v1/files/items")
    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert "private" not in response.text


@pytest.mark.asyncio
async def test_error_without_request_middleware_generates_safe_id():
    from starlette.requests import Request

    from app.api.v1.conventions import _error

    request = Request({"type": "http", "headers": [], "state": {"request_id": "unsafe id"}})
    response = _error(request, 418)
    assert b"internal_error" in response.body
    assert len(response.headers["x-request-id"]) == 32


def test_command_idempotency_header_is_documented_as_required():
    app, _ = sample()
    operation = app.openapi()["paths"]["/api/v1/files/commands"]["post"]
    assert sum(p["name"] == "Idempotency-Key" for p in operation["parameters"]) == 1
    header = next(p for p in operation["parameters"] if p["name"] == "Idempotency-Key")
    assert header["in"] == "header" and header["required"] is True
    assert header["schema"]["pattern"] == r"^[A-Za-z0-9_.:-]{1,128}$"


@pytest.mark.parametrize("flag", [None, True, False])
def test_settings_parse_and_application_wire_docs_policy(flag):
    import ast
    from pathlib import Path

    from app.core.config import Settings

    settings = Settings(
        _env_file=None,
        SECRET_KEY=secrets.token_urlsafe(32),
        POSTGRES_URL="postgresql://synthetic.invalid/unused",
        ENVIRONMENT="production",
        API_DOCS_ENABLED=flag,
    )
    assert bool(openapi_options(settings.ENVIRONMENT, settings.API_DOCS_ENABLED)["docs_url"]) == bool(flag)
    tree = ast.parse((Path(__file__).resolve().parents[2] / "app/main.py").read_text())
    factory = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "create_application"
    )
    call = next(
        node
        for node in ast.walk(factory)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "FastAPI"
    )
    assert any(
        kw.arg is None and isinstance(kw.value, ast.Call) and kw.value.func.id == "openapi_options"
        for kw in call.keywords
    )
