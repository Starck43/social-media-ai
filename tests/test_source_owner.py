"""Tests for `resolve_source_owner`: which user's personal token a source uses.

A source is workspace-scoped, but its L2 token is personal (`user_credentials`).
The owner is an explicit `Source.params["token_owner"]` (accepted only when the
user is an active member of the workspace) or, by default, the workspace owner.
Runs against the real database.
"""

import secrets
from types import SimpleNamespace

import pytest

from app.models import Role, User
from app.models.managers.tenant_manager import TenantUserManager, tenants
from app.services.social.owner import resolve_source_owner
from app.types import UserRoleType


def _source(tenant_id, params=None):
    return SimpleNamespace(id=1, tenant_id=tenant_id, params=params or {})


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
    role = await Role.objects.get(codename=UserRoleType.VIEWER.name)

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


async def test_no_members_returns_none(workspace):
    assert await resolve_source_owner(_source(workspace.id)) is None


async def test_defaults_to_owner_role_member(workspace, make_user):
    owner = await make_user()
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=owner.id, role="owner")
    member = await make_user()
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=member.id, role="member")

    assert await resolve_source_owner(_source(workspace.id)) == owner.id


async def test_falls_back_to_newest_when_no_owner_role(workspace, make_user):
    first = await make_user()
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=first.id, role="member")
    second = await make_user()
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=second.id, role="member")

    assert await resolve_source_owner(_source(workspace.id)) == second.id


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


async def test_non_numeric_token_owner_is_ignored(workspace, make_user):
    owner = await make_user()
    await TenantUserManager().add_web_member(tenant_id=workspace.id, user_id=owner.id, role="owner")

    assert await resolve_source_owner(_source(workspace.id, {"token_owner": "abc"})) is None
