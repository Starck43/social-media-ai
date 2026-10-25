"""Seed only permissions declared by the stable resource catalog."""

import asyncio

from app.core.access_catalog import RESOURCES, resource_for
from app.core.tenant_context import tenant_scope
from app.models import ModelType, Permission
from app.models.managers.base_manager import BaseManager
from app.services.user.permissions import RolePermissionService


async def _seed_model_types() -> int:
    manager = BaseManager(ModelType)
    created = 0
    for resource in RESOURCES:
        existing = await manager.filter(resource_key=resource.key).first()
        if existing is not None:
            continue
        await manager.create(
            resource_key=resource.key,
            model_name=resource.model_name,
            table_name=resource.table_name,
        )
        created += 1
    return created


async def _seed_permissions() -> tuple[int, int]:
    model_types = await BaseManager(ModelType).all()
    created = 0
    skipped = 0
    for model_type in model_types:
        resource = resource_for(model_type.resource_key)
        if resource is None:
            skipped += 1
            continue
        before = len(await Permission.objects.get_for_model_type(model_type.id))
        permissions = await Permission.objects.bulk_create_for_model(
            app_name=resource.key.split(".", maxsplit=1)[0],
            model_name=resource.key,
            model_type_id=model_type.id,
            action_types=list(resource.actions),
        )
        created += max(0, len(permissions) - before)
    return created, skipped


def seed_permissions() -> None:
    with tenant_scope(bypass=True):
        model_types_created = asyncio.run(_seed_model_types())
        created, skipped = asyncio.run(_seed_permissions())
        asyncio.run(RolePermissionService.assign_default_permissions())
    print(
        f"Model types: {model_types_created} created; "
        f"permissions: {created} created; {skipped} rows outside the catalog"
    )


def assign_default_roles_permissions() -> None:
    with tenant_scope(bypass=True):
        asyncio.run(RolePermissionService.assign_default_permissions())


if __name__ == "__main__":
    seed_permissions()
