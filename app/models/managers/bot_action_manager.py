"""Bot action ledger queries and status transitions.

Raw ``select`` statements carry ``_tenant_criterion()`` explicitly, so ledger
reads fail closed exactly like the queryset paths of ``BaseManager``.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access_catalog import workspace_grants
from app.core.database import with_db_session
from app.core.permissions import get_current_user
from app.core.tenant_context import current_tenant_id
from app.types import ActionType, BotActionStatus

from .base_manager import BaseManager

if TYPE_CHECKING:
    from ..actions.agent_action import BotAction


class BotActionManager(BaseManager["BotAction"]):
    """Ledger of proposed bot actions (created PENDING with ``dry_run=True``).

    Mutations lock rows and bind approvals to an active actor and sealed intent.
    Claims are dry-run simulations only; live capability activation is separate.
    """

    def __init__(self) -> None:
        from ..actions.agent_action import BotAction

        super().__init__(BotAction)

    def _scope(self, agent_task_id: int | None, agent_scenario_id: int | None) -> ColumnElement[bool] | None:
        """Per-task scope; scenario only for legacy rows written without a task."""
        model = self.model
        if agent_task_id is not None:
            return model.agent_task_id == agent_task_id
        if agent_scenario_id is not None:
            return and_(model.agent_task_id.is_(None), model.agent_scenario_id == agent_scenario_id)
        return None

    @with_db_session
    async def get_pending_actions(
        self,
        session: AsyncSession,
        limit: int = 100,
        scenario_id: int | None = None,
    ) -> Sequence[BotAction]:
        """Pending actions of the current workspace, newest first."""
        model = self.model
        query = select(model).where(model.status == BotActionStatus.PENDING, *self._tenant_criterion())
        if scenario_id:
            query = query.where(model.agent_scenario_id == scenario_id)
        query = query.order_by(model.created_at.desc()).limit(limit)
        return (await session.execute(query)).scalars().all()

    @with_db_session
    async def get_recent_actions(
        self,
        session: AsyncSession,
        hours: int = 24,
        limit: int = 100,
    ) -> Sequence[BotAction]:
        """Actions of the current workspace created in the last ``hours``, newest first."""
        model = self.model
        since = datetime.now(UTC) - timedelta(hours=hours)
        query = (
            select(model)
            .where(model.created_at >= since, *self._tenant_criterion())
            .order_by(model.created_at.desc())
            .limit(limit)
        )
        return (await session.execute(query)).scalars().all()

    @with_db_session
    async def count_actions_in_window(
        self,
        session: AsyncSession,
        agent_task_id: int | None = None,
        hours: int = 1,
        agent_scenario_id: int | None = None,
    ) -> int:
        """Count actions created within the window (rate limiting).

        Counted per task: the guards live on the task, and two tasks sharing a
        scenario must not spend one budget. ``agent_scenario_id`` covers rows
        written before that move, which carry a NULL task.
        """
        scope = self._scope(agent_task_id, agent_scenario_id)
        if scope is None:
            return 0
        model = self.model
        since = datetime.now(UTC) - timedelta(hours=hours)
        query = select(func.count(model.id)).where(
            scope,
            model.created_at >= since,
            model.status.in_(
                [BotActionStatus.APPROVED, BotActionStatus.CLAIMED, BotActionStatus.EXECUTED, BotActionStatus.UNKNOWN]
            ),
            *self._tenant_criterion(),
        )
        return (await session.execute(query)).scalar() or 0

    @with_db_session
    async def get_last_action_time(
        self,
        session: AsyncSession,
        agent_task_id: int | None = None,
        agent_scenario_id: int | None = None,
    ) -> datetime | None:
        """Creation time of the last EXECUTED action (cooldown), same scope as the count."""
        scope = self._scope(agent_task_id, agent_scenario_id)
        if scope is None:
            return None
        model = self.model
        query = (
            select(func.coalesce(model.executed_at, model.created_at))
            .where(scope, model.status == BotActionStatus.EXECUTED, *self._tenant_criterion())
            .order_by(func.coalesce(model.executed_at, model.created_at).desc())
            .limit(1)
        )
        return (await session.execute(query)).scalar()

    @staticmethod
    def fingerprint(values):
        def read(name):
            return values.get(name) if isinstance(values, dict) else getattr(values, name, None)

        action_type = read("action_type")
        envelope = {
            key: read(key)
            for key in (
                "capability",
                "target_ref",
                "payload",
                "origin",
                "source_id",
                "agent_scenario_id",
                "agent_task_id",
                "analytics_id",
                "dry_run",
            )
        }
        envelope["action_type"] = getattr(action_type, "name", action_type)
        if not isinstance(envelope["payload"], dict):
            raise ValueError("Action payload must be an object")
        return hashlib.sha256(
            json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
        ).hexdigest()

    async def propose(self, *, idempotency_key, capability, target_ref, payload, origin="manual", **references):
        """Atomic proposal insertion. A key can only replay exactly one sealed intent.

        This is a persistence API for trusted producers, not a live execution API.
        Existing create() stays available to import legacy social rows.
        """
        if (
            not isinstance(idempotency_key, str)
            or not 1 <= len(idempotency_key) <= 200
            or not isinstance(capability, str)
            or not 1 <= len(capability) <= 100
            or not isinstance(target_ref, str)
            or not 1 <= len(target_ref) <= 300
            or origin not in {"task", "chat", "manual"}
        ):
            raise ValueError("Invalid proposal identity")
        allowed = {"source_id", "agent_scenario_id", "agent_task_id", "analytics_id", "action_type"}
        if set(references) - allowed:
            raise ValueError("Unexpected proposal fields")
        tenant_id = current_tenant_id()
        if type(tenant_id) is not int or tenant_id <= 0:
            raise PermissionError("Action origin workspace required")
        values = dict(
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            capability=capability,
            target_ref=target_ref,
            payload=payload,
            origin=origin,
            dry_run=True,
            **references,
        )
        values["payload_hash"] = self.fingerprint(values)
        values["origin_snapshot"] = {key: references.get(key) for key in allowed - {"action_type"}}
        from app.models import AgentScenario, AgentTask, AIAnalytics, Source

        async with self._get_owned_session() as session:
            for name, cls in {
                "source_id": Source,
                "agent_scenario_id": AgentScenario,
                "agent_task_id": AgentTask,
                "analytics_id": AIAnalytics,
            }.items():
                if references.get(name) is not None and not await session.scalar(
                    select(cls.id).where(cls.id == references[name], cls.tenant_id == tenant_id)
                ):
                    raise PermissionError("Action reference outside origin workspace")
            statement = (
                insert(self.model)
                .values(**values)
                .on_conflict_do_nothing(constraint="uq_bot_actions_tenant_idempotency")
                .returning(self.model)
            )
            row = (await session.execute(statement)).scalar_one_or_none()
            if row is not None:
                return row, True
            row = await session.scalar(
                select(self.model).where(
                    self.model.tenant_id == tenant_id, self.model.idempotency_key == idempotency_key
                )
            )
            if row.payload_hash != values["payload_hash"] or self.fingerprint(row) != values["payload_hash"]:
                raise ValueError("Idempotency key reused for a different intent")
            return row, False

    async def _actor(self, session, membership_id=None):
        """Re-read active account, workspace, membership and role; never platform bypass."""
        from app.models import Tenant, TenantUser, User, WorkspaceRole

        actor = get_current_user()
        if actor is None or not actor.is_active:
            raise PermissionError("Action actor required")
        tenant_id = current_tenant_id()
        row = (
            await session.execute(
                select(TenantUser, WorkspaceRole)
                .join(WorkspaceRole, WorkspaceRole.id == TenantUser.role_id)
                .join(User, User.id == TenantUser.user_id)
                .join(Tenant, Tenant.id == TenantUser.tenant_id)
                .where(
                    TenantUser.tenant_id == tenant_id,
                    TenantUser.user_id == actor.id,
                    TenantUser.channel == "web",
                    TenantUser.is_active.is_(True),
                    User.is_active.is_(True),
                    Tenant.is_active.is_(True),
                )
                .with_for_update(of=(Tenant, User, TenantUser, WorkspaceRole))
            )
        ).first()
        if row is None or ("botaction", ActionType.UPDATE) not in workspace_grants(row[1].codename):
            raise PermissionError("Action update grant required")
        membership = row[0]
        if membership_id is not None and membership_id != membership.id:
            raise PermissionError("Cannot approve as another membership")
        return actor, membership

    async def _locked(self, session, action_id):
        return await session.scalar(
            select(self.model).where(self.model.id == action_id, *self._tenant_criterion()).with_for_update()
        )

    @staticmethod
    def _record(row, actor, member, status):
        row.history = [
            *(row.history or []),
            {
                "at": datetime.now(UTC).isoformat(),
                "from": row.status.name,
                "to": status.name,
                "actor_user_id": actor.id,
                "actor_membership_id": member.id,
                "payload_hash": row.payload_hash,
            },
        ]

    @with_db_session
    async def approve_action(self, action_id, user_id=None, ttl_seconds=900, *, expected_payload_hash=None, session):
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600:
            raise ValueError("Invalid approval lifetime")
        actor, member = await self._actor(session, user_id)
        row = await self._locked(session, action_id)
        if row is None or row.status != BotActionStatus.PENDING:
            return None
        fingerprint = self.fingerprint(row)
        if expected_payload_hash is not None and expected_payload_hash != fingerprint:
            return None
        if row.payload_hash is not None and row.payload_hash != fingerprint:
            return None
        row.payload_hash = row.approved_payload_hash = fingerprint
        self._record(row, actor, member, BotActionStatus.APPROVED)
        row.status, row.confirmed_by, row.approved_user_id = BotActionStatus.APPROVED, member.id, actor.id
        row.confirmed_at = datetime.now(UTC)
        row.approval_expires_at = row.confirmed_at + timedelta(seconds=ttl_seconds)
        await session.flush()
        return row

    @with_db_session
    async def reject_action(self, action_id, user_id=None, *, session):
        actor, member = await self._actor(session, user_id)
        row = await self._locked(session, action_id)
        if row is None or row.status != BotActionStatus.PENDING:
            return None
        self._record(row, actor, member, BotActionStatus.REJECTED)
        row.status, row.confirmed_by, row.approved_user_id = BotActionStatus.REJECTED, member.id, actor.id
        row.confirmed_at = datetime.now(UTC)
        await session.flush()
        return row

    @with_db_session
    async def claim_action(self, action_id, lease_seconds=60, *, session):
        """Claim a sealed dry-run simulation once. No live capability is enabled."""
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 300:
            raise ValueError("Invalid claim lifetime")
        actor, member = await self._actor(session)
        row = await self._locked(session, action_id)
        now = datetime.now(UTC)
        if (
            row is None
            or row.status != BotActionStatus.APPROVED
            or not row.dry_run
            or row.approved_user_id != actor.id
            or row.confirmed_by != member.id
            or row.approval_expires_at is None
            or row.approval_expires_at <= now
            or row.approved_payload_hash != self.fingerprint(row)
        ):
            return None
        token = secrets.token_urlsafe(32)
        self._record(row, actor, member, BotActionStatus.CLAIMED)
        row.status = BotActionStatus.CLAIMED
        row.claim_token_hash = hashlib.sha256(token.encode()).hexdigest()
        row.claimed_at, row.claim_expires_at = now, now + timedelta(seconds=lease_seconds)
        row.attempts = (row.attempts or 0) + 1
        await session.flush()
        return row, token

    async def _finish(self, session, action_id, token, *, failed=False):
        actor, member = await self._actor(session)
        row = await self._locked(session, action_id)
        if (
            row is None
            or row.status != BotActionStatus.CLAIMED
            or not row.dry_run
            or row.approved_user_id != actor.id
            or row.confirmed_by != member.id
            or not isinstance(token, str)
            or not row.claim_token_hash
            or not secrets.compare_digest(row.claim_token_hash, hashlib.sha256(token.encode()).hexdigest())
            or row.claim_expires_at is None
            or row.claim_expires_at <= datetime.now(UTC)
            or row.approval_expires_at is None
            or row.approval_expires_at <= datetime.now(UTC)
            or row.approved_payload_hash != self.fingerprint(row)
        ):
            return None
        self._record(row, actor, member, BotActionStatus.FAILED if failed else BotActionStatus.EXECUTED)
        row.status = BotActionStatus.FAILED if failed else BotActionStatus.EXECUTED
        row.executed_at = datetime.now(UTC)
        row.result = {"dry_run": True, "simulation_only": True, "success": not failed}
        row.error = "simulation_failed" if failed else None
        row.claim_token_hash = None
        await session.flush()
        return row

    @with_db_session
    async def mark_executed(self, action_id, result=None, *, claim_token=None, session):
        # Caller/provider bodies are deliberately not persisted by this dry-run API.
        return await self._finish(session, action_id, claim_token)

    @with_db_session
    async def mark_failed(self, action_id, error="simulation_failed", *, claim_token=None, session):
        return await self._finish(session, action_id, claim_token, failed=True)

    @with_db_session
    async def mark_unknown(self, action_id, *, session):
        actor, member = await self._actor(session)
        row = await self._locked(session, action_id)
        if (
            row is None
            or row.status != BotActionStatus.CLAIMED
            or row.approved_user_id != actor.id
            or row.claim_expires_at is None
            or row.claim_expires_at > datetime.now(UTC)
        ):
            return None
        self._record(row, actor, member, BotActionStatus.UNKNOWN)
        row.status, row.claim_token_hash, row.error = BotActionStatus.UNKNOWN, None, "claim_expired"
        await session.flush()
        return row

    @with_db_session
    async def cancel_action(self, action_id, *, session):
        actor, member = await self._actor(session)
        row = await self._locked(session, action_id)
        if row is None or row.status not in {BotActionStatus.PENDING, BotActionStatus.APPROVED}:
            return None
        self._record(row, actor, member, BotActionStatus.CANCELLED)
        row.status = BotActionStatus.CANCELLED
        row.claim_token_hash = None
        await session.flush()
        return row


# Compatibility type name. Public models share this single manager instance.
AgentActionManager = BotActionManager
