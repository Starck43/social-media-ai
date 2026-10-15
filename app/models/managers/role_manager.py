from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from .base_manager import BaseManager

if TYPE_CHECKING:
    from app.types import ActionType, PlatformRoleType

    from ..permission import Permission
    from ..role import Role


class RoleManager(BaseManager["Role"]):
    """
    Manager for Role model with permission management.

    Provides methods for:
    — Role CRUD operations
    — Permission assignment/removal
    — Role-based access control queries
    — Finding roles by a codename and permissions
    """

    def __init__(self, model_cls):
        super().__init__(model_cls)

    async def get_by_name(self, name: str) -> Optional["Role"]:
        """Compatibility lookup; role identity is its codename."""
        return await self.get_by_codename(name)

    async def get_by_name_with_permissions(self, name: str) -> Optional["Role"]:
        """
        Get role by name with its permissions preloaded.

        Endpoints serialise the role right after the lookup, when the session is
        already closed — without the prefetch that is a DetachedInstanceError.

        Args:
                name: Role name (case-insensitive)

        Returns:
                Role object with permissions loaded, or None
        """
        role = await self.get_by_codename(name)
        return await self.get_with_permissions(role.id) if role else None

    async def get_by_codename(self, codename: str | PlatformRoleType) -> Optional["Role"]:
        """
        Get role by a codename.

        Args:
                codename: Role codename (UserRole enum)

        Returns:
                Role object or None
        """
        from app.types import PlatformRoleType

        value = codename.value if isinstance(codename, PlatformRoleType) else str(codename)
        aliases = {"viewer": "user", "superuser": "admin"}
        return await self.filter(codename=aliases.get(value.strip().lower(), value.strip().lower())).first()

    async def get_with_permissions(self, role_id: int) -> Optional["Role"]:
        """
        Get role with prefetched permissions.

        Args:
                role_id: Role ID

        Returns:
                Role object with permissions loaded
        """
        return await self.filter(id=role_id).prefetch_related("permissions").first()

    async def get_with_users(self, role_id: int) -> Optional["Role"]:
        """
        Get role with prefetched users.

        Args:
                role_id: Role ID

        Returns:
                Role object with users loaded
        """
        return await self.filter(id=role_id).prefetch_related("users").first()

    async def create_role(self, *args, **kwargs) -> "Role":
        """Role catalog is fixed; roles may be added only by a schema release."""
        raise ValueError("Arbitrary platform role creation is disabled")

    async def set_permissions(self, role_id: int, permission_ids: list[int]) -> int:
        """Replace a role's permission links with exactly `permission_ids`.

        The association table has no model, so this is the single write path
        for it — the API, the CLI and the setup scripts all funnel through here.
        """
        from app.core.permissions import get_current_user
        from app.core.tenant_context import is_bypass

        from ..role import role_permission
        from ..user import User

        actor = get_current_user()
        if actor is None:
            if not is_bypass():
                raise PermissionError("Platform admin required")
        else:
            fresh = await User.objects.prefetch_related("role").get(id=actor.id, is_active=True)
            target = await self.get(id=role_id)
            if fresh is None or not fresh.is_platform_admin or target is None or target.codename != "operator":
                raise PermissionError("Only platform admins may configure operator grants")

        return await self._replace_secondary(role_permission, "role_id", role_id, "permission_id", permission_ids)

    async def add_permission(self, role_id: int, permission_id: int) -> Optional["Role"]:
        """
        Add a permission to a role, keeping the ones it already has.

        Rewrites `role_permission` through `set_permissions`. Appending to the ORM
        collection and relying on an "auto-commit through the relationship" never
        worked: `get_with_permissions` opens and closes its own session, so the
        role came back detached and the link was silently lost.

        Args:
                role_id: Role ID
                permission_id: Permission ID

        Returns:
                Updated Role object or None if role not found
        """
        role = await self.get_with_permissions(role_id)
        if not role:
            return None

        permission_ids = {p.id for p in role.permissions}
        if permission_id in permission_ids:
            return role

        await self.set_permissions(role_id, [*permission_ids, permission_id])
        return await self.get_with_permissions(role_id)

    async def remove_permission(self, role_id: int, permission_id: int) -> Optional["Role"]:
        """
        Remove a permission from a role.

        See `add_permission`: the association table is rewritten explicitly
        instead of mutating a detached collection.

        Args:
                role_id: Role ID
                permission_id: Permission ID

        Returns:
                Updated Role object or None if role not found
        """
        role = await self.get_with_permissions(role_id)
        if not role:
            return None

        kept = {p.id for p in role.permissions if p.id != permission_id}
        if len(kept) == len(role.permissions):
            return role

        await self.set_permissions(role_id, list(kept))
        return await self.get_with_permissions(role_id)

    async def get_roles_with_permission(self, permission_codename: str) -> list["Role"]:
        """
        Get all roles that have a specific permission.

        Args:
                permission_codename: Permission codename

        Returns:
                List of Role objects
        """
        from app.core.access_catalog import resolve_permission_key

        from ..model_type import ModelType
        from ..permission import Permission

        resolved = resolve_permission_key(permission_codename)
        if resolved is None:
            return []
        resource, action = resolved
        model_type = await BaseManager(ModelType).filter(resource_key=resource.key).first()
        permission = (
            await Permission.objects.filter(model_type_id=model_type.id, action_type=action).first()
            if model_type
            else None
        )
        if not permission:
            return []

        # Get all roles with prefetched permissions
        all_roles = await self.filter().prefetch_related("permissions")

        return [role for role in all_roles if permission in role.permissions]

    async def has_permission(self, role_id: int, permission_codename: str) -> bool:
        """
        Check if a role has a specific permission.

        Args:
                role_id: Role ID
                permission_codename: Permission codename

        Returns:
                True if role has the permission
        """
        role = await self.get_with_permissions(role_id)
        if not role:
            return False

        return any(perm.codename == permission_codename for perm in role.permissions)

    async def get_permissions_for_role(
        self, role_id: int, action_type: Optional["ActionType"] = None
    ) -> list["Permission"]:
        """
        Get all permissions for a role.

        Args:
                role_id: Role ID
                action_type: An optional filter by action type

        Returns:
                List of Permission objects
        """
        role = await self.get_with_permissions(role_id)
        if not role:
            return []

        permissions = role.permissions

        if action_type:
            permissions = [perm for perm in permissions if perm.action_type == action_type]

        return permissions

    async def get_stats(self) -> dict:
        """
        Get statistics about roles.

        Returns:
                Dict with role statistics
        """
        all_roles = await self.filter()

        stats = {"total": len(all_roles), "by_codename": {}}

        for role in all_roles:
            codename = role.codename
            stats["by_codename"][codename] = {"label": role.label, "description": role.description}

        return stats
