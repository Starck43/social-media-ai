"""Seed the fixed platform role catalog through the async model manager."""

import asyncio

from app.core.tenant_context import tenant_scope
from app.models import PlatformRole, WorkspaceRole
from app.types import PlatformRoleType, WorkspaceRoleType

ROLE_DESCRIPTIONS = {
    PlatformRoleType.USER: "Registered account without access to the operator console.",
    PlatformRoleType.OPERATOR: "Platform console access limited to explicitly assigned permissions.",
    PlatformRoleType.ADMIN: "Full platform administration, including operator grants.",
}
WORKSPACE_ROLE_DESCRIPTIONS = {
    WorkspaceRoleType.OWNER: "Workspace owner with full access inside their workspace.",
    WorkspaceRoleType.ADMIN: "Workspace administrator with full access inside their workspace.",
    WorkspaceRoleType.MANAGER: "Workspace manager for sources, scenarios and tasks.",
    WorkspaceRoleType.ANALYST: "Workspace analyst for viewing and analyzing content.",
    WorkspaceRoleType.VIEWER: "Read-only access to workspace content.",
}


async def _seed_roles() -> tuple[int, int]:
    created = 0
    existing = 0
    for role_type in PlatformRoleType:
        role = await PlatformRole.objects.get_by_codename(role_type)
        if role is not None:
            existing += 1
            continue
        await PlatformRole.objects.create(
            codename=role_type.value,
            description=ROLE_DESCRIPTIONS[role_type],
        )
        created += 1
    return created, existing


async def _seed_workspace_roles() -> tuple[int, int]:
    created = 0
    existing = 0
    for role_type in WorkspaceRoleType:
        role = await WorkspaceRole.objects.filter(codename=role_type.value).first()
        if role is not None:
            existing += 1
            continue
        await WorkspaceRole.objects.create(
            codename=role_type.value,
            description=WORKSPACE_ROLE_DESCRIPTIONS[role_type],
        )
        created += 1
    return created, existing


async def _seed_role_catalogs() -> tuple[tuple[int, int], tuple[int, int]]:
    return await _seed_roles(), await _seed_workspace_roles()


async def seed_roles_async() -> None:
    with tenant_scope(bypass=True):
        (platform_created, platform_existing), (workspace_created, workspace_existing) = await _seed_role_catalogs()
    print(f"Platform roles: {platform_created} created, {platform_existing} already present")
    print(f"Workspace roles: {workspace_created} created, {workspace_existing} already present")


def seed_roles() -> None:
    asyncio.run(seed_roles_async())


if __name__ == "__main__":
    seed_roles()
