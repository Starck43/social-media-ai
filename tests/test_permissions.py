"""The one permission system: what a role grants, and how a request asks for it.

The project used to carry five parallel answers to "may this user do this" — the
canonical model rights, a legacy `User.has_perm` codename check, a Django-style
permission-class framework in `app/core/permissions.py`, a `role_required`
decorator and three `require_*` service helpers. Only the first one worked: the
others compared `ActionType.value`, which for these enums is a
`(db_value, display, emoji)` tuple, so `codename.endswith(f".{value}")` could
never match a real codename, and `ActionType[full_codename]` was a guaranteed
`KeyError`. The dead four are gone; these tests pin the one that stayed, plus a
guard so a merge cannot quietly bring the duplicates back.

Nothing here needs a browser or sqladmin: `User.model_permissions()` is the
predicate, the two `app/api/deps.py` factories are plain async callables, and
the HTTP layers are only a delivery mechanism for them.
"""

import importlib.util
from contextlib import asynccontextmanager
from typing import AsyncIterator

import pytest
from fastapi import HTTPException

from app.api.deps import require_model_perm, require_platform_role
from app.models import Role, User
from app.types import ActionType, UserRoleType

UNKNOWN_MODEL_RIGHT = "Missing permission: source.delete"


async def _role(codename: str) -> Role:
    return await Role.objects.get(codename=UserRoleType[codename].name)


@asynccontextmanager
async def _role_with(role: Role, keep) -> AsyncIterator[Role]:
    """Give `role` exactly the permissions `keep(permission)` selects.

    The seeded matrix hands every platform role a bit of everything, so the
    cases that tell one right from another have to be built. The original links
    are restored in a `finally`: leaving a role stripped would break every test
    that runs after this one.
    """
    loaded = await Role.objects.get_with_permissions(role.id)
    original = [permission.id for permission in loaded.permissions]
    await Role.objects.set_permissions(role.id, [p.id for p in loaded.permissions if keep(p)])
    try:
        yield role
    finally:
        await Role.objects.set_permissions(role.id, original)


def _on_model(model_name: str):
    def keep(permission) -> bool:
        return permission.model_type is not None and permission.model_type.model_name == model_name

    return keep


async def _user(role: Role, *, superuser: bool = False) -> User:
    import secrets

    username = f"perms{secrets.token_hex(4)}"
    return await User.objects.create_user(
        username=username,
        email=f"{username}@example.test",
        password="secret-password-1",
        role_id=role.id,
        is_superuser=superuser,
    )


async def _loaded(user: User) -> User:
    """The user as every surface sees them: role and permissions eager-loaded."""
    return await User.objects.prefetch_related("role.permissions").get(id=user.id)


# --- the predicate ---------------------------------------------------------


async def test_model_permissions_cover_one_model_at_a_time():
    loaded = await _loaded(await _user(await _role("VIEWER")))

    assert ActionType.VIEW in loaded.model_permissions("source")
    assert ActionType.UPDATE not in loaded.model_permissions("source")
    # A right on another model is not a right on this one.
    assert loaded.model_permissions("permission") == set()


async def test_has_perm_for_is_case_insensitive_and_model_scoped():
    loaded = await _loaded(await _user(await _role("VIEWER")))

    # The console passes the lowercased class name, the API a table name.
    assert loaded.has_perm_for("Source", ActionType.VIEW)
    assert loaded.has_perm_for("source", ActionType.VIEW)
    assert not loaded.has_perm_for("source", ActionType.DELETE)
    # The same action on a model the role never mentions.
    assert not loaded.has_perm_for("permission", ActionType.VIEW)


async def test_a_superuser_role_grants_every_action_on_every_model():
    loaded = await _loaded(await _user(await _role("SUPERUSER")))

    assert loaded.model_permissions("source") == set(ActionType)
    assert loaded.has_perm_for("model-that-does-not-exist", ActionType.MODERATE)
    assert loaded.has_admin_access()


async def test_a_stripped_role_grants_nothing():
    """The matrix is the only source: no permission rows, no access."""
    role = await _role("VIEWER")
    async with _role_with(role, lambda permission: False):
        stripped = await _loaded(await _user(role))

    assert stripped.model_permissions("source") == set()
    assert not stripped.has_admin_access()
    assert stripped.has_perm_for("source", ActionType.VIEW) is False


