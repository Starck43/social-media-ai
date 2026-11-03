"""Local-only validation: real manager-backed identities in isolated test_schema."""

import pytest

from app.core.permissions import operator_permission_scope
from app.core.tenant_context import tenant_scope
from app.models import TenantUser

pytest_plugins = ("tests.shared.business_tenancy", "tests.shared.fixtures.personal_workspace")
pytestmark = [pytest.mark.asyncio, pytest.mark.tenancy]


async def test_business_db_world_exposes_two_persisted_tenants_and_actors(business_db_world):
    world = business_db_world
    assert len({tenant.id for tenant in world.tenants}) == 2
    assert world.users[1].id != world.users[2].id
    first = next(m for m in world.memberships if m.user_id == world.users[1].id)
    second = next(m for m in world.memberships if m.user_id == world.users[2].id)
    assert first.tenant_id != second.tenant_id
    with tenant_scope(bypass=True), operator_permission_scope():
        first_db = await TenantUser.objects.get(id=first.id)
        second_db = await TenantUser.objects.get(id=second.id)
    assert first_db.tenant_id == first.tenant_id
    assert second_db.tenant_id == second.tenant_id
