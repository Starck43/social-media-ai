"""Transactional business write-approval command ledger (P13-T04).

Guarantees:
1. Preview creates a command + per-target rows bound to `(tenant_id, actor_user_id, capability, idempotency_key)`.
   Exact idempotent replay returns the existing command; mismatched payload raises a conflict.
2. Approve binds the approver, tenant, capability version, canonical payload hash and TTL
   (default 15m, max 24h). Any payload mutation invalidates approval.
3. Execute acquires a row lock (`SELECT ... FOR UPDATE`), transitions `approved -> claimed`
   atomically (preventing concurrent double execution), rechecks active membership, role grant,
   capability enablement, kill switch, connection state and TTL before calling the adapter once.
4. Adapter timeout / transport ambiguity records `unknown` with a reconcile hook and emits
   an `unknown` audit event — never an automatic blind resend.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy import select

from app.core.access_catalog import workspace_grants
from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import get_current_user
from app.core.tenant_context import TenantContextError, current_tenant_id, is_bypass
from app.models.actions.business_command import BusinessCommand, BusinessCommandTarget
from app.models.audit.action_audit_event import ActionAuditEvent
from app.models.identity.user import User
from app.models.integrations.connection import WorkspaceConnection
from app.models.tenancy.tenant import Tenant
from app.models.tenancy.tenant_user import TenantUser
from app.models.tenancy.workspace_role import WorkspaceRole

from .registry import (
    DEFAULT_TTL_SECONDS,
    MAX_SCHEDULED_TTL_SECONDS,
    CapabilitySpec,
    canonical_payload_hash,
    get_capability,
)


class CommandError(RuntimeError):
    """Base value-safe error for business command operations."""


class CommandNotFound(CommandError):
    """Raised when a command does not exist in the current workspace."""


class CommandDenied(CommandError):
    """Raised when actor, role, capability, kill switch, TTL or payload check fails."""


class CommandConflict(CommandError):
    """Raised on state transition conflict, idempotency mismatch or concurrent claim."""


class CommandTimeoutUnknown(CommandError):
    """Raised when an external write times out and enters `unknown` reconciliation state."""


AdapterCallable = Callable[[BusinessCommand, list[BusinessCommandTarget]], Awaitable[dict[str, str]]]


def _require_tenant_and_actor() -> tuple[int, int]:
    tenant_id = current_tenant_id()
    actor = get_current_user()
    if is_bypass() or type(tenant_id) is not int or tenant_id <= 0:
        raise TenantContextError("Business commands require an explicit workspace scope")
    if actor is None or not getattr(actor, "is_active", False) or type(actor.id) is not int or actor.id <= 0:
        raise CommandDenied("Active authenticated user required")
    return tenant_id, actor.id


RUNTIME_ENABLED_CAPABILITIES: set[str] = set()
RUNTIME_KILL_SWITCHES: dict[str, bool] = {}


def _is_capability_enabled(spec: CapabilitySpec) -> bool:
    if RUNTIME_KILL_SWITCHES.get(spec.kill_switch_key) or RUNTIME_KILL_SWITCHES.get(spec.code):
        return False
    kill_flags = getattr(settings, "COMMAND_KILL_SWITCHES", None) or {}
    if isinstance(kill_flags, dict) and kill_flags.get(spec.kill_switch_key):
        return False
    if getattr(settings, spec.kill_switch_key, False):
        return False
    if spec.code in RUNTIME_ENABLED_CAPABILITIES:
        return True
    enabled_map = getattr(settings, "ENABLED_BUSINESS_CAPABILITIES", None)
    if isinstance(enabled_map, (set, frozenset, list, tuple)):
        return spec.code in enabled_map
    if isinstance(enabled_map, dict):
        return bool(enabled_map.get(spec.code, spec.enabled_by_default))
    # New outbox transport capabilities use the already accepted per-transport
    # operator switches; exact binding/kill checks are repeated by the sender.
    prefix = {
        "comms.email.send.v1": "P15_EMAIL_SEND",
        "comms.telegram.bot_send.v1": "P15_TELEGRAM_BOT_SEND",
        "comms.max.bot_send.v1": "P15_MAX_BOT_SEND",
    }.get(spec.code)
    if spec.code == "calendar.event_write.v1":
        return settings.CALENDAR_EVENT_WRITE_ENABLED
    if prefix is not None:
        return os.environ.get(f"{prefix}_ENABLED", "0") == "1" and os.environ.get(f"{prefix}_KILL", "0") == "0"
    return spec.enabled_by_default


async def _verify_member_grant(session, tenant_id: int, user_id: int, spec: CapabilitySpec) -> None:
    user = (
        await session.execute(select(User.id).where(User.id == user_id, User.is_active.is_(True)))
    ).scalar_one_or_none()
    tenant = (
        await session.execute(select(Tenant.id).where(Tenant.id == tenant_id, Tenant.is_active.is_(True)))
    ).scalar_one_or_none()
    member = (
        await session.execute(
            select(TenantUser).where(
                TenantUser.tenant_id == tenant_id,
                TenantUser.user_id == user_id,
                TenantUser.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if user is None or tenant is None or member is None or member.role_id is None:
        raise CommandDenied("Workspace membership is inactive or missing")
    role_code = (
        await session.execute(select(WorkspaceRole.codename).where(WorkspaceRole.id == member.role_id))
    ).scalar_one_or_none()
    grants = workspace_grants(role_code) if role_code is not None else frozenset()
    if (spec.required_resource, spec.required_action) not in grants:
        raise CommandDenied("Insufficient workspace role permissions for capability")


async def _verify_connection(session, tenant_id: int, connection_id: int | None) -> None:
    if connection_id is None:
        return
    conn = (
        await session.execute(
            select(WorkspaceConnection).where(
                WorkspaceConnection.tenant_id == tenant_id,
                WorkspaceConnection.id == connection_id,
            )
        )
    ).scalar_one_or_none()
    if conn is None or not conn.is_active or conn.state != "verified":
        raise CommandDenied("Bound workspace connection is missing, disabled or unverified")


def serialize_command(cmd: BusinessCommand, targets: list[BusinessCommandTarget] | None = None) -> dict[str, Any]:
    return {
        "id": cmd.id,
        "tenant_id": cmd.tenant_id,
        "capability": cmd.capability,
        "capability_version": cmd.capability_version,
        "approval_policy": cmd.approval_policy,
        "actor_user_id": cmd.actor_user_id,
        "connection_id": cmd.connection_id,
        "idempotency_key": cmd.idempotency_key,
        "target_refs": list(cmd.target_refs or []),
        "payload_hash": cmd.payload_hash,
        "approved_payload_hash": cmd.approved_payload_hash,
        "preview_snapshot": dict(cmd.preview_snapshot or {}),
        "state": cmd.state,
        "approved_user_id": cmd.approved_user_id,
        "approved_at": cmd.approved_at,
        "approval_expires_at": cmd.approval_expires_at,
        "claimed_at": cmd.claimed_at,
        "executed_at": cmd.executed_at,
        "outcome_summary": cmd.outcome_summary,
        "error_code": cmd.error_code,
        "reconcile_hint": cmd.reconcile_hint,
        "targets": [
            {
                "id": t.id,
                "target_ref": t.target_ref,
                "target_idempotency_key": t.target_idempotency_key,
                "state": t.state,
                "provider_receipt": t.provider_receipt,
                "error_code": t.error_code,
                "executed_at": t.executed_at,
            }
            for t in (targets or [])
        ],
    }


class BusinessCommandLedger:
    """Service API for previewing, approving, rejecting, executing and reconciling commands."""

    async def preview_command(
        self,
        *,
        capability: str,
        idempotency_key: str,
        target_refs: list[str],
        payload: dict[str, Any],
        preview_snapshot: dict[str, Any] | None = None,
        connection_id: int | None = None,
        session=None,
    ) -> dict[str, Any]:
        tenant_id, actor_id = _require_tenant_and_actor()
        try:
            spec = get_capability(capability)
            digest = canonical_payload_hash(capability, target_refs, payload)
        except ValueError as exc:
            raise CommandDenied(str(exc)) from exc
        if len(target_refs) > 1 and spec.approval_policy != "per_batch":
            raise CommandDenied("Capability does not allow multi-target batch commands")

        if session is None:
            async with async_session_maker() as owned, owned.begin():
                return await self.preview_command(
                    capability=capability,
                    idempotency_key=idempotency_key,
                    target_refs=target_refs,
                    payload=payload,
                    preview_snapshot=preview_snapshot,
                    connection_id=connection_id,
                    session=owned,
                )

        await _verify_member_grant(session, tenant_id, actor_id, spec)
        await _verify_connection(session, tenant_id, connection_id)

        existing = (
            await session.execute(
                select(BusinessCommand).where(
                    BusinessCommand.tenant_id == tenant_id,
                    BusinessCommand.actor_user_id == actor_id,
                    BusinessCommand.capability == spec.code,
                    BusinessCommand.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.payload_hash != digest:
                raise CommandConflict("Idempotency key already bound to a different payload")
            targets = list(
                (
                    await session.execute(
                        select(BusinessCommandTarget)
                        .where(
                            BusinessCommandTarget.tenant_id == tenant_id,
                            BusinessCommandTarget.command_id == existing.id,
                        )
                        .order_by(BusinessCommandTarget.id)
                    )
                )
                .scalars()
                .all()
            )
            return serialize_command(existing, targets)

        now = datetime.now(timezone.utc)
        cmd = BusinessCommand(
            tenant_id=tenant_id,
            capability=spec.code,
            capability_version=spec.version,
            approval_policy=spec.approval_policy,
            actor_user_id=actor_id,
            connection_id=connection_id,
            idempotency_key=idempotency_key,
            target_refs=list(target_refs),
            payload=dict(payload),
            payload_hash=digest,
            preview_snapshot=dict(preview_snapshot or {"summary": spec.code, "targets_count": len(target_refs)}),
            state="previewed",
            created_at=now,
            updated_at=now,
        )
        session.add(cmd)
        await session.flush()

        target_rows: list[BusinessCommandTarget] = []
        for idx, ref in enumerate(target_refs):
            target_row = BusinessCommandTarget(
                tenant_id=tenant_id,
                command_id=cmd.id,
                target_ref=ref,
                target_idempotency_key=f"{idempotency_key}:{idx}:{ref}"[:160],
                state="pending",
                created_at=now,
                updated_at=now,
            )
            session.add(target_row)
            target_rows.append(target_row)
        await session.flush()

        await ActionAuditEvent.objects.record_success(
            session=session,
            actor_kind="user",
            actor_id=actor_id,
            action="commands.create",
            resource_kind="command",
            resource_id=cmd.id,
        )
        return serialize_command(cmd, target_rows)

    async def approve_command(
        self,
        command_id: int,
        *,
        expected_payload_hash: str,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        session=None,
    ) -> dict[str, Any]:
        tenant_id, actor_id = _require_tenant_and_actor()
        if type(ttl_seconds) is not int or not 60 <= ttl_seconds <= MAX_SCHEDULED_TTL_SECONDS:
            raise CommandDenied("Approval TTL must be between 60 seconds and 24 hours")
        if session is None:
            async with async_session_maker() as owned, owned.begin():
                return await self.approve_command(
                    command_id,
                    expected_payload_hash=expected_payload_hash,
                    ttl_seconds=ttl_seconds,
                    session=owned,
                )

        cmd = (
            await session.execute(
                select(BusinessCommand)
                .where(BusinessCommand.tenant_id == tenant_id, BusinessCommand.id == command_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if cmd is None:
            raise CommandNotFound("Command not found")
        spec = get_capability(cmd.capability)
        await _verify_member_grant(session, tenant_id, actor_id, spec)
        await _verify_connection(session, tenant_id, cmd.connection_id)

        current_digest = canonical_payload_hash(cmd.capability, list(cmd.target_refs), dict(cmd.payload))
        if current_digest != cmd.payload_hash or expected_payload_hash != current_digest:
            raise CommandDenied("Payload hash mismatch; modified commands require a new preview and approval")
        if cmd.state not in {"previewed", "approved"}:
            raise CommandConflict(f"Cannot approve command in state {cmd.state}")

        now = datetime.now(timezone.utc)
        cmd.state = "approved"
        cmd.approved_user_id = actor_id
        cmd.approved_payload_hash = current_digest
        cmd.approved_at = now
        cmd.approval_expires_at = now + timedelta(seconds=ttl_seconds)
        cmd.updated_at = now
        await session.flush()

        await ActionAuditEvent.objects.record_success(
            session=session,
            actor_kind="user",
            actor_id=actor_id,
            action="commands.approve",
            resource_kind="command",
            resource_id=cmd.id,
        )
        targets = await self._load_targets(session, tenant_id, cmd.id)
        return serialize_command(cmd, targets)

    async def reject_command(self, command_id: int, *, session=None) -> dict[str, Any]:
        tenant_id, actor_id = _require_tenant_and_actor()
        if session is None:
            async with async_session_maker() as owned, owned.begin():
                return await self.reject_command(command_id, session=owned)

        cmd = (
            await session.execute(
                select(BusinessCommand)
                .where(BusinessCommand.tenant_id == tenant_id, BusinessCommand.id == command_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if cmd is None:
            raise CommandNotFound("Command not found")
        spec = get_capability(cmd.capability)
        await _verify_member_grant(session, tenant_id, actor_id, spec)
        if cmd.state not in {"draft", "previewed", "approved"}:
            raise CommandConflict(f"Cannot cancel command in state {cmd.state}")

        now = datetime.now(timezone.utc)
        cmd.state = "cancelled"
        cmd.updated_at = now
        targets = await self._load_targets(session, tenant_id, cmd.id)
        for target in targets:
            if target.state == "pending":
                target.state = "cancelled"
                target.updated_at = now
        await session.flush()
        return serialize_command(cmd, targets)

    async def execute_command(
        self,
        command_id: int,
        *,
        adapter: AdapterCallable | None = None,
        now: datetime | None = None,
        session=None,
    ) -> dict[str, Any]:
        tenant_id, actor_id = _require_tenant_and_actor()
        now = now or datetime.now(timezone.utc)
        if session is None:
            async with async_session_maker() as owned, owned.begin():
                return await self.execute_command(command_id, adapter=adapter, now=now, session=owned)

        cmd = (
            await session.execute(
                select(BusinessCommand)
                .where(BusinessCommand.tenant_id == tenant_id, BusinessCommand.id == command_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if cmd is None:
            raise CommandNotFound("Command not found")

        spec = get_capability(cmd.capability)
        # Outbox transports have a separate pre-I/O durable claim transaction.
        # Never send or manufacture a success via this generic adapter path.
        if cmd.capability in {
            "comms.email.send.v1",
            "comms.telegram.bot_send.v1",
            "comms.max.bot_send.v1",
            "calendar.event_write.v1",
        }:
            raise CommandDenied(
                "Calendar commands execute only through the persistent calendar dispatcher"
                if cmd.capability == "calendar.event_write.v1"
                else "Transport commands execute only through the persistent outbox"
            )
        # Fail-closed rechecks at execution time: membership, role, capability, kill switch, connection, TTL, hash.
        await _verify_member_grant(session, tenant_id, actor_id, spec)
        if cmd.approved_user_id is None:
            raise CommandDenied("Command has no recorded approver")
        await _verify_member_grant(session, tenant_id, cmd.approved_user_id, spec)
        if not _is_capability_enabled(spec):
            cmd.error_code = "capability_disabled_or_killed"
            raise CommandDenied("Capability is disabled or blocked by kill switch")
        await _verify_connection(session, tenant_id, cmd.connection_id)

        if cmd.state == "claimed" or cmd.state in {"succeeded", "unknown"}:
            raise CommandConflict("Command was already claimed or executed")
        if cmd.state != "approved":
            raise CommandConflict(f"Command must be approved before execution (current: {cmd.state})")

        if cmd.approval_expires_at is None or now >= cmd.approval_expires_at:
            cmd.state = "expired"
            cmd.error_code = "approval_expired"
            cmd.updated_at = now
            await session.flush()
            raise CommandDenied("Command approval TTL has expired")

        current_digest = canonical_payload_hash(cmd.capability, list(cmd.target_refs), dict(cmd.payload))
        if current_digest != cmd.payload_hash or cmd.approved_payload_hash != current_digest:
            cmd.error_code = "payload_tampered"
            raise CommandDenied("Command payload changed after approval")

        targets = await self._load_targets(session, tenant_id, cmd.id)
        cmd.state = "claimed"
        cmd.claimed_at = now
        cmd.updated_at = now
        for target in targets:
            target.state = "claimed"
            target.updated_at = now
        await session.flush()

        # Execute adapter once (no automatic retries).
        try:
            if adapter is not None:
                receipts = await adapter(cmd, targets)
            else:
                receipts = {t.target_ref: f"receipt:{cmd.id}:{t.id}" for t in targets}
        except (asyncio.TimeoutError, TimeoutError) as exc:
            cmd.state = "unknown"
            cmd.error_code = "adapter_timeout_unknown"
            cmd.reconcile_hint = (
                "Verify provider state by target_idempotency_key before reconciling; never blind-resend."
            )
            cmd.updated_at = now
            for target in targets:
                target.state = "unknown"
                target.error_code = "adapter_timeout_unknown"
                target.updated_at = now
            await session.flush()
            await ActionAuditEvent.objects.record_unknown(
                actor_kind="user",
                actor_id=actor_id,
                action="commands.execute",
                resource_kind="command",
                resource_id=cmd.id,
                reason_code="operation_unconfirmed",
            )
            raise CommandTimeoutUnknown("External write outcome is unknown after timeout") from exc
        except Exception:
            cmd.state = "failed"
            cmd.error_code = "adapter_execution_failed"
            cmd.executed_at = now
            cmd.updated_at = now
            for target in targets:
                target.state = "failed"
                target.error_code = "adapter_execution_failed"
                target.executed_at = now
                target.updated_at = now
            await session.flush()
            return serialize_command(cmd, targets)

        cmd.state = "succeeded"
        cmd.executed_at = now
        cmd.updated_at = now
        cmd.error_code = None
        cmd.outcome_summary = {"targets_succeeded": len(targets), "targets_total": len(targets)}
        for target in targets:
            target.state = "succeeded"
            target.provider_receipt = str((receipts or {}).get(target.target_ref, f"receipt:{cmd.id}:{target.id}"))[
                :160
            ]
            target.executed_at = now
            target.updated_at = now
        await session.flush()

        await ActionAuditEvent.objects.record_success(
            session=session,
            actor_kind="user",
            actor_id=actor_id,
            action="commands.execute",
            resource_kind="command",
            resource_id=cmd.id,
        )
        return serialize_command(cmd, targets)

    async def reconcile_unknown(
        self,
        command_id: int,
        *,
        resolved_state: str,
        provider_receipts: dict[str, str] | None = None,
        session=None,
    ) -> dict[str, Any]:
        """Settle an `unknown` command after out-of-band provider verification."""
        tenant_id, actor_id = _require_tenant_and_actor()
        if resolved_state not in {"succeeded", "failed"}:
            raise CommandDenied("Reconciliation must resolve to succeeded or failed")
        if session is None:
            async with async_session_maker() as owned, owned.begin():
                return await self.reconcile_unknown(
                    command_id,
                    resolved_state=resolved_state,
                    provider_receipts=provider_receipts,
                    session=owned,
                )

        cmd = (
            await session.execute(
                select(BusinessCommand)
                .where(BusinessCommand.tenant_id == tenant_id, BusinessCommand.id == command_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if cmd is None:
            raise CommandNotFound("Command not found")
        spec = get_capability(cmd.capability)
        await _verify_member_grant(session, tenant_id, actor_id, spec)
        if cmd.state != "unknown":
            raise CommandConflict("Only commands in unknown state can be reconciled")

        now = datetime.now(timezone.utc)
        cmd.state = resolved_state
        cmd.executed_at = now
        cmd.updated_at = now
        cmd.reconcile_hint = f"Reconciled by user {actor_id} as {resolved_state}"
        targets = await self._load_targets(session, tenant_id, cmd.id)
        for target in targets:
            target.state = resolved_state
            if resolved_state == "succeeded":
                target.provider_receipt = (provider_receipts or {}).get(target.target_ref, f"reconciled:{cmd.id}")[:160]
                target.error_code = None
            else:
                target.error_code = "reconciled_failed"
            target.executed_at = now
            target.updated_at = now
        await session.flush()

        if resolved_state == "succeeded":
            await ActionAuditEvent.objects.record_success(
                session=session,
                actor_kind="user",
                actor_id=actor_id,
                action="commands.execute",
                resource_kind="command",
                resource_id=cmd.id,
            )
        return serialize_command(cmd, targets)

    async def get_command(self, command_id: int, *, session=None) -> dict[str, Any]:
        tenant_id, actor_id = _require_tenant_and_actor()
        if session is None:
            async with async_session_maker() as owned:
                return await self.get_command(command_id, session=owned)

        cmd = (
            await session.execute(
                select(BusinessCommand).where(BusinessCommand.tenant_id == tenant_id, BusinessCommand.id == command_id)
            )
        ).scalar_one_or_none()
        if cmd is None:
            raise CommandNotFound("Command not found")
        spec = get_capability(cmd.capability)
        await _verify_member_grant(session, tenant_id, actor_id, spec)
        targets = await self._load_targets(session, tenant_id, cmd.id)
        return serialize_command(cmd, targets)

    async def list_commands(self, *, limit: int = 50, before_id: int | None = None, session=None) -> dict[str, Any]:
        tenant_id, _actor_id = _require_tenant_and_actor()
        if type(limit) is not int or not 1 <= limit <= 100:
            raise CommandDenied("Limit must be between 1 and 100")
        if session is None:
            async with async_session_maker() as owned:
                return await self.list_commands(limit=limit, before_id=before_id, session=owned)

        conditions = [BusinessCommand.tenant_id == tenant_id]
        if before_id is not None:
            conditions.append(BusinessCommand.id < before_id)
        rows = list(
            (
                await session.execute(
                    select(BusinessCommand).where(*conditions).order_by(BusinessCommand.id.desc()).limit(limit)
                )
            )
            .scalars()
            .all()
        )
        items = [serialize_command(row) for row in rows]
        next_before = rows[-1].id if len(rows) == limit else None
        return {"items": items, "limit": limit, "next_before_id": next_before}

    @staticmethod
    async def _load_targets(session, tenant_id: int, command_id: int) -> list[BusinessCommandTarget]:
        return list(
            (
                await session.execute(
                    select(BusinessCommandTarget)
                    .where(
                        BusinessCommandTarget.tenant_id == tenant_id,
                        BusinessCommandTarget.command_id == command_id,
                    )
                    .order_by(BusinessCommandTarget.id)
                )
            )
            .scalars()
            .all()
        )


command_ledger = BusinessCommandLedger()
