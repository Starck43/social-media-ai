"""Source-only public HTTP policy contracts for P11-T08.

The checks exercise middleware and generated OpenAPI without opening the shared
PostgreSQL test schema. Workspace data behavior remains covered by the existing
DB-backed API scope and task-launch suites.
"""

__source_only__ = True

from types import SimpleNamespace

import jwt
import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from starlette.responses import JSONResponse

from app.core import api_scope
from app.core.api_scope import ApiScopeMiddleware
from app.core.config import settings
from app.core.request_context import RequestContextMiddleware
from app.main import create_application
from app.schemas.task import TaskPlanRunRequest


@pytest.mark.parametrize(
    ("path", "is_public"),
    [
        ("/api/v1/auth/login", True),
        ("/api/v1/auth/login/", True),
        ("/api/v1/auth/login/extra", False),
        ("/api/v1/health", True),
        ("/api/v1/healthz", False),
        ("/api/v1/social/callback", True),
        ("/api/v1/social/callback/extra", False),
        ("/api/v1/test-db", False),
        ("/api/v10/health", False),
    ],
)
def test_public_api_allowlist_matches_exact_paths(path: str, is_public: bool) -> None:
    assert api_scope._is_public(path) is is_public


async def test_expired_access_token_is_rejected_before_user_lookup(monkeypatch) -> None:
    middleware = ApiScopeMiddleware(_unused_asgi_app)
    looked_up = False

    async def unexpected_lookup(*_args, **_kwargs):
        nonlocal looked_up
        looked_up = True
        raise AssertionError("expired JWT must be rejected before ORM lookup")

    monkeypatch.setattr(
        "app.models.User.objects.prefetch_related", lambda *_args: SimpleNamespace(get=unexpected_lookup)
    )
    token = jwt.encode(
        {"sub": "123", "type": "access", "exp": 1},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/sources",
            "headers": [(b"authorization", f"Bearer {token}".encode())],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 1),
            "scheme": "http",
        }
    )

    assert await middleware._authenticate(request) is None
    assert looked_up is False


@pytest.mark.parametrize("token_payload", [{"sub": "123", "type": "refresh"}, {"sub": "not-an-int", "type": "access"}])
async def test_wrong_token_type_or_malformed_subject_is_rejected_without_lookup(monkeypatch, token_payload) -> None:
    middleware = ApiScopeMiddleware(_unused_asgi_app)
    looked_up = False

    async def unexpected_lookup(*_args, **_kwargs):
        nonlocal looked_up
        looked_up = True
        raise AssertionError("invalid identity must be rejected before ORM lookup")

    monkeypatch.setattr(
        "app.models.User.objects.prefetch_related", lambda *_args: SimpleNamespace(get=unexpected_lookup)
    )
    token = jwt.encode(token_payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/sources",
            "headers": [(b"authorization", f"Bearer {token}".encode())],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 1),
            "scheme": "http",
        }
    )

    assert await middleware._authenticate(request) is None
    assert looked_up is False


async def test_deleted_or_revoked_actor_is_rejected_by_fresh_user_lookup(monkeypatch) -> None:
    middleware = ApiScopeMiddleware(_unused_asgi_app)
    token = jwt.encode(
        {"sub": "123", "type": "access"},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    looked_up_ids: list[int] = []

    async def missing_user(*, id: int):
        looked_up_ids.append(id)
        return None

    monkeypatch.setattr("app.models.User.objects.prefetch_related", lambda *_args: SimpleNamespace(get=missing_user))
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/sources",
            "headers": [(b"authorization", f"Bearer {token}".encode())],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 1),
            "scheme": "http",
        }
    )

    assert await middleware._authenticate(request) is None
    assert looked_up_ids == [123]


async def test_revoked_workspace_membership_is_rechecked_for_selector(monkeypatch) -> None:
    """A previously selected workspace is denied after its membership disappears."""
    from app.models.managers.tenant_manager import TenantUserManager

    async def no_current_memberships(_manager, _user_id):
        return []

    monkeypatch.setattr(TenantUserManager, "web_memberships", no_current_memberships)
    middleware = ApiScopeMiddleware(_unused_asgi_app)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/sources",
            "headers": [(b"x-tenant-id", b"99")],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 1),
            "scheme": "http",
        }
    )

    tenant_id, error, grants = await middleware._resolve_tenant(request, SimpleNamespace(id=7))

    assert (tenant_id, error, grants) == (None, "User has no workspace membership", None)


