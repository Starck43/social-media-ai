"""Platform-owner role guards (P9-T02).

No database: the owner predicate and the role-assignment guards are pure
functions of the loaded accounts, so escalation rules can be pinned without the
shared test schema.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.admin.role_picker import RoleChoiceField, role_choices
from app.core.access_catalog import role_label
from app.models.identity.user import User
from app.models.managers.user_manager import UserManager
from app.types import (
    ActionType,
    PlatformRoleType,
    assignable_platform_roles,
    can_assign_platform_role,
    platform_role_rank,
)


def _user(codename: str | None, *, active: bool = True) -> User:
    user = User(is_active=active)
    role = SimpleNamespace(codename=codename) if codename else None
    # The ORM blocks plain attributes on a mapped relationship; the predicate
    # only reads `role.codename`, so set the instance state directly.
    user.__dict__["role"] = role
    return user


def _account(user_id: int, codename: str, *, active: bool = True) -> User:
    """A real `User` holding `codename`: rank rules read `role.codename`."""
    user = _user(codename, active=active)
    user.__dict__["id"] = user_id
    return user


def _actor(codename: str) -> User:
    return _account(1, codename)


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
    """The owner role is bootstrap-only: even the owner may not hand it out."""
    calls = _patch_queries(
        monkeypatch,
        _actor("superuser"),
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
        _actor("superuser"),
        _account(2, "superuser"),
    )
    with pytest.raises(HTTPException) as raised:
        await UserManager().set_platform_role(user_id=2, role_code=PlatformRoleType.ADMIN, actor_id=1)
    assert raised.value.status_code == 403
    assert "собственника" in str(raised.value.detail)
    assert calls == ["actor", "target"]


@pytest.mark.asyncio
async def test_owner_cannot_change_their_own_role(monkeypatch: pytest.MonkeyPatch):
    owner = _account(2, "superuser")
    _patch_queries(monkeypatch, owner, owner)
    with pytest.raises(HTTPException) as raised:
        await UserManager().set_platform_role(user_id=2, role_code=PlatformRoleType.ADMIN, actor_id=2)
    assert raised.value.status_code == 403


def test_rank_orders_the_three_levels_and_never_offers_the_owner_role():
    assert platform_role_rank("user") < platform_role_rank("operator") < platform_role_rank("admin")
    assert platform_role_rank("admin") < platform_role_rank("superuser")
    assert platform_role_rank("nonsense") is None

    assert assignable_platform_roles(_account(1, "superuser")) == [
        PlatformRoleType.USER,
        PlatformRoleType.OPERATOR,
        PlatformRoleType.ADMIN,
    ]
    assert assignable_platform_roles(_account(1, "admin")) == [PlatformRoleType.USER, PlatformRoleType.OPERATOR]
    assert assignable_platform_roles(_account(1, "operator")) == [PlatformRoleType.USER]
    assert assignable_platform_roles(_account(1, "user")) == []


@pytest.mark.parametrize(
    ("actor_role", "target_role", "allowed"),
    [
        ("admin", "operator", True),
        ("admin", "user", True),
        ("admin", "admin", False),
        ("admin", "superuser", False),
        ("operator", "user", True),
        ("operator", "operator", False),
        ("superuser", "admin", True),
        ("superuser", "superuser", False),
        ("user", "user", False),
    ],
)
def test_assignment_stays_strictly_below_the_actor(actor_role, target_role, allowed):
    assert can_assign_platform_role(_account(1, actor_role), target_role) is allowed


@pytest.mark.asyncio
async def test_manager_writes_the_role_when_it_is_below_the_actor(monkeypatch: pytest.MonkeyPatch):
    admin = _account(1, "admin")
    target = _account(2, "user")
    _patch_queries(monkeypatch, admin, target)
    recorded: list[tuple[int, str]] = []

    async def _fake_apply(self, user_id: int, role_id: int) -> None:
        recorded.append((user_id, role_id))

    async def _role_row(codename) -> SimpleNamespace:
        return SimpleNamespace(id=str(codename), codename=str(codename))

    monkeypatch.setattr(UserManager, "_apply_role_change", _fake_apply, raising=False)
    monkeypatch.setattr("app.models.PlatformRole.objects.get_by_codename", _role_row, raising=False)
    await UserManager().set_platform_role(user_id=2, role_code=PlatformRoleType.OPERATOR, actor_id=1)
    assert recorded == [(2, "operator")]


@pytest.mark.asyncio
async def test_admin_cannot_assign_a_peer_the_admin_role(monkeypatch: pytest.MonkeyPatch):
    calls = _patch_queries(monkeypatch, _account(1, "admin"), _account(2, "operator"))
    with pytest.raises(HTTPException) as raised:
        await UserManager().set_platform_role(user_id=2, role_code=PlatformRoleType.ADMIN, actor_id=1)
    assert raised.value.status_code == 403
    assert "выше своей" in str(raised.value.detail)
    # Refused before the target is even loaded.
    assert calls == ["actor"]


@pytest.mark.asyncio
async def test_admin_cannot_rewrite_a_peer_account(monkeypatch: pytest.MonkeyPatch):
    _patch_queries(monkeypatch, _account(1, "admin"), _account(2, "admin"))
    with pytest.raises(HTTPException) as raised:
        await UserManager().set_platform_role(user_id=2, role_code=PlatformRoleType.USER, actor_id=1)
    assert raised.value.status_code == 403
    assert "не ниже своей" in str(raised.value.detail)


def test_console_picker_offers_only_what_the_actor_may_assign():
    labels = [code for code, _ in role_choices(_account(1, "admin"))]
    assert labels == ["operator", "user"]
    assert [code for code, _ in role_choices(_account(1, "user"))] == []
    # Highest first, so the default scroll position is the least surprising.
    assert [code for code, _ in role_choices(_account(1, "superuser"))] == ["admin", "operator", "user"]


def test_picker_field_reads_the_bound_role_codename():
    from wtforms import Form

    class _Form(Form):
        role = RoleChoiceField(choices=[("operator", "Оператор"), ("user", "Пользователь")])

    bound = _Form(obj=SimpleNamespace(role=SimpleNamespace(codename="operator"))).role
    assert bound.data == "operator"
    assert [value for value, *_ in bound.iter_choices()] == ["operator", "user"]