async def test_the_is_superuser_flag_needs_no_permission_rows():
    """The developer flag is checked before the role's rights, so it survives an
    unseeded (or emptied) matrix — otherwise a broken seed locks the owner out."""
    role = await _role("VIEWER")
    async with _role_with(role, lambda permission: False):
        developer = await _loaded(await _user(role, superuser=True))

    assert developer.model_permissions("source") == set(ActionType)
    assert developer.has_perm_for("source", ActionType.DELETE)
    assert developer.has_admin_access()


# --- the dependencies ------------------------------------------------------


async def test_require_model_perm_passes_and_returns_a_user_safe_to_use():
    """The returned user must not be the detached one `get_authenticated_user`
    hands over: touching `.role` on it would raise DetachedInstanceError."""
    viewer = await _user(await _role("VIEWER"))

    granted = await require_model_perm("source", ActionType.VIEW)(viewer)

    assert granted.id == viewer.id
    assert granted.role is not None
    assert granted.role.permissions  # the same trap, one relation deeper


async def test_require_model_perm_refuses_a_right_the_role_does_not_have():
    viewer = await _user(await _role("VIEWER"))

    with pytest.raises(HTTPException) as refusal:
        await require_model_perm("source", ActionType.DELETE)(viewer)

    assert refusal.value.status_code == 403
    assert refusal.value.detail == UNKNOWN_MODEL_RIGHT


async def test_require_model_perm_refuses_a_model_the_role_never_mentions():
    """Having `source.view` is not having `permission.view`."""
    viewer = await _user(await _role("VIEWER"))

    with pytest.raises(HTTPException) as refusal:
        await require_model_perm("permission", ActionType.VIEW)(viewer)

    assert refusal.value.status_code == 403


async def test_require_platform_role_follows_the_ladder_and_the_flag():
    admin = await require_platform_role(UserRoleType.ADMIN)(await _user(await _role("ADMIN")))
    assert admin.role is not None

    with pytest.raises(HTTPException) as refusal:
        await require_platform_role(UserRoleType.ADMIN)(await _user(await _role("VIEWER")))
    assert refusal.value.status_code == 403

    developer = await require_platform_role(UserRoleType.ADMIN)(await _user(await _role("VIEWER"), superuser=True))
    assert developer.id


# --- the surfaces share one predicate --------------------------------------


async def test_the_console_grants_exactly_what_the_predicate_says():
    """The console reads `model_permissions()` once and turns it into grants; the
    API asks per (model, action). If those two ever disagree, one surface is
    answering a different question than the other."""
    from types import SimpleNamespace

    from sqladmin.authorization import Action

    from app.admin.authorization import AdminAuthorizationBackend
    from app.admin.views import SourceAdmin

    loaded = await _loaded(await _user(await _role("VIEWER")))
    views = [SourceAdmin()]
    backend = AdminAuthorizationBackend()
    backend.setup(SimpleNamespace(_views=views))
    request = SimpleNamespace(
        state=SimpleNamespace(admin_user=loaded),
        session={},
        app=SimpleNamespace(state=SimpleNamespace(admin=SimpleNamespace(_views=views))),
    )
    await backend.load(request)

    for action, right in ((Action.LIST, ActionType.VIEW), (Action.DELETE, ActionType.DELETE)):
        assert backend.has_permission(request, "source", action) is loaded.has_perm_for("source", right), action

    # ...and the concrete expectation, so the two cannot drift together.
    assert backend.has_permission(request, "source", Action.LIST) is True
    assert backend.has_permission(request, "source", Action.DELETE) is False


# --- the duplicates stay gone ----------------------------------------------


def test_the_parallel_permission_systems_are_not_reintroduced():
    """Each of these was a second answer to the same question, and three were
    broken by construction: they compared `ActionType.value`, which for these
    enums is the `(db_value, display, emoji)` tuple, so the codename suffix match
    could never succeed. A merge that brings one back must fail here."""
    from app.api import deps
    from app.core import decorators
    from app.services.user import permissions as role_permissions

    assert not hasattr(User, "has_perm")
    assert not hasattr(User, "has_role")
    assert not hasattr(decorators, "role_required")
    assert not hasattr(deps, "require_workspace_role")
    assert not hasattr(deps, "require_workspace_owner_or_perm")
    assert not hasattr(role_permissions, "require_permission")
    assert not hasattr(role_permissions, "require_any_permission")
    assert importlib.util.find_spec("app.core.permissions") is None
    assert importlib.util.find_spec("app.services.user.roles") is None
