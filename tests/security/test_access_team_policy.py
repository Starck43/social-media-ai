"""Exercise team authority and last-owner decisions without a live database."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models import WorkspaceRole
from app.models.managers.tenant_manager import TenantUserManager


class Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def __iter__(self):
        return iter(self.value)

    def all(self):
        return self.value


class Session:
    def __init__(self, values):
        self.values = iter(values)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return Result(next(self.values))


def membership(identity, user_id, role):
    return SimpleNamespace(
        id=identity,
        tenant_id=11,
        user_id=user_id,
        channel="web",
        is_active=True,
        role=WorkspaceRole(id=identity + 10, codename=role) if role else None,
        role_id=identity + 10 if role else None,
        ai_chat_enabled=False,
    )


def use_session(manager, session):
    @asynccontextmanager
    async def owned():
        yield session

    manager._get_owned_session = owned


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target_role,new_role", [("owner", "viewer"), ("admin", "viewer"), ("viewer", "admin"), (None, "viewer")]
)
async def test_workspace_admin_cannot_change_protected_or_unresolved_members(target_role, new_role):
    manager = TenantUserManager()
    actor = SimpleNamespace(id=7, is_active=True)
    caller = membership(1, 7, "admin")
    target = membership(2, 8, target_role)
    desired = WorkspaceRole(id=20, codename=new_role)
    session = Session([SimpleNamespace(id=11), actor, [caller, target], desired])
    use_session(manager, session)
    original = target.role_id
    with tenant_scope(11), permission_scope(actor):
        assert not await manager.change_member_role(tenant_id=11, membership_id=2, role_id=20)
    assert target.role_id == original
    assert session.statements[0]._for_update_arg is not None
    assert session.statements[1]._for_update_arg is not None


@pytest.mark.asyncio
async def test_owner_can_resolve_previously_unassigned_member():
    manager = TenantUserManager()
    actor = SimpleNamespace(id=7, is_active=True)
    caller, target = membership(1, 7, "owner"), membership(2, 8, None)
    session = Session([SimpleNamespace(id=11), actor, [caller, target], WorkspaceRole(id=20, codename="viewer")])
    use_session(manager, session)
    with tenant_scope(11), permission_scope(actor):
        assert await manager.change_member_role(tenant_id=11, membership_id=2, role_id=20)
    assert target.role_id == 20


@pytest.mark.asyncio
async def test_last_active_bound_owner_cannot_be_demoted():
    manager = TenantUserManager()
    actor = SimpleNamespace(id=7, is_active=True)
    caller, inactive_peer = membership(1, 7, "owner"), membership(2, 8, "owner")
    session = Session(
        [SimpleNamespace(id=11), actor, [caller, inactive_peer], WorkspaceRole(id=20, codename="viewer"), []]
    )
    use_session(manager, session)
    with tenant_scope(11), permission_scope(actor):
        assert not await manager.change_member_role(tenant_id=11, membership_id=1, role_id=20)
    assert caller.role_id == 11


@pytest.mark.asyncio
async def test_membership_ai_access_changes_only_with_team_authority():
    manager = TenantUserManager()
    actor = SimpleNamespace(id=7, is_active=True)
    caller, target = membership(1, 7, "admin"), membership(2, 8, "viewer")
    session = Session([SimpleNamespace(id=11), actor, [caller, target]])
    use_session(manager, session)
    with tenant_scope(11), permission_scope(actor):
        assert await manager.set_ai_access(tenant_id=11, membership_id=2, enabled=True)
    assert target.ai_chat_enabled
