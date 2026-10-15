"""PostgreSQL acceptance gates for workspace access and personal agent data."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.agent.identity import memory_actor_scope, resolve_runtime_identity
from app.agent.learning import _actor_session_ids
from app.agent.runtime import _dispatch_or_stage
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models import AgentMessage, AgentSession, PlatformRole, User, WorkspaceRole
from app.models.managers.agent_memory_manager import agent_memory
from app.models.managers.tenant_manager import tenant_users, tenants
from app.services.ai.output_contracts import learned_facts

pytestmark = pytest.mark.tenancy


async def _role_id(model, codename: str) -> int:
    role = await model.objects.filter(codename=codename).first()
    assert role is not None, f"test schema is missing seeded role {codename!r}"
    return role.id


@dataclass
class Workspace:
    tenant_id: int
    platform_user_role_id: int
    owner_role_id: int
    viewer_role_id: int
    user_ids: list[int] = field(default_factory=list)

    @classmethod
    async def create(cls) -> "Workspace":
        platform_user_role_id = await _role_id(PlatformRole, "user")
        owner_role_id = await _role_id(WorkspaceRole, "owner")
        viewer_role_id = await _role_id(WorkspaceRole, "viewer")
        suffix = uuid4().hex
        tenant = await tenants.create(
            slug=f"access-accept-{suffix}",
            name="Access acceptance",
            plan="business",
            is_active=True,
        )
        return cls(
            tenant_id=tenant.id,
            platform_user_role_id=platform_user_role_id,
            owner_role_id=owner_role_id,
            viewer_role_id=viewer_role_id,
        )

    async def new_user(self) -> User:
        username = f"access-{uuid4().hex}"
        user = await User.objects.create_user(
            username=username,
            password=uuid4().hex,
            email=f"{username}@example.invalid",
            role_id=self.platform_user_role_id,
            is_active=True,
        )
        self.user_ids.append(user.id)
        return user

    async def add_web_member(self, user: User, role_id: int, *, ai_chat_enabled: bool = False):
        with tenant_scope(self.tenant_id):
            return await tenant_users.create(
                tenant_id=self.tenant_id,
                channel="web",
                external_user_id=str(user.id),
                user_id=user.id,
                role_id=role_id,
                ai_chat_enabled=ai_chat_enabled,
                is_active=True,
            )

    async def add_web_session(self, user: User) -> AgentSession:
        with tenant_scope(self.tenant_id):
            return await AgentSession.objects.create(
                tenant_id=self.tenant_id,
                channel="web",
                chat_id=str(user.id),
                kind="private",
                is_owner=False,
                is_active=True,
                state={},
            )

    @contextmanager
    def as_owner(self, user: User):
        with tenant_scope(self.tenant_id), permission_scope(user, is_owner=True):
            yield

    async def cleanup(self) -> None:
        # This trusted cleanup is limited to the generated test workspace and users.
        with tenant_scope(bypass=True):
            await tenants.delete_by_id(self.tenant_id)
            for user_id in reversed(self.user_ids):
                await User.objects.delete_by_id(user_id)


@asynccontextmanager
async def _workspace():
    workspace = await Workspace.create()
    try:
        yield workspace
    finally:
        await workspace.cleanup()


async def _resolve_web_identity(workspace: Workspace, user: User):
    with tenant_scope(workspace.tenant_id):
        identity = await resolve_runtime_identity(
            SimpleNamespace(
                tenant_id=workspace.tenant_id,
                channel="web",
                user_id=str(user.id),
                chat_id=str(user.id),
            )
        )
    assert identity is not None
    return identity


@pytest.mark.asyncio
async def test_concurrent_active_owners_cannot_both_demote_themselves():
    async with _workspace() as workspace:
        first, second = await workspace.new_user(), await workspace.new_user()
        first_membership = await workspace.add_web_member(first, workspace.owner_role_id)
        second_membership = await workspace.add_web_member(second, workspace.owner_role_id)
        release = asyncio.Event()
        arrived = 0

        async def self_demote(actor: User, membership_id: int) -> bool:
            nonlocal arrived
            with workspace.as_owner(actor):
                arrived += 1
                if arrived == 2:
                    release.set()
                await asyncio.wait_for(release.wait(), timeout=5)
                return await tenant_users.change_member_role(
                    tenant_id=workspace.tenant_id,
                    membership_id=membership_id,
                    role_id=workspace.viewer_role_id,
                )

        outcomes = await asyncio.wait_for(
            asyncio.gather(
                self_demote(first, first_membership.id),
                self_demote(second, second_membership.id),
            ),
            timeout=20,
        )

        assert sorted(outcomes) == [False, True]
        with tenant_scope(workspace.tenant_id):
            memberships = await tenant_users.filter(
                tenant_id=workspace.tenant_id,
                channel="web",
                user_id__in=[first.id, second.id],
            )
        assert sum(membership.role_id == workspace.owner_role_id for membership in memberships) == 1


@pytest.mark.asyncio
async def test_ai_grant_allows_memory_dispatch_but_revoked_ai_or_membership_blocks_stale_identity():
    async with _workspace() as workspace:
        owner, member = await workspace.new_user(), await workspace.new_user()
        await workspace.add_web_member(owner, workspace.owner_role_id)
        membership = await workspace.add_web_member(member, workspace.viewer_role_id)
        session = await workspace.add_web_session(member)
        initial_identity = await _resolve_web_identity(workspace, member)
        assert initial_identity.ai_chat_enabled is False

        with workspace.as_owner(owner):
            assert await tenant_users.set_ai_access(
                tenant_id=workspace.tenant_id,
                membership_id=membership.id,
                enabled=True,
            )

        async def dispatch(identity, key: str, value: str):
            with tenant_scope(workspace.tenant_id):
                return await _dispatch_or_stage(
                    session,
                    "memory_set",
                    {"key": key, "value": value},
                    f"acceptance-{uuid4().hex}",
                    identity,
                )

        result, stopped = await dispatch(initial_identity, "grant-write", "allowed")
        assert not stopped
        assert result

        with workspace.as_owner(owner):
            assert await tenant_users.set_ai_access(
                tenant_id=workspace.tenant_id,
                membership_id=membership.id,
                enabled=False,
            )
        result, stopped = await dispatch(initial_identity, "revoked-ai-write", "denied")
        assert stopped
        assert "Доступ изменился" in result

        with workspace.as_owner(owner):
            assert await tenant_users.set_ai_access(
                tenant_id=workspace.tenant_id,
                membership_id=membership.id,
                enabled=True,
            )
        result, stopped = await dispatch(initial_identity, "membership-write", "allowed-before-revoke")
        assert not stopped
        assert result

        with workspace.as_owner(owner):
            assert await tenant_users.disable_web_member(
                tenant_id=workspace.tenant_id,
                membership_id=membership.id,
            )
        result, stopped = await dispatch(initial_identity, "revoked-membership-write", "denied")
        assert stopped
        assert "Доступ изменился" in result

        with tenant_scope(workspace.tenant_id), memory_actor_scope(member.id, False):
            assert await agent_memory.read("grant-write") == "allowed"
            assert await agent_memory.read("membership-write") == "allowed-before-revoke"
            assert await agent_memory.read("revoked-ai-write") is None
            assert await agent_memory.read("revoked-membership-write") is None


@pytest.mark.asyncio
async def test_two_workspace_members_keep_memory_and_learning_cursors_private():
    async with _workspace() as workspace:
        first, second = await workspace.new_user(), await workspace.new_user()
        await workspace.add_web_member(first, workspace.viewer_role_id, ai_chat_enabled=True)
        await workspace.add_web_member(second, workspace.viewer_role_id, ai_chat_enabled=True)
        first_session = await workspace.add_web_session(first)
        second_session = await workspace.add_web_session(second)

        with tenant_scope(workspace.tenant_id):
            first_message = await AgentMessage.objects.create(
                tenant_id=workspace.tenant_id,
                session_id=first_session.id,
                role="user",
                content="First actor evidence",
            )
            second_message = await AgentMessage.objects.create(
                tenant_id=workspace.tenant_id,
                session_id=second_session.id,
                role="user",
                content="Second actor evidence",
            )

        selected: dict[int, list[int]] = {}
        for user, message, value in (
            (first, first_message, "first-private"),
            (second, second_message, "second-private"),
        ):
            identity = await _resolve_web_identity(workspace, user)
            assert identity.ai_chat_enabled is True
            with tenant_scope(workspace.tenant_id), memory_actor_scope(user.id, False):
                session_ids = await _actor_session_ids()
                selected[user.id] = session_ids
                assert session_ids == [first_session.id if user.id == first.id else second_session.id]
                batch = learned_facts(
                    {
                        "facts": [
                            {
                                "key": "same_key",
                                "value": value,
                                "confidence": 0.9,
                                "evidence_id": message.id,
                            }
                        ]
                    },
                    {message.id},
                )
                assert await agent_memory.apply_learn_batch(
                    batch,
                    expected_watermark=0,
                    new_watermark=message.id,
                    authorized_session_ids=set(session_ids),
                )

        assert set(selected[first.id]).isdisjoint(selected[second.id])
        with tenant_scope(workspace.tenant_id), memory_actor_scope(first.id, False):
            first_transcript = await AgentMessage.objects.filter(session_id__in=selected[first.id])
            assert [message.id for message in first_transcript] == [first_message.id]

            foreign_batch = learned_facts(
                {
                    "facts": [
                        {
                            "key": "foreign_evidence",
                            "value": "must-not-commit",
                            "confidence": 0.9,
                            "evidence_id": second_message.id,
                        }
                    ]
                },
                {second_message.id},
            )
            with pytest.raises(ValueError, match="Learning evidence is no longer an owned user message"):
                await agent_memory.apply_learn_batch(
                    foreign_batch,
                    expected_watermark=first_message.id,
                    new_watermark=max(first_message.id, second_message.id) + 1,
                    authorized_session_ids=set(selected[first.id]),
                )
            assert await agent_memory.read("same_key") == "first-private"
            assert await agent_memory.read("learn_msg_wm", scope="meta") == str(first_message.id)
        with tenant_scope(workspace.tenant_id), memory_actor_scope(second.id, False):
            second_transcript = await AgentMessage.objects.filter(session_id__in=selected[second.id])
            assert [message.id for message in second_transcript] == [second_message.id]
            assert await agent_memory.read("same_key") == "second-private"
            assert await agent_memory.read("learn_msg_wm", scope="meta") == str(second_message.id)
