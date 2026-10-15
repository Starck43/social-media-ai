"""Initialize least-privileged platform defaults without wildcard grants.

Operator permissions are assigned explicitly by a platform administrator.
This setup command only creates empty defaults when none exist.
"""

import asyncio

from app.core.tenant_context import tenant_scope
from app.services.user.permissions import RolePermissionService


async def assign_roles_permissions() -> None:
    await RolePermissionService.assign_default_permissions()


if __name__ == "__main__":
    with tenant_scope(bypass=True):
        asyncio.run(assign_roles_permissions())
