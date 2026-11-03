"""Owned contact integration identities with deletion AND residue verification."""

import pytest

from app.core.permissions import operator_permission_scope
from app.core.tenant_context import tenant_scope
from app.models import Contact, ContactConsent, ContactIdentity, ContactMergeJournal, Tenant, User


@pytest.fixture
async def contact_world(personal_world):
    """Reuse the existing synthetic world, and verify our complete owned cleanup."""
    world = personal_world
    tenant_ids = [row.id for row in world.tenants]
    user_ids = [row.id for row in world.users]
    try:
        yield world
    finally:
        # Idempotent if a test exercised tenant offboarding itself. Never delete
        # by a broad name prefix: IDs belong only to this fixture instance.
        with tenant_scope(bypass=True), operator_permission_scope():
            for tenant_id in tenant_ids:
                await Tenant.objects.delete_by_id(tenant_id)
            for user_id in user_ids:
                await User.objects.delete_by_id(user_id)
            assert await Tenant.objects.filter(Tenant.id.in_(tenant_ids)).count() == 0
            assert await User.objects.filter(User.id.in_(user_ids)).count() == 0
            for tenant_id in tenant_ids:
                with tenant_scope(tenant_id):
                    for model in (Contact, ContactIdentity, ContactConsent, ContactMergeJournal):
                        assert await model.objects.filter(tenant_id=tenant_id).count() == 0
