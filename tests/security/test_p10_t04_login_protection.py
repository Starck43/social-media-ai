"""Shared login-failure budget (P10-T04).

The point of the store is that the budget is per account and per client address
*in the database*, so a cleared cookie or a different process does not reset it.
These tests exercise the real store against the validated test schema, assert the
threshold behaviour, and pin the generic answer and the dummy verifier that make
an unknown account indistinguishable from a wrong password.

Every synthetic key is namespaced and removed in `finally`; the suite writes no
other table.
"""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.hashing import get_password_hash, verify_password
from app.main import create_application
from app.models import PlatformRole, User
from app.models.identity.login_attempt import LoginAttempt
from app.services.user import login_protection
from app.services.user.auth import GENERIC_LOGIN_ERROR, authenticate
from app.services.user.login_protection import (
    LoginLocked,
    clear_login_failures,
    guard_login,
    record_login_failure,
)
from app.types import PlatformRoleType

PREFIX = "p10t04-"
PASSWORD = "secret-password-1"
CSRF_RE = re.compile(r'name="_csrf" value="([^"]+)"')


def _key(name: str) -> str:
    return f"{PREFIX}{name}-{uuid4().hex[:10]}"


async def _drop(*identifiers: str) -> None:
    async with async_session_maker() as session:
        await session.execute(delete(LoginAttempt).where(LoginAttempt.identifier.in_(list(identifiers))))
        await session.commit()


@pytest.fixture
def low_budgets(monkeypatch):
    """Small, explicit budgets so a test does not need twenty round trips."""

    monkeypatch.setattr(settings, "MAX_LOGIN_ATTEMPTS", 3)
    monkeypatch.setattr(settings, "LOGIN_IP_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(settings, "LOGIN_TIMEOUT_MINUTES", 15)


@pytest.mark.asyncio
async def test_failures_below_the_threshold_do_not_lock(low_budgets):
    account = _key("below")
    try:
        await record_login_failure(account)
        await record_login_failure(account)
        await guard_login(account)  # must not raise

        async with async_session_maker() as session:
            row = (await session.execute(select(LoginAttempt).where(LoginAttempt.identifier == account))).scalar_one()
            assert row.failed_count == 2
            assert row.locked_until is None
    finally:
        await _drop(account)


@pytest.mark.asyncio
async def test_the_account_budget_locks_and_a_success_clears_it(low_budgets):
    account = _key("lock")
    try:
        for _ in range(3):
            await record_login_failure(account)
        with pytest.raises(LoginLocked):
            await guard_login(account)

        await clear_login_failures(account)
        await guard_login(account)  # a proven password restores the budget

        async with async_session_maker() as session:
            assert (
                await session.execute(select(LoginAttempt).where(LoginAttempt.identifier == account))
            ).first() is None
    finally:
        await _drop(account)


@pytest.mark.asyncio
async def test_the_address_budget_is_shared_by_different_accounts(low_budgets):
    """One address cannot keep guessing different usernames."""

    address = _key("address")
    first, second, third, fresh = (_key(f"addr-{index}") for index in range(4))
    try:
        for account in (first, second, third):
            await record_login_failure(account, address)
        # A fourth account from the same address is refused before it is tried.
        with pytest.raises(LoginLocked):
            await guard_login(fresh, address)
        # The same fresh account without that address is still allowed.
        await guard_login(fresh)
    finally:
        await _drop(address, first, second, third, fresh)


@pytest.mark.asyncio
async def test_the_dummy_verifier_is_a_real_hash():
    """`dummy_hash` must be a bcrypt value, not the old `"dummy_hash"` literal."""

    hash_value = login_protection.dummy_hash()

    assert hash_value.startswith("$2")
    assert verify_password("never-the-password", hash_value) is False
    assert await login_protection.verify_dummy_password("anything") is None


@pytest.mark.asyncio
async def test_an_unknown_account_and_a_wrong_password_answer_identically(monkeypatch):
    """Both credential failures carry the same status and detail."""

    known = SimpleNamespace(
        id=1,
        username=_key("known"),
        hashed_password=get_password_hash("right-password"),
        is_active=True,
    )

    async def get_by_username_or_email(value):
        return known if value == known.username else None

    monkeypatch.setattr(
        "app.services.user.auth.User.objects.get_by_username_or_email",
        AsyncMock(side_effect=get_by_username_or_email),
    )

    details = []
    for username, password in ((known.username, "wrong-password"), (_key("unknown"), "whatever")):
        with pytest.raises(HTTPException) as refused:
            await asyncio.wait_for(authenticate(username, password, verify_password), timeout=10)
        assert refused.value.status_code == 400
        details.append(refused.value.detail)

    assert details == [GENERIC_LOGIN_ERROR, GENERIC_LOGIN_ERROR]


@pytest.mark.asyncio
async def test_the_web_and_api_surfaces_spend_one_budget(low_budgets, monkeypatch):
    """Failures on `/app/login` lock the same account out of `/api/v1/auth/login`."""

    role = await PlatformRole.objects.get(codename=PlatformRoleType.USER.value)
    username = _key("shared")
    web_address, api_address = _key("web-address"), _key("api-address")
    # Distinct addresses keep the address budget out of the comparison, so the
    # refusal can only come from the account row both surfaces write.
    monkeypatch.setattr("app.web.auth.client_address", lambda request: web_address)
    monkeypatch.setattr("app.api.v1.endpoints.auth.client_address", lambda request: api_address)
    user = await User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password=PASSWORD,
        role_id=role.id,
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=create_application()), base_url="http://testserver", follow_redirects=True
        ) as client:
            token = (CSRF_RE.search((await client.get("/app/login")).text)).group(1)
            for _ in range(3):
                refused = await client.post(
                    "/app/login", data={"username": username, "password": "wrong-password", "_csrf": token}
                )
                assert refused.status_code == 400

            locked = await client.post("/api/v1/auth/login", data={"username": username, "password": PASSWORD})

        assert locked.status_code == 400
        assert locked.json()["detail"] == GENERIC_LOGIN_ERROR
    finally:
        await _drop(username, web_address, api_address)
        await User.objects.delete_user(user.id)


@pytest.mark.asyncio
async def test_an_inactive_account_is_not_an_enumeration_hint(monkeypatch):
    inactive = SimpleNamespace(
        id=2,
        username=_key("inactive"),
        hashed_password=get_password_hash("right-password"),
        is_active=False,
    )
    monkeypatch.setattr(
        "app.services.user.auth.User.objects.get_by_username_or_email",
        AsyncMock(return_value=inactive),
    )

    with pytest.raises(HTTPException) as refused:
        await authenticate(inactive.username, "right-password", verify_password)

    assert refused.value.status_code == 400
    assert refused.value.detail == GENERIC_LOGIN_ERROR
