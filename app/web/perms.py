"""Request-bound workspace capabilities, independent of platform role.

Active membership grants and the shared resource catalog gate both navigation
and handlers. Personal-resource handlers additionally constrain the actor ID.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from app.types import ActionType, PlatformRoleType

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models import TenantUser, User


def _resolve_action(action: ActionType | str) -> ActionType | None:
    """Accept an `ActionType` or its db value / name (`"update"`, `"UPDATE"`)."""
    if isinstance(action, ActionType):
        return action
    if not isinstance(action, str):
        return None
    token = action.strip().lower()
    if not token:
        return None
    return ActionType.get_by_value(token) or ActionType.get_by_name(action.strip().upper())


class WebPerms:
    """What the caller may do in the workspace this request runs for."""

    __slots__ = ("_user", "_memberships", "_tenant_id", "_member_grants")

    def __init__(
        self,
        user: "User | None" = None,
        memberships: Iterable["TenantUser"] | None = None,
        tenant_id: int | None = None,
        member_grants: frozenset | None = None,
    ) -> None:
        self._user = user
        self._memberships = list(memberships or [])
        self._tenant_id = tenant_id
        # `TenantUserManager.member_grants()` of the active membership; None = unresolved.
        self._member_grants = member_grants

    # --- who ---------------------------------------------------------------

    @property
    def user(self) -> "User | None":
        return self._user

    @property
    def is_superuser(self) -> bool:
        """Legacy template flag: global mode is never available in /app."""
        return False

    @property
    def is_superuser_role(self) -> bool:
        """Whether the caller holds a platform operator or admin platform role.

        Whole-section gates (`guard_superuser`) ask *who* the caller is, not
        what the active workspace grants: owning a workspace is never an
        operator role. Reads the platform role the request carries
        (`User.objects.prefetch_related("role.permissions")`); callers whose
        platform role is absent fail closed.
        """
        user = self._user
        if user is None or not getattr(user, "is_active", False):
            return False
        role = getattr(user, "role", None)
        codename = getattr(role, "codename", None)
        return codename in (PlatformRoleType.OPERATOR, PlatformRoleType.ADMIN)

    @property
    def workspace_role(self) -> str | None:
        """Codename of the workspace role in the active workspace, or None."""
        for membership in self._memberships:
            if membership.tenant_id == self._tenant_id:
                if membership.role is None:
                    return None
                codename = membership.role.codename
                name = str(codename)
                return name
        return None

    @property
    def is_owner(self) -> bool:
        for membership in self._memberships:
            if membership.tenant_id == self._tenant_id:
                return bool(self._membership() and membership.is_owner)
        return False

    @property
    def can_open_chat(self) -> bool:
        """Active members need the separate AI grant; owners have it by default."""
        member = self._membership()
        return bool(member and (member.is_owner or getattr(member, "ai_chat_enabled", False)))

    # --- what they may do --------------------------------------------------

    def can(self, model_name: str, action: ActionType | str) -> bool:
        from app.core.access_catalog import resource_for

        resource = resource_for(model_name)
        resolved = _resolve_action(action)
        user = self._user
        if (
            user is None
            or not getattr(user, "is_active", False)
            or self._tenant_id is None
            or resource is None
            or not resource.workspace
            or resolved not in resource.actions
        ):
            return False
        membership = self._membership()
        return bool(
            membership and self._member_grants is not None and (resource.model_name, resolved) in self._member_grants
        )

    def _membership(self):
        user = self._user
        if user is None or not getattr(user, "is_active", False):
            return None
        return next(
            (
                m
                for m in self._memberships
                if m.tenant_id == self._tenant_id
                and getattr(m, "user_id", None) == user.id
                and getattr(m, "channel", None) == "web"
                and getattr(m, "is_active", False)
                and getattr(m, "role", None) is not None
            ),
            None,
        )

    def can_manage_workspace(self, tenant_id: int | None) -> bool:
        if isinstance(tenant_id, bool) or not isinstance(tenant_id, int) or tenant_id != self._tenant_id:
            return False
        membership = self._membership()
        return bool(membership and membership.role.codename in ("owner", "admin"))

    def can_manage_member(self, target_role: str | None, new_role: str | None = None) -> bool:
        membership = self._membership()
        if membership is None:
            return False
        actor = membership.role.codename
        if actor == "owner":
            return True
        protected = {"owner", "admin", None}
        return actor == "admin" and target_role not in protected and (new_role is None or new_role not in protected)

    def can_any(self, model_name: str, *actions: ActionType | str) -> bool:
        """True when at least one of the actions is allowed (toolbar buttons)."""
        return any(self.can(model_name, action) for action in actions)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"WebPerms(role={self.workspace_role!r}, superuser={self.is_superuser})"
