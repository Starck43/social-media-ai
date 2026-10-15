"""Public API registration cannot grant platform rights or account state.

`POST /api/v1/auth/register` is reachable without a token, so the platform role
and account state must be server-set.
The ORM managers are stubbed: these tests need no database.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import auth
from app.schemas.user import UserCreate
from app.types import PlatformRoleType

VIEWER = SimpleNamespace(id=7, codename=PlatformRoleType.USER.value)
PAYLOAD = {"username": "newcomer", "email": "newcomer@example.com", "password": "strongpassword123"}
HOSTILE = {**PAYLOAD, "is_superuser": True, "is_active": False, "role_id": 1}


def _tokens(subject: str) -> dict:
    return {
        "access_token": f"access-{subject}",
        "refresh_token": f"refresh-{subject}",
        "token_type": "bearer",
        "expires_at": datetime(2030, 1, 1, tzinfo=timezone.utc),
    }


@pytest.fixture
def stubs(monkeypatch):
    created = SimpleNamespace(id=42)
    create_user = AsyncMock(return_value=created)
    role_get = AsyncMock(return_value=VIEWER)
    monkeypatch.setattr(auth.User.objects, "get_by_username_or_email", AsyncMock(return_value=None))
    monkeypatch.setattr(auth.User.objects, "exists", AsyncMock(return_value=False))
    monkeypatch.setattr(auth.User.objects, "create_user", create_user)
    monkeypatch.setattr(auth.PlatformRole.objects, "get_by_codename", role_get)
    monkeypatch.setattr(auth, "create_tokens_pair", _tokens)
    return SimpleNamespace(create_user=create_user, role_get=role_get)


def test_schema_drops_privilege_fields():
    data = UserCreate(**HOSTILE).model_dump()
    assert set(data) == {"username", "email", "password"}


async def test_register_ignores_privilege_fields(stubs):
    result = await auth.create_user(UserCreate(**HOSTILE))

    assert result["access_token"] == "access-42"
    stubs.role_get.assert_awaited_once_with(PlatformRoleType.USER)
    stubs.create_user.assert_awaited_once_with(
        username="newcomer",
        email="newcomer@example.com",
        password="strongpassword123",
        role_id=VIEWER.id,
        is_active=True,
    )


async def test_register_fails_closed_without_viewer_role(stubs):
    stubs.role_get.return_value = None

    with pytest.raises(auth.HTTPException) as error:
        await auth.create_user(UserCreate(**PAYLOAD))

    assert error.value.status_code == 500
    stubs.create_user.assert_not_awaited()


def test_register_http_body_cannot_grant_superuser(stubs):
    app = FastAPI()
    app.include_router(auth.router, prefix="/api/v1/auth")

    response = TestClient(app).post("/api/v1/auth/register", json=HOSTILE)

    assert response.status_code == 200
    kwargs = stubs.create_user.await_args.kwargs
    assert kwargs["is_active"] is True
    assert kwargs["role_id"] == VIEWER.id
