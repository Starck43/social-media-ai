"""Console guards around the platform-owner account.

The permissions suite pins the manager seams; this file pins what the console
does on top of them: the edit view refuses the owner for any other operator
(before sqladmin writes anything), and the password pages answer 403 for the
owner's account on both the GET form and the POST write, without touching
storage. No database, no console mount, no HTTP server — just the views, the
routes and inert doubles.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.admin import endpoints
from app.admin.views import UserAdmin


def _account(user_id: int, codename: str, *, active: bool = True) -> SimpleNamespace:
    """An account as the console loads it, with the role badge it guards on."""
    return SimpleNamespace(
        id=user_id,
        username=f"account-{user_id}",
        is_active=active,
        is_superuser=active and codename == "superuser",
        role=SimpleNamespace(codename=codename),
    )


@pytest.fixture
def users(monkeypatch):
    """Point every `User.objects` read at a dict of loaded accounts."""

    def install(accounts: dict[int, SimpleNamespace]) -> None:
        class Query:
            def prefetch_related(self, *_):
                return self

            async def get(self, **criteria):
                return accounts.get(criteria["id"])

        monkeypatch.setattr("app.models.identity.user.User.objects", SimpleNamespace(prefetch_related=lambda *_: Query()))

    return install


def _request(actor) -> Request:
    """A bare Starlette request; the view reads the actor off `request.state`."""
    request = Request({"type": "http", "method": "POST", "path": "/admin/user/edit/2", "headers": []})
    request.state.admin_user = actor
    return request


# --- `UserAdmin.update_model` ----------------------------------------------


async def test_the_console_refuses_to_edit_the_owner_for_another_operator(users, monkeypatch):
    """The role picker renders read-only, but the POST is the real gate."""
    users({2: _account(2, "superuser")})
    write = AsyncMock(return_value=True)
    monkeypatch.setattr("sqladmin.models.ModelView.update_model", write)

    with pytest.raises(HTTPException) as refused:
        await UserAdmin().update_model(_request(_account(9, "admin")), "2", {"username": "not-the-owner"})

    assert refused.value.status_code == 403
    write.assert_not_awaited()


async def test_the_console_refuses_the_deactivated_owner(users, monkeypatch):
    """`is_superuser` is false while the owner is inactive; the role still rules."""
    users({2: _account(2, "superuser", active=False)})
    write = AsyncMock(return_value=True)
    monkeypatch.setattr("sqladmin.models.ModelView.update_model", write)

    with pytest.raises(HTTPException) as refused:
        await UserAdmin().update_model(_request(_account(9, "admin")), "2", {"username": "not-the-owner"})

    assert refused.value.status_code == 403
    write.assert_not_awaited()


async def test_the_console_still_refuses_a_forged_role_id(users, monkeypatch):
    """Even for its own account the console never writes `role_id` directly."""
    users({2: _account(2, "superuser")})
    write = AsyncMock(return_value=True)
    monkeypatch.setattr("sqladmin.models.ModelView.update_model", write)

    with pytest.raises(HTTPException) as refused:
        await UserAdmin().update_model(_request(_account(2, "superuser")), "2", {"role_id": 3})

    assert refused.value.status_code == 400
    write.assert_not_awaited()


async def test_the_owner_edits_their_own_identity_through_the_console(users, monkeypatch):
    users({2: _account(2, "superuser")})
    write = AsyncMock(return_value=True)
    monkeypatch.setattr("sqladmin.models.ModelView.update_model", write)

    assert await UserAdmin().update_model(_request(_account(2, "superuser")), "2", {"username": "starck"}) is True
    assert write.await_args.args[2] == {"username": "starck"}


# --- the console password routes -------------------------------------------


def _password_route(path: str):
    """The registered callable for one admin route, or the test fails loudly."""
    for route in endpoints.router.routes:
        if getattr(route, "path", None) == path:
            return route.endpoint
    raise AssertionError(f"route {path} is not registered")


@pytest.fixture
def password_console(monkeypatch, request):
    """A signed-in admin on the password pages, with every write watched."""
    admin = _account(9, "admin")
    admin.has_admin_access = lambda: True
    admin.is_platform_admin = True
    admin.hashed_password = "stored-hash"
    owner = _account(2, "superuser", active=getattr(request, "param", True))

    async def authenticate(token=None, *args, **kwargs):
        return admin

    update = AsyncMock(return_value=True)

    class Query:
        def prefetch_related(self, *_):
            return self

        async def get(self, **criteria):
            return {admin.id: admin, owner.id: owner}.get(criteria["id"])

    monkeypatch.setattr("app.admin.endpoints.get_authenticated_user", authenticate)
    # The routes read `User.objects` for the actor, the target and the write.
    monkeypatch.setattr(
        "app.admin.endpoints.User",
        SimpleNamespace(
            objects=SimpleNamespace(prefetch_related=lambda *_: Query(), update_by_id=update)
        ),
    )
    # The POST verifies CSRF before it looks at the target; a valid-session
    # request is what a browser sends, so the refusal under test is the guard.
    monkeypatch.setattr(
        "app.admin.endpoints.get_csrf_manager",
        lambda _request: SimpleNamespace(verify_token=lambda token: bool(token)),
    )

    async def permit(*args, **kwargs):
        return admin

    monkeypatch.setattr("app.admin.endpoints.require_admin_perm", permit)
    return SimpleNamespace(admin=admin, update=update)


def _session_request(path: str) -> Request:
    request = Request({"type": "http", "method": "POST", "path": path, "headers": []})
    request.scope["session"] = {"token": "session-token"}
    return request


async def test_the_password_form_is_refused_for_the_owner(password_console):
    """An `admin` may not even open a reset form for the owner's account."""
    with pytest.raises(HTTPException) as refused:
        await endpoints.change_password_form_path(_session_request("/admin/user/change-password/2"), user_id=2)

    assert refused.value.status_code == 403
    password_console.update.assert_not_awaited()


async def test_the_password_write_is_refused_for_the_owner_without_writing(password_console):
    """The POST answers 403 before storage sees a new hash."""
    request = _session_request("/admin/user/change-password/2")

    with pytest.raises(HTTPException) as refused:
        await endpoints.change_password_submit_path(
            request,
            user_id=2,
            new_password="Str0ng!Pass1",
            confirm_password="Str0ng!Pass1",
            csrf_token="whatever",
            current_password=None,
        )

    assert refused.value.status_code == 403
    password_console.update.assert_not_awaited()


# `is_superuser` folds in `is_active`, so a suspended owner loses authority but
# not ownership: the console must still refuse both password pages for them.


@pytest.mark.parametrize("password_console", [False], indirect=True, ids=["inactive-owner"])
async def test_the_password_form_is_refused_for_the_inactive_owner(password_console):
    with pytest.raises(HTTPException) as refused:
        await endpoints.change_password_form_path(_session_request("/admin/user/change-password/2"), user_id=2)

    assert refused.value.status_code == 403
    password_console.update.assert_not_awaited()


@pytest.mark.parametrize("password_console", [False], indirect=True, ids=["inactive-owner"])
async def test_the_password_write_is_refused_for_the_inactive_owner_without_writing(password_console):
    request = _session_request("/admin/user/change-password/2")

    with pytest.raises(HTTPException) as refused:
        await endpoints.change_password_submit_path(
            request,
            user_id=2,
            new_password="Str0ng!Pass1",
            confirm_password="Str0ng!Pass1",
            csrf_token="whatever",
            current_password=None,
        )

    assert refused.value.status_code == 403
    password_console.update.assert_not_awaited()
