"""Platform role grant management using registered resource/action keys."""

import logging

from app.core.access_catalog import resolve_permission_key
from app.models import Permission, PlatformRole
from app.models.managers.role_manager import RoleManager
from app.types import PlatformRoleType

logger = logging.getLogger(__name__)


class RolePermissionService:
    """Maintain grants for the configurable platform operator role."""

    @staticmethod
    async def _set_role_permissions(role_id: int, permission_ids: list[int]) -> None:
        await RoleManager(PlatformRole).set_permissions(role_id, permission_ids)

    @staticmethod
    async def expand_permission_patterns(keys: list[str]) -> list[str]:
        """Resolve exact registered keys; wildcards are deliberately unsupported."""
        result = []
        for key in keys:
            resolved = resolve_permission_key(key)
            if resolved is None:
                raise ValueError(f"Unknown permission: {key}")
            resource, action = resolved
            if action not in resource.actions:
                raise ValueError(f"Action is not registered for {resource.key}: {action.db_value}")
            result.append(f"{resource.key}.{action.db_value}")
        return list(dict.fromkeys(result))

    @staticmethod
    async def get_role_permissions(role_id: int) -> list[Permission]:
        role = await PlatformRole.objects.get_with_permissions(role_id)
        return list(role.permissions) if role else []

    @staticmethod
    async def get_available_permissions() -> list[Permission]:
        return list(await Permission.objects.order_by(Permission.id).all())

    @staticmethod
    def _get_permission_groups(codenames: list[str]) -> dict[str, dict[str, str]]:
        groups: dict[str, dict[str, str]] = {}
        for codename in codenames:
            resolved = resolve_permission_key(codename)
            if resolved is None:
                continue
            resource, action = resolved
            groups.setdefault(resource.key, {})[action.db_value] = f"{resource.key}.{action.db_value}"
        return groups

    @classmethod
    def _get_updated_permissions(
        cls, current_codenames: list[str], new_codenames: list[str]
    ) -> tuple[list[str], list[str]]:
        current = {key for group in cls._get_permission_groups(current_codenames).values() for key in group.values()}
        new = {key for group in cls._get_permission_groups(new_codenames).values() for key in group.values()}
        return sorted(current & new), sorted(new - current)

    @classmethod
    async def update_role_permissions(
        cls,
        role_codename: str,
        permission_codenames: list[str],
        strategy: str = "replace",
    ) -> dict[str, list[str]]:
        role_code = role_codename.strip().lower()
        if role_code != PlatformRoleType.OPERATOR.value:
            raise ValueError("Only operator grants are configurable")
        if strategy not in {"replace", "merge", "synchronize"}:
            raise ValueError(f"Unknown update strategy: {strategy}")

        role = await PlatformRole.objects.get_by_codename(role_code)
        if role is None:
            raise ValueError(f"Platform role '{role_code}' not found")
        role = await PlatformRole.objects.get_with_permissions(role.id)
        current = {permission.codename for permission in role.permissions}
        requested = set(await cls.expand_permission_patterns(permission_codenames))
        if strategy == "merge":
            desired = current | requested
        else:
            desired = requested

        current_rows = {permission.codename: permission for permission in role.permissions}
        by_codename: dict[str, Permission] = dict(current_rows)
        for codename in desired - set(by_codename):
            permission = await Permission.objects.get_by_codename(codename)
            if permission is None:
                raise ValueError(f"Permission is not seeded: {codename}")
            by_codename[codename] = permission

        kept_ids = [by_codename[codename].id for codename in sorted(desired)]
        await cls._set_role_permissions(role.id, kept_ids)
        return {
            "added": sorted(desired - current),
            "removed": sorted(current - desired),
            "updated": [],
            "unchanged": sorted(current & desired),
        }

    @staticmethod
    async def get_permissions_by_codenames(codenames: list[str]) -> list[Permission]:
        permissions = []
        for codename in await RolePermissionService.expand_permission_patterns(codenames):
            permission = await Permission.objects.get_by_codename(codename)
            if permission is not None:
                permissions.append(permission)
        return permissions

    @classmethod
    async def assign_default_permissions(cls) -> None:
        """Keep the ordinary user and operator defaults least-privileged."""
        for role_code in (PlatformRoleType.USER, PlatformRoleType.OPERATOR):
            role = await PlatformRole.objects.get_by_codename(role_code)
            if role is None:
                continue
            role = await PlatformRole.objects.get_with_permissions(role.id)
            if role.permissions:
                continue
            await cls._set_role_permissions(role.id, [])
            logger.info("Initialized empty platform grants for role=%s", role_code.value)
