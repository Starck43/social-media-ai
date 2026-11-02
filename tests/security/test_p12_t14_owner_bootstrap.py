"""Bootstrap must not silently restore revoked access or grant a new role.

Exercises the real resolver and PostgreSQL transaction; all IDs and tenants are
synthetic. No transport, external provider or production database is used.
"""

import asyncio
from uuid import uuid4

import pytest

from app.agent.identity import resolve_runtime_identity
from app.channels.base import Inbound
from app.core.config import settings
from app.core.tenant_context import tenant_scope
from app.models import Role, Tenant, User, WorkspaceRole
from app.models.managers.tenant_manager import tenant_channels, tenant_users
from app.services.tenancy.resolver import resolve_inbound
from app.types import PlatformRoleType

pytestmark = pytest.mark.tenancy


@pytest.fixture
async def scenario(monkeypatch):
    slug = f"p12t14-{uuid4().hex}"
    identifier = str(uuid4().int % (10**15))
    with tenant_scope(bypass=True):
        tenant = await Tenant.objects.create(slug=slug, name="Bootstrap test", plan="business")
        foreign = await Tenant.objects.create(slug=f"{slug}-foreign", name="Foreign test", plan="business")
        owner = await WorkspaceRole.objects.get(codename="owner")
        viewer = await WorkspaceRole.objects.get(codename="viewer")
    monkeypatch.setattr(settings, "DEFAULT_TENANT_SLUG", slug)
    monkeypatch.setattr(settings, "TELEGRAM_OWNER_IDS", identifier)
    monkeypatch.setattr(settings, "MAX_OWNER_ID", identifier)
    data = {"tenant": tenant, "foreign": foreign, "owner": owner, "viewer": viewer, "id": identifier}
    try:
        yield data
    finally:
        with tenant_scope(bypass=True):
            await Tenant.objects.delete_by_id(foreign.id)
            await Tenant.objects.delete_by_id(tenant.id)


def inbound(data, *, channel="telegram", chat="new"):
    return Inbound(channel=channel, chat_id=chat, user_id=data["id"], text="hello")


@pytest.mark.parametrize("channel", ["telegram", "max"])
async def test_first_contact_is_atomic_but_not_runtime_identity(scenario, channel):
    result = await resolve_inbound(inbound(scenario, channel=channel))
    assert result and result.onboarded and result.role_codename == "owner"
    with tenant_scope(bypass=True):
        member = await tenant_users.get_member(
            tenant_id=scenario["tenant"].id, channel=channel, external_user_id=scenario["id"]
        )
        binding = await tenant_channels.get_binding(channel=channel, chat_id="new")
    assert member.role_id == scenario["owner"].id and member.user_id is None
    assert binding.tenant_id == scenario["tenant"].id
    with tenant_scope(scenario["tenant"].id):
        assert await resolve_runtime_identity(result) is None


@pytest.mark.parametrize("active,role", [(False, "owner"), (True, "viewer"), (False, "viewer")])
async def test_existing_membership_not_reactivated_or_promoted(scenario, active, role):
    tenant, uid = scenario["tenant"], scenario["id"]
    with tenant_scope(bypass=True):
        member = await tenant_users.add_member(
            tenant_id=tenant.id, channel="telegram", external_user_id=uid, role_id=scenario[role].id
        )
        await tenant_users.update_by_id(member.id, is_active=active)
    assert await resolve_inbound(inbound(scenario)) is None
    with tenant_scope(bypass=True):
        after = await tenant_users.get(id=member.id)
        assert (after.is_active, after.role_id) == (active, scenario[role].id)
        assert await tenant_channels.get(channel="telegram", chat_id="new") is None


async def test_revoked_binding_is_not_reactivated_and_no_member_created(scenario):
    with tenant_scope(bypass=True):
        binding = await tenant_channels.bind(tenant_id=scenario["tenant"].id, channel="telegram", chat_id="new")
        await tenant_channels.update_by_id(binding.id, is_active=False)
    assert await resolve_inbound(inbound(scenario)) is None
    with tenant_scope(bypass=True):
        assert not (await tenant_channels.get(id=binding.id)).is_active
        assert (
            await tenant_users.get(tenant_id=scenario["tenant"].id, channel="telegram", external_user_id=scenario["id"])
            is None
        )


async def test_foreign_binding_is_not_moved_and_no_member_created(scenario):
    with tenant_scope(bypass=True):
        binding = await tenant_channels.bind(tenant_id=scenario["foreign"].id, channel="telegram", chat_id="new")
    assert await resolve_inbound(inbound(scenario)) is None
    with tenant_scope(bypass=True):
        assert (await tenant_channels.get(id=binding.id)).tenant_id == scenario["foreign"].id
        assert (
            await tenant_users.get(tenant_id=scenario["tenant"].id, channel="telegram", external_user_id=scenario["id"])
            is None
        )


