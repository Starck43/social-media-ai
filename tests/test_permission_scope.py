"""Centralized permission scope: decorator behavior, bypass, isolation.

`app/core/permissions.py` is the non-HTTP twin of the API/web/console
checks: same question ("may this caller do this to this model?"), same
predicate (`User.has_perm_for()`), but the caller travels in a `ContextVar`
instead of a request. These tests pin the contract Phase 4-7 will build on:
decorator allow/deny, explicit bypass, anonymous denial,
message format, scope nesting/restore, and string action resolution.
"""

from types import SimpleNamespace

import pytest

from app.core.permissions import (
    PermissionDeniedError,
    get_current_user,
    has_permission,
    has_permission_by_codename,
    has_role,
    permission_scope,
    require_permission,
    require_role,
)
from app.core.tenant_context import tenant_scope
from app.models import Role, User
from app.types import ActionType, UserRoleType


pytestmark = pytest.mark.tenancy  # no implicit operator authority in rights tests

async def _role(codename: str) -> Role:
    return await Role.objects.get(codename=UserRoleType[codename].name)


async def _user(role: Role, *, superuser: bool = False) -> User:
    import secrets

    username = f"scope{secrets.token_hex(4)}"
    return await User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="secret-password-1",
        role_id=role.id,
        is_superuser=superuser,
    )


async def _loaded(user: User) -> User:
    return await User.objects.prefetch_related("role.permissions").get(id=user.id)


def _detached_like(user: User) -> User:
    """A user whose role walk raises, like an ORM row with a closed session."""
    broken = SimpleNamespace(
        _is_superuser_role=user._is_superuser_role,
        has_perm_for=lambda *a: (_ for _ in ()).throw(RuntimeError("detached")),
        is_superuser=user.is_superuser,
        role=None,
    )
    return broken


# --- scope ------------------------------------------------------------


def test_scope_sets_restores_and_nests():
    sentinel = object()
    assert get_current_user() is None
    with permission_scope(sentinel):
        assert get_current_user() is sentinel
        with permission_scope(None):
            assert get_current_user() is None
        assert get_current_user() is sentinel
    assert get_current_user() is None


def test_scope_restores_on_exception():
    sentinel = object()
    with pytest.raises(RuntimeError), permission_scope(sentinel):
        raise RuntimeError("boom")
    assert get_current_user() is None


# --- has_permission ----------------------------------------------------


async def test_has_permission_delegates_to_model_predicate():
    viewer = await _loaded(await _user(await _role("VIEWER")))
    assert has_permission(viewer, "source", ActionType.VIEW) is True
    assert has_permission(viewer, "source", ActionType.DELETE) is False


async def test_has_permission_accepts_string_actions():
    viewer = await _loaded(await _user(await _role("VIEWER")))
    assert has_permission(viewer, "source", "view") is True
    assert has_permission(viewer, "source", "UPDATE") is False
    assert has_permission(viewer, "source", "no-such-action") is False


async def test_has_permission_never_raises():
    viewer = await _user(await _role("VIEWER"))
    assert has_permission(_detached_like(viewer), "source", ActionType.VIEW) is False


def test_has_permission_denies_none_user():
    """No identity is not an operator or an implicit worker grant."""
    with tenant_scope(1), permission_scope(None, is_owner=True):
        assert has_permission(None, "source", ActionType.DELETE) is False


def test_has_permission_passes_platform_bypass():
    """CLI/worker/admin console run under `tenant_scope(bypass=True)`."""
    sentinel = object()
    with tenant_scope(bypass=True), permission_scope(sentinel):
        assert has_permission(sentinel, "source", ActionType.DELETE) is True


async def test_has_role_follows_platform_ladder_and_flag():
    admin = await _loaded(await _user(await _role("ADMIN")))
    viewer = await _loaded(await _user(await _role("VIEWER")))
    assert has_role(admin, UserRoleType.ADMIN) is True
    assert has_role(viewer, UserRoleType.ADMIN) is False
    assert has_role(viewer, "viewer") is True
    developer = await _loaded(await _user(await _role("VIEWER"), superuser=True))
    assert has_role(developer, UserRoleType.ADMIN) is True


def test_has_role_passes_explicit_bypass_but_denies_none_user():
    with tenant_scope(bypass=True):
        assert has_role(object(), UserRoleType.ADMIN) is True
    assert has_role(None, UserRoleType.ADMIN) is False


# --- decorators --------------------------------------------------------


async def test_require_permission_allows_and_denies_with_api_message():
    viewer = await _loaded(await _user(await _role("VIEWER")))

    @require_permission("source", ActionType.VIEW)
    async def read():
        return "ok"

    @require_permission("source", ActionType.DELETE)
    async def wipe():
        return "ok"  # pragma: no cover - denial raises first

    with permission_scope(viewer):
        assert await read() == "ok"
        with pytest.raises(PermissionDeniedError) as refusal:
            await wipe()
    assert str(refusal.value) == "Missing permission: source.delete"


async def test_require_permission_sync_callable_and_explicit_user():
    viewer = await _loaded(await _user(await _role("VIEWER")))

    @require_permission("source", ActionType.DELETE)
    def wipe(*, current_user=None):
        return "ok"  # pragma: no cover - denial raises first

    with pytest.raises(PermissionDeniedError):
        wipe(current_user=viewer)
    with tenant_scope(bypass=True):
        assert wipe() == "ok"


async def test_require_role_gates_platform_role():
    admin = await _loaded(await _user(await _role("ADMIN")))
    viewer = await _loaded(await _user(await _role("VIEWER")))

    @require_role(UserRoleType.ADMIN)
    async def op(*, current_user=None):
        return "ok"

    assert await op(current_user=admin) == "ok"
    with pytest.raises(PermissionDeniedError):
        await op(current_user=viewer)


def test_decorators_reject_unknown_action_and_role():
    with pytest.raises(ValueError):
        require_permission("source", "no-such-action")

    with pytest.raises(ValueError):
        require_role("no-such-role")


# --- has_permission_by_codename ---------------------------------------------


async def test_has_permission_by_codename_delegates_to_model_predicate():
    viewer = await _loaded(await _user(await _role("VIEWER")))
    assert has_permission_by_codename(viewer, "source.view") is True
    assert has_permission_by_codename(viewer, "source.delete") is False


async def test_has_permission_by_codename_parses_action_string():
    viewer = await _loaded(await _user(await _role("VIEWER")))
    assert has_permission_by_codename(viewer, "source.view") is True
    assert has_permission_by_codename(viewer, "source.UPDATE") is False


async def test_has_permission_by_codename_never_raises():
    viewer = await _user(await _role("VIEWER"))
    assert has_permission_by_codename(_detached_like(viewer), "source.view") is False


def test_has_permission_by_codename_denies_none_user():
    with tenant_scope(1), permission_scope(None, is_owner=True):
        assert has_permission_by_codename(None, "source.delete") is False


def test_has_permission_by_codename_passes_bypass():
    """CLI/worker/admin console run under `tenant_scope(bypass=True)`."""
    sentinel = object()
    with tenant_scope(bypass=True), permission_scope(sentinel):
        assert has_permission_by_codename(sentinel, "source.delete") is True


async def test_has_permission_by_codename_rejects_malformed():
    """A codename without a dot or None returns False, not an exception."""
    viewer = await _loaded(await _user(await _role("VIEWER")))
    with permission_scope(viewer):
        assert has_permission_by_codename(viewer, None) is False
        assert has_permission_by_codename(viewer, "nodothere") is False
        assert has_permission_by_codename(viewer, ".view") is False
        assert has_permission_by_codename(viewer, "source.unknownaction") is False
