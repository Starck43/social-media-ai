"""Rights of the current web user in the active workspace (docs/design/ui.md §7).

One question — "may this caller do this to this model?" — answered exactly the
way the sqladmin console and the machine API answer it (`User.has_perm_for`,
structured `permissions.model_type_id` + `action_type`, never the stored
codename), with the one exception the product needs: whoever *owns* a workspace
may always configure it, whatever their platform role is. Superusers always
pass.

Built once per request by `TenantUIMiddleware` and handed to templates as
`perms` (to hide write affordances) and to route handlers through
`guard_web()` in `app/web/deps.py` (to reject the mutation itself — hiding a
button is not a check).

Requires a user whose `role.permissions` are eager-loaded; the middleware does
that (`User.objects.prefetch_related("role.permissions")`), so `can()` never
walks a closed session.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from app.types import ActionType

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models import TenantUser, User

# Workspace roles that may always configure their own workspace. `TenantUser.role`
# is a free string today (`owner` is the server default and what invite codes
# issue); anything not listed here is a plain member and needs platform rights.
OWNER_ROLES = frozenset({"owner"})


def _resolve_action(action: ActionType | str) -> ActionType | None:
    """Accept an `ActionType` or its db value / name (`"update"`, `"UPDATE"`)."""
    if isinstance(action, ActionType):
        return action
    token = (action or "").strip().lower()
    if not token:
        return None
    return ActionType.get_by_value(token) or ActionType.get_by_name(action.strip().upper())


class WebPerms:
    """What the caller may do in the workspace this request runs for."""

    __slots__ = ("_user", "_memberships", "_tenant_id")

    def __init__(
        self,
        user: "User | None" = None,
        memberships: Iterable["TenantUser"] | None = None,
        tenant_id: int | None = None,
    ) -> None:
        self._user = user
        self._memberships = list(memberships or [])
        self._tenant_id = tenant_id

    # --- who ---------------------------------------------------------------

    @property
    def user(self) -> "User | None":
        return self._user

    @property
    def is_superuser(self) -> bool:
        return bool(self._user is not None and self._user.is_superuser)

    @property
    def is_superuser_role(self) -> bool:
        """A platform role above `UserRoleType.ADMIN`: the operator's own
        `is_superuser` flag or the SUPERUSER role (`User._is_superuser_role`).

        Sections that expose infrastructure (the job queue) gate on this, not on
        `can()` — a workspace owner passes every model right they have anyway,
        and the queue is still not theirs to see. Fail-closed: no user, no answer.
        """
        return self._user is not None and bool(self._user._is_superuser_role())

    @property
    def workspace_role(self) -> str | None:
        """Membership role in the active workspace (`owner`, `member`, ...)."""
        for membership in self._memberships:
            if membership.tenant_id == self._tenant_id:
                return membership.role
        return None

    @property
    def is_owner(self) -> bool:
        return self.workspace_role in OWNER_ROLES

    # --- what they may do --------------------------------------------------

    def can(self, model_name: str, action: ActionType | str) -> bool:
        """True when the caller may perform `action` on `model_name`.

        `model_name` is the `model_types.model_name` value, lower-cased — the
        same string the sqladmin console and `app/api/deps.py` use (`source`,
        `agenttask`, `agentscenario`, `digestrun`, ...).
        """
        if self._user is None:
            return False
        if self._user.is_superuser or self.is_owner:
            return True
        resolved = _resolve_action(action)
        if resolved is None:
            return False
        try:
            return bool(self._user.has_perm_for(model_name, resolved))
        except Exception:  # noqa: BLE001 - a rights check must never 500 a page
            return False

    def can_any(self, model_name: str, *actions: ActionType | str) -> bool:
        """True when at least one of the actions is allowed (toolbar buttons)."""
        return any(self.can(model_name, action) for action in actions)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"WebPerms(role={self.workspace_role!r}, superuser={self.is_superuser})"