async def test_existing_owner_can_bind_new_chat_but_not_revoked_member(scenario):
    tenant, uid = scenario["tenant"], scenario["id"]
    first = await resolve_inbound(inbound(scenario, chat="one"))
    second = await resolve_inbound(inbound(scenario, chat="two"))
    assert first and second and first.tenant_id == second.tenant_id == tenant.id
    with tenant_scope(bypass=True):
        member = await tenant_users.get_member(tenant_id=tenant.id, channel="telegram", external_user_id=uid)
        await tenant_users.update_by_id(member.id, is_active=False)
    assert await resolve_inbound(inbound(scenario, chat="one")) is None
    assert await resolve_inbound(inbound(scenario, chat="three")) is None
    with tenant_scope(bypass=True):
        assert not (await tenant_users.get(id=member.id)).is_active
        assert await tenant_channels.get(channel="telegram", chat_id="three") is None


async def test_env_removal_does_not_revoke_existing_membership(scenario, monkeypatch):
    first = await resolve_inbound(inbound(scenario, chat="one"))
    assert first
    monkeypatch.setattr(settings, "TELEGRAM_OWNER_IDS", "")
    assert await resolve_inbound(inbound(scenario, chat="one"))
    assert await resolve_inbound(inbound(scenario, chat="two")) is None


async def test_parallel_first_contact_does_not_duplicate_or_promote(scenario):
    results = await asyncio.gather(*(resolve_inbound(inbound(scenario)) for _ in range(8)))
    assert all(result and result.tenant_id == scenario["tenant"].id for result in results)
    with tenant_scope(bypass=True):
        members = await tenant_users.filter(
            tenant_id=scenario["tenant"].id, channel="telegram", external_user_id=scenario["id"]
        )
        bindings = await tenant_channels.filter(channel="telegram", chat_id="new")
    assert len(members) == len(bindings) == 1
    assert members[0].role_id == scenario["owner"].id


async def test_linked_user_loses_runtime_access_after_membership_revocation(scenario):
    result = await resolve_inbound(inbound(scenario))
    role = await Role.objects.get_by_codename(PlatformRoleType.USER)
    name = f"p12t14-{uuid4().hex}"
    user = await User.objects.create_user(
        username=name, email=f"{name}@example.com", password="test-only-password", role_id=role.id
    )
    try:
        with tenant_scope(bypass=True):
            member = await tenant_users.get_member(
                tenant_id=scenario["tenant"].id, channel="telegram", external_user_id=scenario["id"]
            )
            await tenant_users.update_by_id(member.id, user_id=user.id)
        with tenant_scope(scenario["tenant"].id):
            assert (await resolve_runtime_identity(result)).is_owner
        with tenant_scope(bypass=True):
            await tenant_users.update_by_id(member.id, is_active=False)
        assert await resolve_inbound(inbound(scenario)) is None
        with tenant_scope(scenario["tenant"].id):
            assert await resolve_runtime_identity(result) is None
    finally:
        with tenant_scope(bypass=True):
            await User.objects.delete_user(user.id)


async def test_concurrent_demotion_is_rechecked_after_row_lock(scenario):
    from sqlalchemy import select

    from app.core.database import async_session_maker
    from app.models import TenantUser

    with tenant_scope(bypass=True):
        member = await tenant_users.add_member(
            tenant_id=scenario["tenant"].id,
            channel="telegram",
            external_user_id=scenario["id"],
            role_id=scenario["owner"].id,
        )
        async with async_session_maker() as session:
            async with session.begin():
                locked = (
                    await session.execute(select(TenantUser).where(TenantUser.id == member.id).with_for_update())
                ).scalar_one()
                task = asyncio.create_task(resolve_inbound(inbound(scenario)))
                try:
                    await asyncio.sleep(0.15)
                    assert not task.done(), "bootstrap did not wait for the competing member change"
                    locked.role_id = scenario["viewer"].id
                except BaseException:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    raise
            assert await asyncio.wait_for(task, 5) is None
        assert (await tenant_users.get(id=member.id)).role_id == scenario["viewer"].id
        assert await tenant_channels.get(channel="telegram", chat_id="new") is None


async def test_inactive_workspace_never_receives_bootstrap(scenario):
    with tenant_scope(bypass=True):
        await Tenant.objects.update_by_id(scenario["tenant"].id, is_active=False)
    assert await resolve_inbound(inbound(scenario)) is None
    with tenant_scope(bypass=True):
        assert (
            await tenant_users.get(tenant_id=scenario["tenant"].id, channel="telegram", external_user_id=scenario["id"])
            is None
        )
        assert await tenant_channels.get(channel="telegram", chat_id="new") is None


async def test_quota_failure_creates_neither_member_nor_binding(scenario, monkeypatch):
    from unittest.mock import AsyncMock

    from app.models.managers import tenant_manager

    monkeypatch.setattr(tenant_manager, "_channel_limit_reason", AsyncMock(return_value="test quota"))
    with pytest.raises(tenant_manager.PlanLimitError):
        await resolve_inbound(inbound(scenario))
    with tenant_scope(bypass=True):
        assert (
            await tenant_users.get(tenant_id=scenario["tenant"].id, channel="telegram", external_user_id=scenario["id"])
            is None
        )
        assert await tenant_channels.get(channel="telegram", chat_id="new") is None
