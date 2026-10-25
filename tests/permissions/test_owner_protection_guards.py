"""Platform-owner protection guards in `UserManager`.

Three ways to damage the owner account were reported by the P9 review:

1. the console edit form — an `admin` could POST `role_id` for the owner;
2. `DELETE /api/v1/users/{id}` — the account went through `delete_user`;
3. the console password reset — an `admin` changed the owner's password.

Each guard sits at the seam every caller goes through, so these tests exercise
that seam: no database, no console and no HTTP layer. The ownership test is the
*role*, not the `is_superuser` property, which is false for a deactivated owner
— a suspended owner must not become editable or deletable.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.models.managers.user_manager import UserManager


def _account(user_id: int, codename: str, *, active: bool = True) -> SimpleNamespace:
    """A loaded account as `prefetch_related("role").get()` returns it."""
    return SimpleNamespace(
        id=user_id,
        is_active=active,
        username=f"account-{user_id}",
        is_superuser=active and codename == "superuser",
        role=SimpleNamespace(codename=codename),
    )


def _manager(monkeypatch, accounts: dict[int, SimpleNamespace]) -> UserManager:
    """A `UserManager` whose user lookups answer from `accounts`."""

    class Query:
        def prefetch_related(self, *_):
            return self

        async def get(self, **criteria):
            account = accounts.get(criteria["id"])
            if account is None or criteria.get("is_active") is True and not account.is_active:
                return None
            return account

    monkeypatch.setattr(UserManager, "prefetch_related", lambda self, *_: Query(), raising=False)
    monkeypatch.setattr(UserManager, "update_by_id", AsyncMock(return_value="updated"), raising=False)
    monkeypatch.setattr(UserManager, "delete_by_id", AsyncMock(return_value=True), raising=False)
    # Outside a permission scope there is no ambient actor and no owner bypass.
    monkeypatch.setattr("app.core.permissions.get_current_user", lambda: None)
    monkeypatch.setattr("app.core.tenant_context.is_bypass", lambda: False)
    return UserManager()


# --- the API delete path ----------------------------------------------------


@pytest.mark.parametrize("active", [True, False], ids=["active-owner", "inactive-owner"])
async def test_the_owner_is_never_deleted_through_the_manager(monkeypatch, active):
    """`DELETE /api/v1/users/2` was stopped only by an FK, never by a rule."""
    manager = _manager(monkeypatch, {2: _account(2, "superuser", active=active), 5: _account(5, "user")})

    with pytest.raises(HTTPException) as refused:
        await manager.delete_user(2)

    assert refused.value.status_code == 403
    manager.delete_by_id.assert_not_awaited()


async def test_an_ordinary_account_is_still_deleted(monkeypatch):
    manager = _manager(monkeypatch, {5: _account(5, "user")})

    assert await manager.delete_user(5) is True
    manager.delete_by_id.assert_awaited_once_with(5)


# --- the profile edit path --------------------------------------------------


async def test_an_admin_cannot_rewrite_the_owner_profile(monkeypatch):
    manager = _manager(monkeypatch, {2: _account(2, "superuser")})
    admin = _account(9, "admin")

    with pytest.raises(HTTPException) as refused:
        await manager.update_user(2, {"username": "not-the-owner"}, current_user=admin)

    assert refused.value.status_code == 403
    manager.update_by_id.assert_not_awaited()


async def test_an_inactive_owner_is_still_protected_from_an_admin(monkeypatch):
    """`is_superuser` requires `is_active`; ownership must not vanish with it."""
    manager = _manager(monkeypatch, {2: _account(2, "superuser", active=False)})
    admin = _account(9, "admin")

    with pytest.raises(HTTPException) as refused:
        await manager.update_user(2, {"username": "not-the-owner"}, current_user=admin)

    assert refused.value.status_code == 403
    manager.update_by_id.assert_not_awaited()


async def test_an_admin_cannot_rewrite_the_owner_profile_without_an_explicit_actor(monkeypatch):
    """A caller that passes no actor must not read as "the owner himself"."""
    manager = _manager(monkeypatch, {2: _account(2, "superuser")})

    with pytest.raises(HTTPException) as refused:
        await manager.update_user(2, {"username": "not-the-owner"})

    assert refused.value.status_code == 403
    manager.update_by_id.assert_not_awaited()


async def test_the_owner_still_edits_their_own_profile(monkeypatch):
    """The guard protects the account from *others*; locking the owner out is a bug."""
    owner = _account(2, "superuser")
    manager = _manager(monkeypatch, {2: owner})

    assert await manager.update_user(2, {"username": "starck"}, current_user=owner) == "updated"
    manager.update_by_id.assert_awaited_once_with(2, username="starck")


# --- the password path ------------------------------------------------------


async def test_an_admin_cannot_reset_the_owner_password(monkeypatch):
    manager = _manager(monkeypatch, {2: _account(2, "superuser")})
    admin = _account(9, "admin")
    hasher = AsyncMock()
    monkeypatch.setattr("app.models.managers.user_manager.pwd_context", SimpleNamespace(hash=hasher))

    with pytest.raises(HTTPException) as refused:
        await manager.change_password(2, "", "Str0ng!Pass", current_user=admin)

    assert refused.value.status_code == 403
    manager.update_by_id.assert_not_awaited()
    hasher.assert_not_called()


async def test_an_anonymous_reset_of_the_owner_password_is_refused(monkeypatch):
    manager = _manager(monkeypatch, {2: _account(2, "superuser")})

    with pytest.raises(HTTPException) as refused:
        await manager.change_password(2, "", "Str0ng!Pass", current_user=None)

    assert refused.value.status_code == 403
    manager.update_by_id.assert_not_awaited()


async def test_an_inactive_owner_password_is_still_guarded(monkeypatch):
    manager = _manager(monkeypatch, {2: _account(2, "superuser", active=False)})

    with pytest.raises(HTTPException) as refused:
        await manager.change_password(2, "", "Str0ng!Pass", current_user=_account(9, "admin"))

    assert refused.value.status_code == 403
    manager.update_by_id.assert_not_awaited()


async def test_the_owner_changes_their_own_password_with_the_current_one(monkeypatch):
    owner = _account(2, "superuser")
    owner.hashed_password = "stored-hash"
    manager = _manager(monkeypatch, {2: owner})
    monkeypatch.setattr("app.models.managers.user_manager.verify_password", lambda raw, hashed: raw == "current")
    monkeypatch.setattr("app.models.managers.user_manager.pwd_context", SimpleNamespace(hash=lambda raw: f"hashed:{raw}"))

    assert await manager.change_password(2, "current", "Str0ng!Pass", current_user=owner) is True
    manager.update_by_id.assert_awaited_once_with(2, hashed_password="hashed:Str0ng!Pass")


async def test_the_owner_self_change_still_checks_the_current_password(monkeypatch):
    owner = _account(2, "superuser")
    owner.hashed_password = "stored-hash"
    manager = _manager(monkeypatch, {2: owner})
    monkeypatch.setattr("app.models.managers.user_manager.verify_password", lambda raw, hashed: False)

    with pytest.raises(HTTPException) as refused:
        await manager.change_password(2, "wrong", "Str0ng!Pass", current_user=owner)

    assert refused.value.status_code == 400
    manager.update_by_id.assert_not_awaited()


async def test_an_ordinary_password_reset_still_works(monkeypatch):
    """The guard is about the owner, not about switching the admin flow off."""
    admin = _account(9, "admin")
    target = _account(5, "user")
    target.hashed_password = "stored-hash"
    # `change_password` checks the actor's grants: it reloads the actor with
    # `role.permissions` and asks the user model for `user.update`.
    admin.has_admin_access = lambda: True
    admin.has_perm_for = lambda model, action: True
    manager = _manager(monkeypatch, {5: target, 9: admin})
    checked = []
    monkeypatch.setattr(
        "app.models.managers.user_manager.verify_password",
        lambda raw, hashed: checked.append(raw) or raw == "known",
    )
    monkeypatch.setattr("app.models.managers.user_manager.pwd_context", SimpleNamespace(hash=lambda raw: f"hashed:{raw}"))

    assert await manager.change_password(5, "known", "Str0ng!Pass", current_user=admin, is_admin_reset=True) is True

    # The reset form is the only flow that skips the current password. Any
    # other caller is verified as a self-change, even with the update grant.
    checked.clear()
    with pytest.raises(HTTPException) as refused:
        await manager.change_password(5, "", "Str0ng!Pass", current_user=admin, is_admin_reset=False)
    assert refused.value.status_code == 400
    assert checked == [""], "the self-change path must hash-check, not skip"
    manager.update_by_id.assert_awaited_once_with(5, hashed_password="hashed:Str0ng!Pass")