async def test_unknown_workspace_selector_is_denied(monkeypatch) -> None:
    from app.models.managers.tenant_manager import TenantUserManager, tenants

    memberships = [SimpleNamespace(tenant_id=7)]

    async def active_memberships(_manager, _user_id):
        return memberships

    async def missing_tenant(**_kwargs):
        return None

    monkeypatch.setattr(TenantUserManager, "web_memberships", active_memberships)
    monkeypatch.setattr(tenants, "get", missing_tenant)
    middleware = ApiScopeMiddleware(_unused_asgi_app)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/sources",
            "headers": [(b"x-tenant-id", b"999")],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 1),
            "scheme": "http",
        }
    )

    tenant_id, error, grants = await middleware._resolve_tenant(request, SimpleNamespace(id=7))

    assert (tenant_id, error, grants) == (None, "Workspace not found", None)


async def test_authenticated_unknown_path_and_method_keep_framework_404_405() -> None:
    app = FastAPI()

    @app.get("/api/v1/known")
    async def known():
        return {"ok": True}

    middleware = ApiScopeMiddleware(app)
    middleware._authenticate = _authenticated_user
    middleware._resolve_tenant = _tenant_allowed
    async with AsyncClient(transport=ASGITransport(app=middleware), base_url="http://testserver") as client:
        unknown_path = await client.get("/api/v1/missing")
        unknown_method = await client.post("/api/v1/known")

    assert unknown_path.status_code == 404
    assert unknown_method.status_code == 405


@pytest.mark.parametrize(
    ("value", "is_preserved"),
    [
        ("trace-01:abc_DEF.9", True),
        ("a" * 128, True),
        ("a" * 129, False),
        ("bad id", False),
        ("bad\r\nInjected: yes", False),
        ("привет", False),
    ],
)
async def test_request_id_is_bounded_validated_and_echoed(value: str, is_preserved: bool) -> None:
    observed: dict[str, str] = {}

    async def endpoint(scope, receive, send):
        observed["request_id"] = scope["state"]["request_id"]
        await JSONResponse({"request_id": observed["request_id"]})(scope, receive, send)

    app = RequestContextMiddleware(endpoint)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/", headers={"x-request-id": value})

    request_id = response.headers["x-request-id"]
    assert response.json() == {"request_id": request_id}
    if is_preserved:
        assert request_id == value
    else:
        assert request_id != value
        assert len(request_id) == 32
        assert all(char in "0123456789abcdef" for char in request_id)
    assert observed["request_id"] == request_id


def test_expensive_list_routes_expose_bounded_pagination() -> None:
    schema = create_application().openapi()
    list_paths = (
        "/api/v1/sources",
        "/api/v1/tasks",
        "/api/v1/credentials",
        "/api/v1/ai/scenarios",
        "/api/v1/llm/llm-models/",
        "/api/v1/llm/llm-providers/",
        "/api/v1/users",
        "/api/v1/users/roles/",
    )

    for path in list_paths:
        parameters = {
            parameter["name"]: parameter["schema"] for parameter in schema["paths"][path]["get"]["parameters"]
        }
        if "limit" in parameters:
            assert parameters["limit"]["minimum"] == 1, path
            assert parameters["limit"]["maximum"] == 100, path
        if "offset" in parameters:
            assert parameters["offset"]["minimum"] == 0, path
        if "page" in parameters:
            assert parameters["page"]["minimum"] == 1, path
        if "size" in parameters:
            assert parameters["size"]["minimum"] == 1, path
            assert parameters["size"]["maximum"] == 100, path


async def test_task_admission_429_has_retry_hint_and_safe_detail(monkeypatch) -> None:
    from fastapi import HTTPException

    from app.api.v1.endpoints.tasks import run_task_plan
    from app.services.task_contracts import TaskLaunchError

    monkeypatch.setattr("app.core.permissions.has_permission", lambda *_args: True)

    async def reject_active_limit(*_args, **_kwargs):
        raise TaskLaunchError("actor_task_limit", cause=RuntimeError("private SQL detail"))

    monkeypatch.setattr("app.services.task_launch.launch_social_task", reject_active_limit)
    request = TaskPlanRunRequest(
        request_id="p11t08-quota-01",
        task_plan={"source_scope": "all", "trigger": {"type": "once"}},
    )
    try:
        await run_task_plan(request, _user=SimpleNamespace(id=7))
    except HTTPException as error:
        assert error.status_code == 429
        assert error.headers == {"Retry-After": "30"}
        assert "private SQL detail" not in str(error.detail)
        assert "Too many active task launches" in error.detail
    else:
        raise AssertionError("actor admission rejection must map to HTTP 429")


async def _unused_asgi_app(scope, receive, send):
    raise AssertionError("middleware auth helper test must not invoke ASGI app")


async def _authenticated_user(_request):
    return SimpleNamespace(id=7, is_active=True, username="member", has_admin_access=lambda: False)


async def _tenant_denied(_request, _user):
    return None, "User is not a member of this workspace", None


async def _tenant_allowed(_request, _user):
    return 7, None, None
