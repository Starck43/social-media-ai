"""Pure active-membership boundaries shared by web navigation and chat entry."""

from __future__ import annotations

from typing import Any, Iterable


def owns_web_workspace(user: Any, memberships: Iterable[Any], tenant_id: int | None) -> bool:
    """Chat belongs to an active owner, never to a guest or a global-role override.

    Ownership follows the existing membership.is_owner contract (including
    legacy rows). It is not the creator's id or a new permission matrix.
    """
    if user is None or not getattr(user, "is_active", False) or getattr(user, "id", None) is None:
        return False
    if isinstance(tenant_id, bool) or not isinstance(tenant_id, int) or tenant_id <= 0:
        return False
    return any(
        getattr(member, "tenant_id", None) == tenant_id
        and getattr(member, "user_id", None) == user.id
        and getattr(member, "channel", None) == "web"
        and getattr(member, "is_active", False)
        and bool(getattr(member, "is_owner", False))
        for member in memberships
    )
