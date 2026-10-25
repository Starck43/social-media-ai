"""The rights layer of the machine API, on top of the authentication layer.

`ApiScopeMiddleware` already authenticates every `/api/*` request (bearer JWT)
and pins it to a workspace the caller is a member of. What is left to decide is
*what that caller may do*, and there are exactly two answers in this project —
both read from the database, neither from a codename:

- `require_platform_role` — the platform role (`users.role_id`). For
  operations that are not scoped to one workspace at all, e.g. rewriting what
  every account of a role may do.
- `require_model_perm` — the model rights attached to that platform role
  (`role_permission` → `permissions.model_type_id` + `action_type`), read
  through `User.model_permissions()`. This is the same predicate the sqladmin
  console and the workspace UI use, so all three surfaces answer alike.

Only platform operators/admins may pass operator-route checks. A platform
administrator passes platform permission checks; workspace roles never do.

Both are FastAPI dependencies, not decorators: a decorator cannot see what the
framework already resolved (`get_authenticated_user`), so it has to guess at the
call signature — and the previous `role_required` decorator did exactly that.

Note the detached-user trap: `get_authenticated_user` hands over a user whose
session is already closed, so a lazy `user.role` / `user.role.permissions`
access raises `DetachedInstanceError`. Each dependency re-reads the row with the
relations eager-loaded and returns *that* object, so the endpoint receives a
user whose role and permissions are safe to use.
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import Depends, HTTPException
from starlette import status

from app.core.permissions import has_permission
from app.models import User
from app.services.user.auth import get_authenticated_user
from app.types import ActionType, PlatformRoleType


async def _load_with_rights(user: User) -> Any | None:
    """Re-read the user with role + permissions loaded (session is closed)."""
    # Only the prefetch: a nested `selectinload` loads the role too, and pairing
    # it with `select_related("role")` puts two strategies on the same path.
    return await User.objects.prefetch_related("role.permissions").get(id=user.id)


def require_platform_role(min_role: PlatformRoleType) -> Callable[..., Any]:
    """Require an active account with the requested platform capability."""

    async def dependency(user: User = Depends(get_authenticated_user)) -> Any:
        loaded = await _load_with_rights(user)
        allowed = (
            loaded is not None
            and loaded.is_active
            and (
                loaded.has_admin_access()
                if min_role is PlatformRoleType.OPERATOR
                else (
                    loaded.is_platform_admin
                    if min_role is PlatformRoleType.ADMIN
                    else loaded.role is not None and loaded.role.codename == min_role.value
                )
            )
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires platform role {min_role.value}",
            )
        return loaded

    return dependency


def require_model_perm(model_name: str, action: ActionType) -> Callable[..., Any]:
    """Model rights from the caller's platform role.

    Checked against `permissions.model_type_id` + `action_type` rather than the
    stored codename: several seeded codenames are malformed
    (`social.AgentTask.('view', 'Просмотр', '👀')`), the structured columns are
    not.

    Adopt it per endpoint — the router-wide `API_AUTH` only proves *who* calls,
    never *what* they may do.
    """

    async def dependency(user: User = Depends(get_authenticated_user)) -> Any:
        loaded = await _load_with_rights(user)
        # The same policy drives API, web, managers and tools; it chooses active
        # membership rights for workspace resources and platform grants for
        # platform resources.
        from app.core.access_catalog import resource_for

        resource = resource_for(model_name)
        personal_credential = (
            resource is not None and resource.key == "identity.credential" and action in resource.actions
        )
        allowed = (
            loaded is not None
            and loaded.is_active
            and (personal_credential or has_permission(loaded, model_name, action))
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permission: {model_name}.{action.db_value}",
            )
        return loaded

    return dependency
