"""Tests for `resolve_source_owner`: which user's personal token a source uses.

A source is workspace-scoped, but its L2 token is personal (`user_credentials`).
The owner is an explicit `Source.params["token_owner"]` or `Source.owner_user_id`
(the member who added it), each accepted only while that user is an active member
of the workspace. There is no fallback to the workspace owner: such a source
collects through the deployment credential only (L1). Runs against the real
database.
"""

import secrets
from types import SimpleNamespace

import pytest

from app.models import Role, User
from app.models.managers.tenant_manager import TenantUserManager, tenants
from app.services.social.owner import resolve_source_owner
from app.types import PlatformRoleType, WorkspaceRoleType


def _source(tenant_id, params=None, owner_user_id=None):
    return SimpleNamespace(id=1, tenant_id=tenant_id, params=params or {}, owner_user_id=owner_user_id)


@pytest.fixture
async def workspace():
    """A fresh workspace so membership is fully controlled, then removed."""
    tenant = await tenants.create(name="Owner test", slug=f"ownerws{secrets.token_hex(4)}")
    yield tenant
    await tenants.delete_by_id(tenant.id)


@pytest.fixture
async def make_user():
    """Create web users and delete them (with their memberships) afterwards."""
    created = []
    role = await Role.objects.get(codename=PlatformRoleType.USER.value)

    async def _make():
        username = f"srcowner{secrets.token_hex(4)}"
        user = await User.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="secret-password-1",
            role_id=role.id,
        )
        created.append(user)
        return user

    yield _make
    for user in created:
        await User.objects.delete_by_id(user.id)


async def _deactivate_web_membership(tenant_id: int, user_id: int) -> None:
    """Flip the membership flag directly: manager changes demand an actor session."""
    from app.models.tenancy import TenantUser

    rows = await TenantUser.objects.filter(tenant_id=tenant_id, user_id=user_id, channel="web")
    for row in rows:
        await TenantUser.objects.update_by_id(row.id, is_active=False)


async def test_no_members_returns_none(workspace):
    assert await resolve_source_owner(_source(workspace.id)) is None


async def test_ownerless_source_never_borrows_the_workspace_owner(workspace, make_user):
    """The removed default: an owner is a property of the creator, not of a role."""
    owner = await make_user()
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=owner.id, role="owner")
    member = await make_user()
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=member.id, role="member")

    assert await resolve_source_owner(_source(workspace.id)) is None


async def test_ownerless_source_never_borrows_the_newest_member(workspace, make_user):
    first = await make_user()
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=first.id, role="member")
    second = await make_user()
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=second.id, role="member")

    assert await resolve_source_owner(_source(workspace.id)) is None


async def test_recorded_owner_is_used(workspace, make_user):
    """`owner_user_id` records the member who added the source."""
    owner = await make_user()
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=owner.id, role="owner")
    member = await make_user()
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=member.id, role="member")

    assert await resolve_source_owner(_source(workspace.id, owner_user_id=member.id)) == member.id


async def test_workspace_owner_as_recorded_owner_is_honoured(workspace, make_user):
    """The owner may add a source themselves; ownership then follows the actor."""
    owner = await make_user()
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=owner.id, role="owner")

    assert await resolve_source_owner(_source(workspace.id, owner_user_id=owner.id)) == owner.id


async def test_removed_owner_falls_back_to_service_credential(workspace, make_user):
    gone = await make_user()
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=gone.id, role="member")
    successor = await make_user()
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=successor.id, role="owner")
    await _deactivate_web_membership(workspace.id, gone.id)

    assert await resolve_source_owner(_source(workspace.id, owner_user_id=gone.id)) is None


async def test_deactivated_membership_falls_back_to_service_credential(workspace, make_user):
    gone = await make_user()
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=gone.id, role="member")
    await _deactivate_web_membership(workspace.id, gone.id)

    assert await resolve_source_owner(_source(workspace.id, owner_user_id=gone.id)) is None


async def test_explicit_token_owner_member_is_used(workspace, make_user):
    owner = await make_user()
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=owner.id, role="owner")
    chosen = await make_user()
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=chosen.id, role="member")

    assert await resolve_source_owner(_source(workspace.id, {"token_owner": chosen.id})) == chosen.id


async def test_explicit_non_member_is_ignored(workspace, make_user):
    owner = await make_user()
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=owner.id, role="owner")
    stranger = await make_user()  # never added to the workspace

    assert await resolve_source_owner(_source(workspace.id, {"token_owner": stranger.id})) is None


async def test_explicit_token_owner_overrides_recorded_owner(workspace, make_user):
    """The operator's pin wins over the recorded creator, as before."""
    creator = await make_user()
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=creator.id, role="member")
    pinned = await make_user()
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=pinned.id, role="member")

    source = _source(workspace.id, {"token_owner": pinned.id}, owner_user_id=creator.id)
    assert await resolve_source_owner(source) == pinned.id


async def test_forged_token_owner_does_not_fall_back_to_recorded_owner(workspace, make_user):
    """A pin naming a stranger is refused outright, never silently swapped."""
    creator = await make_user()
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=creator.id, role="member")
    stranger = await make_user()

    source = _source(workspace.id, {"token_owner": stranger.id}, owner_user_id=creator.id)
    assert await resolve_source_owner(source) is None


async def test_non_numeric_token_owner_is_ignored(workspace, make_user):
    owner = await make_user()
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=owner.id, role="owner")

    assert await resolve_source_owner(_source(workspace.id, {"token_owner": "abc"})) is None
