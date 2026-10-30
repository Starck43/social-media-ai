"""Isolated HTTP contract for canonical and legacy credential-disable routes."""

from __future__ import annotations

import importlib.util
import itertools
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
_HELPER_SPEC = importlib.util.spec_from_file_location(
    "_q10_source_import_isolation", ROOT / "tests" / "source_import_isolation.py"
)
assert _HELPER_SPEC and _HELPER_SPEC.loader
_HELPER = importlib.util.module_from_spec(_HELPER_SPEC)
_HELPER_SPEC.loader.exec_module(_HELPER)
load_isolated_source = _HELPER.load_isolated_source

_LOADS = itertools.count()


class _ActionType:
    VIEW = "view"
    UPDATE = "update"
    CONFIGURE = "configure"


class _CredentialLoginRequest(BaseModel):
    pass


class _CredentialOauthRequest(BaseModel):
    pass


class _CredentialResponse(BaseModel):
    pass


def _credential_api():
    manager = SimpleNamespace(
        get=AsyncMock(side_effect=lambda *, id, user_id: SimpleNamespace(id=id) if id == 7 and user_id == 3 else None),
        update_by_id=AsyncMock(),
    )

    async def require_update(authorization: str | None = Header(default=None)):
        if authorization is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        if authorization not in {"Bearer permitted", "Bearer foreign"}:
            raise HTTPException(status_code=403, detail="Forbidden")
        return SimpleNamespace(id=4 if authorization == "Bearer foreign" else 3)

    requested_permissions = []

    def dependency_factory(model, action):
        requested_permissions.append((model, action))
        return require_update

    imports = {
        "app.api.deps": SimpleNamespace(require_model_perm=dependency_factory),
        "app.models": SimpleNamespace(User=object, UserCredential=object),
        "app.schemas.credential": SimpleNamespace(
            CredentialLoginRequest=_CredentialLoginRequest,
            CredentialOauthRequest=_CredentialOauthRequest,
            CredentialResponse=_CredentialResponse,
        ),
        "app.types": SimpleNamespace(ActionType=_ActionType),
        "app.models.managers.user_credential_manager": SimpleNamespace(user_credentials=manager),
    }
    before_app_modules = {name for name in sys.modules if name == "app" or name.startswith("app.")}
    module = load_isolated_source(
        f"_q10_credentials_{next(_LOADS)}", ROOT / "app/api/v1/endpoints/credentials.py", imports
    )
    after_app_modules = {name for name in sys.modules if name == "app" or name.startswith("app.")}
    assert after_app_modules == before_app_modules
    assert requested_permissions == [
        ("credential", _ActionType.VIEW),
        ("credential", _ActionType.UPDATE),
        ("credential", _ActionType.CONFIGURE),
        ("credential", _ActionType.CONFIGURE),
        ("credential", _ActionType.VIEW),
    ]
    app = FastAPI()
    app.include_router(module.router, prefix="/api/v1/credentials")
    disable_endpoints = {
        route.endpoint for route in app.routes if getattr(route, "path", "").endswith("/{credential_id}/disable")
    }
    assert len(disable_endpoints) == 1
    return TestClient(app), manager


ROUTES = (
    "/api/v1/credentials/7/disable",
    "/api/v1/credentials/credentials/7/disable",
)


@pytest.mark.parametrize("path", ROUTES)
def test_both_disable_routes_call_same_authorized_handler(path):
    client, manager = _credential_api()

    response = client.patch(path, headers={"Authorization": "Bearer permitted"})

    assert response.status_code == 200
    assert response.json() == {"status": "disabled", "credential_id": 7}
    manager.get.assert_awaited_once_with(id=7, user_id=3)
    manager.update_by_id.assert_awaited_once_with(7, is_active=False)


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize(
    ("credential_id", "authorization", "user_id"),
    [(8, "Bearer permitted", 3), (7, "Bearer foreign", 4)],
    ids=["missing", "another-users"],
)
def test_missing_or_foreign_credential_returns_404_without_mutation(path, credential_id, authorization, user_id):
    client, manager = _credential_api()
    path = path.replace("/7/disable", f"/{credential_id}/disable")

    response = client.patch(path, headers={"Authorization": authorization})

    assert response.status_code == 404
    manager.get.assert_awaited_once_with(id=credential_id, user_id=user_id)
    manager.update_by_id.assert_not_awaited()


@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize(
    ("headers", "status_code"),
    [({}, 401), ({"Authorization": "Bearer denied"}, 403)],
)
def test_both_routes_reject_unauthenticated_or_forbidden_requests(path, headers, status_code):
    client, manager = _credential_api()

    response = client.patch(path, headers=headers)

    assert response.status_code == status_code
    manager.get.assert_not_awaited()
    manager.update_by_id.assert_not_awaited()


def test_openapi_marks_only_legacy_disable_route_deprecated():
    client, _ = _credential_api()
    paths = client.get("/openapi.json").json()["paths"]

    assert paths["/api/v1/credentials/{credential_id}/disable"]["patch"].get("deprecated") is None
    assert paths["/api/v1/credentials/credentials/{credential_id}/disable"]["patch"]["deprecated"] is True
