"""Platform-owner protection guards (P9-T04/T05/T06).

Three ways to damage the owner account were reported by the P9 review:

1. the console edit form — an `admin` could POST `role_id` for the owner;
2. `DELETE /api/v1/users/{id}` — the account went through `delete_user`;
3. the console password reset — an `admin` opened the owner's reset form.

Each guard sits at the seam every caller goes through, so these tests exercise
that seam: no database, no console and no HTTP layer.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.models.managers.user_manager import UserManager

ROOT = Path(__file__).resolve().parents[2]


def _account(user_id: int, codename: str) -> SimpleNamespace:
    """A loaded account as `prefetch_related("role").get()` returns it."""
    return SimpleNamespace(id=user_id, is_superuser=codename == "superuser", role=SimpleNamespace(codename=codename))


def _manager(monkeypatch, accounts: dict[int, SimpleNamespace]) -> UserManager:
    """A `UserManager` whose user lookups answer from `accounts`."""

    async def get(*, id: int):  # noqa: A002 - mirrors the queryset call
        return accounts.get(id)

    class Query:
        def prefetch_related(self, *_):
            return self

        async def get(self, **criteria):
            return await get(id=criteria["id"])

    monkeypatch.setattr(UserManager, "prefetch_related", lambda self, *_: Query(), raising=False)
    monkeypatch.setattr(UserManager, "get", staticmethod(get), raising=False)
    monkeypatch.setattr(UserManager, "update_by_id", AsyncMock(return_value="updated"), raising=False)
    monkeypatch.setattr(UserManager, "delete_by_id", AsyncMock(return_value=True), raising=False)
    return UserManager()


# --- the API delete path ----------------------------------------------------


async def test_the_owner_is_never_deleted_through_the_manager(monkeypatch):
    """`DELETE /api/v1/users/2` was stopped only by an FK, never by a rule."""
    manager = _manager(monkeypatch, {2: _account(2, "superuser"), 5: _account(5, "user")})

    with pytest.raises(HTTPException) as refused:
        await manager.delete_user(2)

    assert refused.value.status_code == 403
    manager.delete_by_id.assert_not_awaited()


async def test_an_ordinary_account_is_still_deleted(monkeypatch):
    manager = _manager(monkeypatch, {5: _account(5, "user")})

    assert await manager.delete_user(5) is True
    manager.delete_by_id.assert_awaited_once_with(5)


# --- the console profile edit path ------------------------------------------


async def test_an_admin_cannot_rewrite_the_owner_profile(monkeypatch):
    manager = _manager(monkeypatch, {2: _account(2, "superuser")})
    admin = _account(9, "admin")

    with pytest.raises(HTTPException) as refused:
        await manager.update_user(2, {"username": "not-the-owner"}, current_user=admin)

    assert refused.value.status_code == 403
    manager.update_by_id.assert_not_awaited()


async def test_the_owner_still_edits_their_own_profile(monkeypatch):
    """The guard protects the account from *others*; locking the owner out is a bug."""
    owner = _account(2, "superuser")
    manager = _manager(monkeypatch, {2: owner})

    assert await manager.update_user(2, {"username": "starck"}, current_user=owner) == "updated"


# --- the console route guards -----------------------------------------------


def _class_method(path: str, class_name: str, method_name: str) -> str:
    """The source of one method, so a guard cannot vanish unnoticed."""
    tree = ast.parse((ROOT / path).read_text())
    view = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    method = next(n for n in view.body if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == method_name)
    return ast.unparse(method)


def test_the_console_refuses_to_edit_the_owner_from_another_account():
    """The role picker renders read-only, but the POST is the real gate."""
    body = _class_method("app/admin/views.py", "UserAdmin", "update_model")
    assert "is_superuser" in body
    assert "admin_user" in body


@pytest.mark.parametrize("route", ["change_password_form_path", "change_password_submit_path"])
def test_the_console_never_opens_the_owner_password_reset(route):
    """Both the GET form and the POST write answer 403 for the owner."""
    tree = ast.parse((ROOT / "app/admin/endpoints.py").read_text())
    function = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == route)
    assert "is_superuser" in ast.unparse(function)
