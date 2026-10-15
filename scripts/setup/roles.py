"""Seed the fixed platform role catalog through the async model manager."""

import asyncio

from app.core.tenant_context import tenant_scope
from app.models import PlatformRole
from app.types import PlatformRoleType

ROLE_DESCRIPTIONS = {
    PlatformRoleType.USER: "Registered account without access to the operator console.",
    PlatformRoleType.OPERATOR: "Platform console access limited to explicitly assigned permissions.",
    PlatformRoleType.ADMIN: "Full platform administration, including operator grants.",
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


def seed_roles() -> None:
    with tenant_scope(bypass=True):
        created, existing = asyncio.run(_seed_roles())
    print(f"Platform roles: {created} created, {existing} already present")


if __name__ == "__main__":
    seed_roles()
