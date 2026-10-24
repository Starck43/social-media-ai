"""Platform-owner role guards (P9-T02).

No database: the owner predicate and the role-assignment guards are pure
functions of the loaded accounts, so escalation rules can be pinned without the
shared test schema.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.access_catalog import role_label
from app.models.identity.user import User
from app.models.managers.user_manager import UserManager
from app.types import ActionType, PlatformRoleType


def _user(codename: str | None, *, active: bool = True) -> User:
    user = User(is_active=active)
    role = SimpleNamespace(codename=codename) if codename else None
    # The ORM blocks plain attributes on a mapped relationship; the predicate
    # only reads `role.codename`, so set the instance state directly.
    user.__dict__["role"] = role
    return user


def test_owner_role_is_its_own_level():
    assert PlatformRoleType.SUPERUSER.value == "superuser"
    assert role_label("superuser") == "Собственник платформы"

    owner = _user("superuser")
    assert owner.is_superuser is True
    assert owner.is_platform_admin is True
    assert owner.has_admin_access() is True
    assert owner.model_permissions("User") == set(ActionType)

    admin = _user("admin")
    assert admin.is_superuser is False
    assert admin.is_platform_admin is True

    for codename in ("operator", "user", None):
        account = _user(codename)
        assert account.is_superuser is False
        assert account.is_platform_admin is False


def test_inactive_owner_loses_both_levels():
    owner = _user("superuser", active=False)
    assert owner.is_superuser is False
    assert owner.is_platform_admin is False
    assert owner.has_admin_access() is False
    assert owner.model_permissions("User") == set()


def test_role_lookup_does_not_alias_owner_to_admin():
    """A silent `superuser -> admin` alias would hide the owner from every gate."""
    import inspect

    from app.models.managers.role_manager import RoleManager

    source = inspect.getsource(RoleManager.get_by_codename)
    assert '"superuser"' not in source


class _RoleQuery:
    """Minimal stand-in for the manager's prefetch-aware lookups."""

    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def prefetch_related(self, *_: object) -> "_RoleQuery":
        return self

    async def get(self, **_: object) -> object | None:
        return self._rows[0] if self._rows else None


def _patch_queries(monkeypatch: pytest.MonkeyPatch, actor: object | None, target: object | None) -> list[str]:
    """Route actor and target lookups; record which lookups happened."""
    calls: list[str] = []

    class _Objects:
        """Actor/target lookups through the manager's prefetch chain."""

        def __init__(self, actor: object | None, target: object | None) -> None:
            self._actor = actor
            self._target = target

        def prefetch_related(self, *_: object) -> "_Objects":
            return self

        async def get(self, id: int, **_: object) -> object | None:
            if id == getattr(self._actor, "id", None):
                calls.append("actor")
                return self._actor
            calls.append("target")
            return self._target

    monkeypatch.setattr(User, "objects", _Objects(actor, target), raising=False)
    # `UserManager` inherits `prefetch_related` from the base manager, so the
    # guard can be installed on the subclass without mutating the base.
    monkeypatch.setattr(UserManager, "prefetch_related", lambda self, *a: _Objects(actor, target), raising=False)
    return calls


@pytest.mark.parametrize("role_code", [PlatformRoleType.SUPERUSER, "superuser"])
@pytest.mark.asyncio
async def test_no_assignment_path_grants_the_owner_role(role_code, monkeypatch: pytest.MonkeyPatch):
    """The owner role is migration-only: even a platform admin may not hand it out."""
    calls = _patch_queries(
        monkeypatch,
        SimpleNamespace(id=1, is_active=True, is_platform_admin=True, is_superuser=False),
        None,
    )
    with pytest.raises(HTTPException) as raised:
        await UserManager().set_platform_role(user_id=2, role_code=role_code, actor_id=1)
    assert raised.value.status_code == 403
    assert "миграцией" in str(raised.value.detail)
    assert calls == ["actor"]


@pytest.mark.asyncio
async def test_platform_admin_cannot_demote_the_owner_account(monkeypatch: pytest.MonkeyPatch):
    calls = _patch_queries(
        monkeypatch,
        SimpleNamespace(id=1, is_active=True, is_platform_admin=True, is_superuser=False),
        SimpleNamespace(id=2, is_active=True, is_platform_admin=True, is_superuser=True),
    )
    with pytest.raises(HTTPException) as raised:
        await UserManager().set_platform_role(user_id=2, role_code=PlatformRoleType.ADMIN, actor_id=1)
    assert raised.value.status_code == 403
    assert "собственника" in str(raised.value.detail)
    assert calls == ["actor", "target"]


@pytest.mark.asyncio
async def test_owner_cannot_change_their_own_role(monkeypatch: pytest.MonkeyPatch):
    owner = SimpleNamespace(id=2, is_active=True, is_platform_admin=True, is_superuser=True)
    _patch_queries(monkeypatch, owner, owner)
    with pytest.raises(HTTPException) as raised:
        await UserManager().set_platform_role(user_id=2, role_code=PlatformRoleType.ADMIN, actor_id=2)
    assert raised.value.status_code == 403
