"""Local PostgreSQL digest approval acceptance; never run against production.

Uses current conftest (read-only schema validation), real managers and runtime.
Only publisher and model continuation are replaced. Run sequentially via OpenCode.
This module also provides owned-fixture helpers for the sibling ASGI module.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager, contextmanager
from copy import copy, deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit
from uuid import uuid4

import pytest

# isort: split
# Refuse --noconftest/direct execution BEFORE importing application engines/models.
_expected_conftest = Path(__file__).resolve().parents[1] / "conftest.py"
_conftest = next(
    (module for module in tuple(sys.modules.values()) if getattr(module, "__file__", None) == str(_expected_conftest)),
    None,
)
if _conftest is None or getattr(_conftest, "TEST_SCHEMA", None) != "test_schema":
    raise RuntimeError("STOP: current root conftest and test_schema redirect are required; do not use --noconftest")
_target = urlsplit(getattr(_conftest, "TEST_DATABASE_URL", ""))
if (
    _target.hostname not in ("localhost", "127.0.0.1")
    or (_target.port or 5432) != 5432
    or _target.path != "/social_manager"
):
    raise RuntimeError("STOP: only the pre-approved local social_manager/test_schema target is allowed")
# isort: split

from sqlalchemy import text

from app.agent import runtime
from app.agent.confirmation import make_pending_intent
from app.agent.identity import resolve_runtime_identity
from app.agent.tools import get_tool
from app.channels.base import Inbound
from app.core.database import async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import current_tenant_id, tenant_scope
from app.models import AgentSession, PlatformRole, User, WorkspaceRole
from app.models.managers.agent_session_manager import agent_sessions
from app.models.managers.tenant_manager import tenant_channels, tenant_users, tenants

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]
ARGS = {"period": "week", "group_by": "sources", "time_breakdown": True}


async def role_id(model, code):
    row = await model.objects.filter(codename=code).first()
    assert row is not None, f"Existing test catalog lacks {model.__name__}:{code}; do not seed"
    return row.id


@dataclass
class DigestWorkspace:
    """Own ONLY newly generated rows; never edit seeded roles or grant links."""

    tenant_id: int
    users: list = field(default_factory=list)
    owner: object = None
    password: str = field(default_factory=lambda: f"Synthetic-{uuid4().hex}")

    async def user(self, platform="user"):
        code_id = await role_id(PlatformRole, platform)
        name = f"digest-accept-{uuid4().hex}"
        user = await User.objects.create_user(
            username=name,
            email=f"{name}@example.invalid",
            password=self.password,
            role_id=code_id,
            is_active=True,
        )
        self.users.append(user.id)
        return user

    async def member(self, user, role="analyst", *, ai=True, channel="web"):
        code_id = await role_id(WorkspaceRole, role)
        with tenant_scope(self.tenant_id):
            return await tenant_users.create(
                tenant_id=self.tenant_id,
                channel=channel,
                external_user_id=str(user.id),
                user_id=user.id,
                role_id=code_id,
                ai_chat_enabled=ai,
                is_active=True,
            )

    @contextmanager
    def as_owner(self):
        with tenant_scope(self.tenant_id), permission_scope(self.owner, is_owner=True):
            yield

    async def change(self, membership, change):
        """Normal team services where available; owned deletion is fault injection."""
        if change in ("admin", "viewer"):
            code_id = await role_id(WorkspaceRole, change)
            with self.as_owner():
                assert await tenant_users.change_member_role(
                    tenant_id=self.tenant_id,
                    membership_id=membership.id,
                    role_id=code_id,
                )
        elif change == "ai":
            with self.as_owner():
                assert await tenant_users.set_ai_access(
                    tenant_id=self.tenant_id,
                    membership_id=membership.id,
                    enabled=False,
                )
        elif change == "inactive":
            with self.as_owner():
                assert await tenant_users.disable_web_member(
                    tenant_id=self.tenant_id,
                    membership_id=membership.id,
                )
        elif change == "missing":
            # Deliberate deletion of this fixture's membership, not historical cleanup.
            with tenant_scope(bypass=True):
                assert await tenant_users.delete_by_id(membership.id)
        else:
            raise AssertionError(f"Unknown mutation: {change}")

    async def cleanup(self):
        with tenant_scope(bypass=True):
            await tenants.delete_by_id(self.tenant_id)
            for user_id in reversed(self.users):
                await User.objects.delete_by_id(user_id)
            assert await tenants.get(id=self.tenant_id) is None
            for user_id in self.users:
                assert await User.objects.get(id=user_id) is None
            assert not list(await AgentSession.objects.filter(tenant_id=self.tenant_id))


@asynccontextmanager
async def owned_workspace():
    # Catalog reads are intentionally not a fresh-schema bootstrap.
    await role_id(PlatformRole, "user")
    await role_id(WorkspaceRole, "owner")
    tenant = await tenants.create(
        slug=f"digest-accept-{uuid4().hex}",
        name="Synthetic digest acceptance",
        plan="business",
        is_active=True,
        daily_cost_limit=0,
    )
    workspace = DigestWorkspace(tenant.id)
    try:
        workspace.owner = await workspace.user()
        await workspace.member(workspace.owner, "owner", ai=False)
        yield workspace
    finally:
        await workspace.cleanup()


@pytest.fixture
def digest_effects(monkeypatch):
    """Prevent sends and model calls; authorization/storage remain real."""
    from app.core.config import settings
    from app.services.digest import builder
    from app.web import digests

    # Isolate spend-limit configuration, not membership/permission/approval policy.
    monkeypatch.setattr(settings, "AGENT_DAILY_COST_LIMIT", 0)
    seen = []

    async def publish(**kwargs):
        seen.append((current_tenant_id(), kwargs))
        return {"status": "sent"}

    publisher = AsyncMock(side_effect=publish)
    monkeypatch.setattr(builder, "build_and_publish", publisher)
    monkeypatch.setattr(digests, "build_and_publish", publisher)
    monkeypatch.setattr(runtime, "_build_messages", AsyncMock(return_value=[]))
    monkeypatch.setattr(runtime, "_run_tool_loop", AsyncMock(return_value="synthetic model continuation"))
    monkeypatch.setattr(runtime, "_chat", AsyncMock(side_effect=AssertionError("Live model calls forbidden")))
    return SimpleNamespace(publisher=publisher, seen=seen)


@dataclass
class DigestActor:
    workspace: DigestWorkspace
    user: object
    membership: object
    session: object
    resolution: object

    async def identity(self):
        with tenant_scope(self.workspace.tenant_id):
            result = await resolve_runtime_identity(self.resolution)
        assert result is not None
        return result

    async def stored(self):
        with tenant_scope(self.workspace.tenant_id):
            row = await agent_sessions.get(id=self.session.id)
        assert row is not None
        return deepcopy(row.state)

    async def stage(self):
        identity = await self.identity()
        with tenant_scope(self.workspace.tenant_id):
            result = await runtime._dispatch_or_stage(
                self.session,
                "digest_send_now",
                deepcopy(ARGS),
                f"call-{uuid4().hex}",
                identity,
            )
        return result

    async def yes(self):
        inbound = Inbound(
            channel=self.resolution.channel,
            chat_id=self.resolution.chat_id,
            user_id=self.resolution.user_id,
            text="да",
        )
        with tenant_scope(self.workspace.tenant_id):
            return await runtime._handle_in_tenant(inbound, self.resolution)


async def actor(workspace, role="analyst", *, ai=True, platform="user", channel="web", chat_id=None):
    user = await workspace.user(platform)
    membership = await workspace.member(user, role, ai=ai, channel=channel)
    chat_id = chat_id or str(user.id)
    with tenant_scope(workspace.tenant_id):
        if channel != "web":
            await tenant_channels.create(
                tenant_id=workspace.tenant_id,
                channel=channel,
                chat_id=chat_id,
                kind="group",
                is_active=True,
                is_digest_target=False,
            )
        session = await AgentSession.objects.create(
            tenant_id=workspace.tenant_id,
            channel=channel,
            chat_id=chat_id,
            kind="private" if channel == "web" else "group",
            is_active=True,
            is_owner=role == "owner",
            state={"keep": "sentinel", "update_offset": 0},
        )
    return DigestActor(
        workspace,
        user,
        membership,
        session,
        SimpleNamespace(
            tenant_id=workspace.tenant_id, channel=channel, chat_id=chat_id, user_id=str(user.id), onboarded=False
        ),
    )


async def staged(subject, effects):
    reply, stop = await subject.stage()
    assert stop and "подтвер" in reply.lower()
    pending = (await subject.stored())["pending_confirmation"]
    identity = await subject.identity()
    assert pending["authorization"] == {
        "version": 3,
        "actor": identity.actor_binding(),
        "session_id": subject.session.id,
        "approval_binding": identity.approval_binding(),
    }
    assert pending["required_permission"] == "notifications.digest_run.create"
    assert pending["args"] == ARGS
    assert (await subject.stored())["keep"] == "sentinel"
    effects.publisher.assert_not_awaited()
    return pending


@pytest.mark.parametrize(
    "role,allowed",
    [
        ("owner", True),
        ("admin", True),
        ("analyst", True),
        ("manager", False),
        ("viewer", False),
    ],
)
async def test_real_presets_stage_confirm_and_replay(role, allowed, digest_effects):
    async with owned_workspace() as workspace:
        subject = await actor(workspace, role, ai=role != "owner")
        if not allowed:
            reply, stop = await subject.stage()
            assert not stop and "нет прав" in reply
            assert "pending_confirmation" not in await subject.stored()
            digest_effects.publisher.assert_not_awaited()
            return
        await staged(subject, digest_effects)

        async def publish_after_durable_removal(**kwargs):
            assert "pending_confirmation" not in await subject.stored()
            digest_effects.seen.append((current_tenant_id(), kwargs))
            return {"status": "sent"}

        digest_effects.publisher.side_effect = publish_after_durable_removal
        assert await subject.yes() == "synthetic model continuation"
        digest_effects.publisher.assert_awaited_once_with(**ARGS)
        assert digest_effects.seen == [(workspace.tenant_id, ARGS)]
        state = await subject.stored()
        assert state == {"keep": "sentinel", "update_offset": 0}
        await subject.yes()
        digest_effects.publisher.assert_awaited_once()


async def test_platform_admin_viewer_cannot_stage(digest_effects):
    async with owned_workspace() as workspace:
        subject = await actor(workspace, "viewer", platform="admin")
        reply, stop = await subject.stage()
        assert not stop and "нет прав" in reply
        assert "pending_confirmation" not in await subject.stored()
        digest_effects.publisher.assert_not_awaited()


async def test_ai_disabled_analyst_cannot_stage(digest_effects):
    async with owned_workspace() as workspace:
        subject = await actor(workspace, ai=False)
        reply, stop = await subject.stage()
        assert stop and "Доступ изменился" in reply
        assert "pending_confirmation" not in await subject.stored()
        digest_effects.publisher.assert_not_awaited()


@pytest.mark.parametrize("change", ["admin", "viewer", "ai", "inactive", "missing"])
@pytest.mark.parametrize("moment", ["before_yes", "after_real_consume"])
async def test_persisted_authority_changes_refuse_send(change, moment, digest_effects, monkeypatch):
    async with owned_workspace() as workspace:
        subject = await actor(workspace)
        pending = await staged(subject, digest_effects)
        consumed = []
        real_consume = agent_sessions.consume_pending_confirmation

        async def consume_then_change(session_id, expected):
            state = await real_consume(session_id, expected)
            assert state is not None
            consumed.append(session_id)
            # The real CAS has COMMITTED before the real membership mutation.
            await workspace.change(subject.membership, change)
            return state

        if moment == "before_yes":
            await workspace.change(subject.membership, change)
        else:
            monkeypatch.setattr(agent_sessions, "consume_pending_confirmation", consume_then_change)
        if moment == "before_yes" and change in ("ai", "inactive", "missing"):
            with pytest.raises(PermissionError, match="AI chat|active workspace identity"):
                await subject.yes()
        else:
            assert "отклонено" in await subject.yes()
        digest_effects.publisher.assert_not_awaited()
        state = await subject.stored()
        assert state["keep"] == "sentinel" and state["update_offset"] == 0
        if moment == "after_real_consume":
            assert consumed == [subject.session.id]
            assert "pending_confirmation" not in state
        elif change in ("ai", "inactive", "missing"):
            assert state["pending_confirmation"] == pending
        else:
            assert "pending_confirmation" not in state


@pytest.mark.parametrize("invalid", ["expired", "version2", "old_update"])
async def test_persisted_obsolete_intents_are_rejected(invalid, digest_effects):
    async with owned_workspace() as workspace:
        subject = await actor(workspace)
        pending = await staged(subject, digest_effects)
        if invalid == "expired":
            pending["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        elif invalid == "version2":
            pending["authorization"]["version"] = 2
        else:
            old_spec = copy(get_tool("digest_send_now"))
            old_spec.required_permission = "notifications.digest_run.update"
            pending = make_pending_intent(
                await subject.identity(),
                subject.session,
                old_spec,
                ARGS,
                "historical-update",
            )
            assert pending is not None
        with tenant_scope(workspace.tenant_id):
            await agent_sessions.set_state(subject.session.id, pending_confirmation=pending)
        assert "отклонено" in await subject.yes()
        digest_effects.publisher.assert_not_awaited()
        assert await subject.stored() == {"keep": "sentinel", "update_offset": 0}


async def test_real_group_actor_cannot_consume_another_members_intent(digest_effects):
    async with owned_workspace() as workspace:
        subject = await actor(workspace, channel="telegram", chat_id=f"synthetic-group-{uuid4().hex}")
        pending = await staged(subject, digest_effects)
        other_user = await workspace.user()
        other_membership = await workspace.member(other_user, channel="telegram")
        other = DigestActor(
            workspace,
            other_user,
            other_membership,
            subject.session,
            SimpleNamespace(
                tenant_id=workspace.tenant_id,
                channel="telegram",
                chat_id=subject.resolution.chat_id,
                user_id=str(other_user.id),
                onboarded=False,
            ),
        )
        assert "отклонено" in await other.yes()
        assert (await subject.stored())["pending_confirmation"] == pending
        digest_effects.publisher.assert_not_awaited()
        await subject.yes()
        digest_effects.publisher.assert_awaited_once_with(**ARGS)


@pytest.mark.parametrize("invalid", ["user", "workspace", "channel", "unbound_user", "unresolved_role"])
async def test_real_messenger_identity_loss_preserves_pending(invalid, digest_effects, monkeypatch):
    async with owned_workspace() as workspace:
        subject = await actor(workspace, channel="telegram", chat_id=f"synthetic-group-{uuid4().hex}")
        pending = await staged(subject, digest_effects)
        # Mutate only newly generated fixture records, never shared catalogs.
        with tenant_scope(bypass=True):
            if invalid == "user":
                assert await User.objects.update_by_id(subject.user.id, is_active=False)
            elif invalid == "workspace":
                assert await tenants.update_by_id(workspace.tenant_id, is_active=False)
            elif invalid == "channel":
                binding = await tenant_channels.get(
                    tenant_id=workspace.tenant_id,
                    channel="telegram",
                    chat_id=subject.resolution.chat_id,
                )
                assert binding is not None
                assert await tenant_channels.update_by_id(binding.id, is_active=False)
            elif invalid == "unbound_user":
                assert await tenant_users.update_by_id(subject.membership.id, user_id=None)
            else:
                assert await tenant_users.update_by_id(subject.membership.id, role_id=None)
        real_consume = agent_sessions.consume_pending_confirmation
        spy = AsyncMock(wraps=real_consume)
        monkeypatch.setattr(agent_sessions, "consume_pending_confirmation", spy)
        reply = await subject.yes()
        assert "Чат не связан с активным пользователем" in reply
        spy.assert_not_awaited()
        digest_effects.publisher.assert_not_awaited()
        assert (await subject.stored())["pending_confirmation"] == pending


async def test_real_cas_rejects_foreign_scope_and_stale_replaced_intent(digest_effects):
    async with owned_workspace() as workspace, owned_workspace() as foreign:
        subject = await actor(workspace)
        old = await staged(subject, digest_effects)
        with tenant_scope(foreign.tenant_id):
            assert await agent_sessions.consume_pending_confirmation(subject.session.id, old) is None
        assert (await subject.stored())["pending_confirmation"] == old
        await subject.stage()
        replacement = (await subject.stored())["pending_confirmation"]
        # `82555f2`: an identical repeat is answered by the pending intent, so the
        # same `intent_id` survives and the stored snapshot is unchanged.
        assert replacement["intent_id"] == old["intent_id"]
        assert replacement == old
        # Exactly one question is outstanding and one «да» answers it. The CAS is
        # the unit twin's job (`test_repeated_identical_intent_keeps_intent_id_
        # and_stays_answerable_once`); consuming the snapshot by hand here would
        # spend the intent and leave «да» with nothing to confirm.
        reply = await subject.yes()
        assert "отклонено" not in reply
        digest_effects.publisher.assert_awaited_once_with(**ARGS)
        assert "pending_confirmation" not in await subject.stored()


async def test_real_changed_intent_retires_the_old_snapshot(digest_effects):
    """A *changed* intent is a new question; the old snapshot must not consume it."""
    async with owned_workspace() as workspace:
        subject = await actor(workspace)
        old = await staged(subject, digest_effects)
        identity = await subject.identity()
        with tenant_scope(workspace.tenant_id):
            await runtime._dispatch_or_stage(
                subject.session,
                "digest_send_now",
                {**ARGS, "period": "themes-of-week"},
                f"call-{uuid4().hex}",
                identity,
            )
        replacement = (await subject.stored())["pending_confirmation"]
        assert replacement["intent_id"] != old["intent_id"]
        with tenant_scope(workspace.tenant_id):
            assert await agent_sessions.consume_pending_confirmation(subject.session.id, old) is None
        assert (await subject.stored())["pending_confirmation"] == replacement
        digest_effects.publisher.assert_not_awaited()


async def test_two_real_confirmation_turns_wait_on_row_then_publish_once(digest_effects, monkeypatch):
    async with owned_workspace() as workspace:
        subject = await actor(workspace)
        await staged(subject, digest_effects)
        original_owned = agent_sessions._get_owned_session
        real_consume = agent_sessions.consume_pending_confirmation
        pids = asyncio.Queue()
        outcomes = []
        arrivals = 0
        ready = asyncio.Event()

        @asynccontextmanager
        async def tracked_owned(session=None):
            async with original_owned(session) as db:
                await db.execute(text("SET LOCAL lock_timeout = '15s'"))
                await pids.put((await db.execute(text("SELECT pg_backend_pid()"))).scalar_one())
                yield db

        async def synchronized_consume(session_id, expected):
            nonlocal arrivals
            arrivals += 1
            if arrivals == 2:
                ready.set()
            await asyncio.wait_for(ready.wait(), 10)
            state = await real_consume(session_id, expected)
            outcomes.append(state)
            return state

        tasks = []
        with monkeypatch.context() as patcher:
            patcher.setattr(agent_sessions, "_get_owned_session", tracked_owned)
            patcher.setattr(agent_sessions, "consume_pending_confirmation", synchronized_consume)
            async with async_session_maker() as locker:
                try:
                    await locker.execute(
                        AgentSession.__table__.select()
                        .where(
                            AgentSession.id == subject.session.id,
                            AgentSession.tenant_id == workspace.tenant_id,
                        )
                        .with_for_update()
                    )
                    tasks = [asyncio.create_task(subject.yes()) for _ in range(2)]
                    backend_pids = [await asyncio.wait_for(pids.get(), 10) for _ in tasks]
                    assert len(set(backend_pids)) == 2
                    async with async_session_maker() as observer:
                        deadline = asyncio.get_running_loop().time() + 8
                        while True:
                            blocked = [
                                bool(
                                    (
                                        await observer.execute(
                                            text("SELECT pg_blocking_pids(:pid)"),
                                            {"pid": pid},
                                        )
                                    ).scalar_one()
                                )
                                for pid in backend_pids
                            ]
                            if all(blocked):
                                break
                            assert not any(task.done() for task in tasks), "A turn finished before lock release"
                            assert asyncio.get_running_loop().time() < deadline, "Missing real PG lock wait"
                            await asyncio.sleep(0.025)
                    await locker.commit()
                    replies = await asyncio.wait_for(asyncio.gather(*tasks), 15)
                finally:
                    await locker.rollback()
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        assert sum(state is not None for state in outcomes) == 1
        assert any("уже использовано" in reply for reply in replies)
        digest_effects.publisher.assert_awaited_once_with(**ARGS)
        assert await subject.stored() == {"keep": "sentinel", "update_offset": 0}


@pytest.mark.parametrize("failure", ["before_commit", "after_commit"])
async def test_runtime_gets_no_authority_from_failed_commit_ack(failure, digest_effects, monkeypatch):
    async with owned_workspace() as workspace:
        subject = await actor(workspace)
        pending = await staged(subject, digest_effects)
        original_owned = agent_sessions._get_owned_session

        @asynccontextmanager
        async def failed_owned(session=None):
            assert session is None
            async with original_owned() as db:
                if failure == "after_commit":
                    real_commit = db.commit

                    async def unacknowledged_commit():
                        await real_commit()
                        raise RuntimeError("Synthetic acknowledgement loss after real commit")

                    db.commit = unacknowledged_commit
                yield db
                if failure == "before_commit":
                    raise RuntimeError("Synthetic failure before commit")

        with monkeypatch.context() as patcher:
            patcher.setattr(agent_sessions, "_get_owned_session", failed_owned)
            assert "сохранение не подтверждено" in await subject.yes()
        digest_effects.publisher.assert_not_awaited()
        state = await subject.stored()
        if failure == "before_commit":
            assert state["pending_confirmation"] == pending
            await subject.yes()
            digest_effects.publisher.assert_awaited_once_with(**ARGS)
        else:
            assert "pending_confirmation" not in state
            await subject.yes()
            digest_effects.publisher.assert_not_awaited()
        assert (await subject.stored())["keep"] == "sentinel"
