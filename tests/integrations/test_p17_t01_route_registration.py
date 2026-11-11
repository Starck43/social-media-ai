"""Static route contract, no DB and no external calls."""

from fastapi import FastAPI

from app.api.v1.endpoints import onec
from app.api.v1.entry import router
from app.services.user.auth import get_authenticated_user


def test_onec_routes_are_registered_with_bearer_auth():
    app = FastAPI()
    app.include_router(router)
    paths = app.openapi()["paths"]
    assert {path: set(methods) for path, methods in paths.items() if path.startswith("/integrations/1c/")} == {
        "/integrations/1c/connect": {"post"},
        "/integrations/1c/connections": {"get"},
        "/integrations/1c/connections/{connection_id}": {"delete"},
        "/integrations/1c/connections/{connection_id}/health-check": {"post"},
        "/integrations/1c/hosts": {"get", "post"},
        "/integrations/1c/hosts/{host_id}": {"delete"},
    }
    included = [r for r in router.routes if getattr(r, "original_router", None) is onec.router]
    assert len(included) == 1
    assert included[0].include_context.prefix == "/integrations/1c"
    assert any(dep.dependency is get_authenticated_user for dep in included[0].include_context.dependencies)
