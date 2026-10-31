"""Shared resolution of an operator identity from an admin session token."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models import User


async def resolve_operator_identity(
    token: str | None,
    *,
    token_type: str | None = None,
    require_admin_access: bool = False,
) -> User | None:
    """Resolve a token to a fresh user with role permissions loaded.

    The common admin-console gate is optional so scope and standalone endpoints
    can keep their own access and response policies.
    """
    from app.models import User
    from app.services.user.auth import get_authenticated_user

    if token_type is None:
        authenticated = await get_authenticated_user(token=token)
    else:
        authenticated = await get_authenticated_user(token=token, token_type=token_type)
    if authenticated is None:
        return None
    user = await User.objects.prefetch_related("role.permissions").get(id=authenticated.id)
    if user is None or (require_admin_access and not user.has_admin_access()):
        return None
    return user
