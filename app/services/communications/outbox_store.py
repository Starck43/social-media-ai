"""Transactional outbox shell; the P12 functions remain the transition authority.

Content/destination snapshots are immutable: change intent by creating a new draft.
Claims are committed in a separate transaction BEFORE provider I/O. A lost worker
never makes a target eligible again, including a crash before the provider call.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.channels.senders.contracts import EMAIL, EmailRequest, MailAccount, SendBinding, SendResult
from app.core.access_catalog import workspace_grants
from app.core.database import async_session_maker, with_db_session
from app.core.permissions import get_current_user
from app.core.tenant_context import current_tenant_id, is_bypass
from app.integrations.email.smtp_send import send_email
from app.models import (
    Contact,
    ContactConsent,
    ContactIdentity,
    Tenant,
    TenantUser,
    User,
    WorkspaceConnection,
    WorkspaceConnectionSecret,
    WorkspaceRole,
)
from app.models.actions.business_command import BusinessCommand, BusinessCommandTarget
from app.models.communications.outbox import OutboxMessage, OutboxTarget
from app.services.commands.ledger import _is_capability_enabled, command_ledger
from app.services.commands.registry import canonical_payload_hash, get_capability
from app.services.communications import outbox as transitions
from app.services.communications.followups import adjust_for_quiet_hours
from app.services.communications.outbox_contracts import DraftIn, ScheduleIn
from app.types import ActionType
from app.utils.crypto import decrypt_secret

UTC = timezone.utc
CLAIM_GRACE = timedelta(minutes=5)
CAPABILITIES = {"email": EMAIL, "telegram": "comms.telegram.bot_send.v1", "max": "comms.max.bot_send.v1"}


class OutboxUnavailable(LookupError):
    """Opaque absent/foreign reference."""


class OutboxConflict(ValueError):
    """Immutable intent, expired approval or invalid transition."""


def digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def instant(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise OutboxConflict("aware_instant_required")
    return value.astimezone(UTC)


def due_after_quiet_hours(value, zone):
    return adjust_for_quiet_hours(instant(value).astimezone(ZoneInfo(zone))).astimezone(UTC)


def content_digest(message, targets):
    # Exact JSON, not the legacy stripped/lowercased pure hash alone. Newlines,
    # whitespace and case in recipient/content/credential identity remain bound.
    return digest(
        {
            "author": message.author_id,
            "contact": message.contact_id,
            "subject": message.subject_encrypted or "",
            "body": message.content_encrypted,
            "purpose": message.purpose,
            "timezone": message.timezone,
            "targets": [
                {
                    "channel": t.channel,
                    "connection": t.connection_id,
                    "identity": t.identity_id,
                    "recipient": t.recipient_encrypted,
                    "credential": t.credential_hash,
                }
                for t in targets
            ],
        }
    )


def approval_digest(message):
    return digest({"content_hash": message.content_hash, "scheduled_at": instant(message.scheduled_at).isoformat()})


def target_item(message, target):
    item = transitions.create_outbox_item(
        str(target.id),
        target.channel,
        "",
        target.recipient_encrypted,
        message.subject_encrypted or "",
        message.content_encrypted,
        route_verified=True,
    )
    item.update(
        state=target.state,
        content_hash=target.content_hash,
        bound_actor=str(message.approved_by),
        ttl_expires_at=message.approval_expires_at.isoformat() if message.approval_expires_at else None,
        attempts_count=target.attempts,
    )
    return item


def rollup(message, targets):
    states = {target.state for target in targets}
    if "approved" in states:
        message.state = "approved"
    elif "claimed" in states:
        message.state = "claimed"
    elif "uncertain_delivery" in states:
        message.state = "uncertain_delivery"
    elif states == {"sent"}:
        message.state = "sent"
    elif states == {"cancelled"}:
        message.state = "cancelled"
    elif "failed" in states:
        message.state = "failed"
    else:
        message.state = "validated"


def projection(message, targets):
    return {
        "id": message.id,
        "author_id": message.author_id,
        "contact_id": message.contact_id,
        "subject": message.subject_encrypted or "",
        "body": message.content_encrypted,
        "content_hash": message.content_hash,
        "state": message.state,
        "timezone": message.timezone,
        "scheduled_at": message.scheduled_at,
        "approval_expires_at": message.approval_expires_at,
        "targets": [
            {
                "id": t.id,
                "channel": t.channel,
                "connection_id": t.connection_id,
                "identity_id": t.identity_id,
                "recipient": t.recipient_encrypted,
                "state": t.state,
                "attempts": t.attempts,
                "command_id": t.command_id,
                "provider_message_id": t.provider_message_id,
                "error_code": t.error_code,
            }
            for t in targets
        ],
    }


async def actor_authority(session, tenant, actor_id, action, *, serialize=False):
    """Re-read authorization under revocation locks; no platform/service bypass."""
    if is_bypass() or type(tenant) is not int or tenant <= 0 or type(actor_id) is not int or actor_id <= 0:
        raise PermissionError("outbox_denied")
    user = await session.scalar(
        select(User.id).where(User.id == actor_id, User.is_active.is_(True)).with_for_update(read=True)
    )
    workspace = await session.scalar(
        select(Tenant.id).where(Tenant.id == tenant, Tenant.is_active.is_(True)).with_for_update(read=not serialize)
    )
    member = await session.scalar(
        select(TenantUser)
        .where(TenantUser.tenant_id == tenant, TenantUser.user_id == actor_id, TenantUser.is_active.is_(True))
        .with_for_update(read=True)
    )
    role = (
        None
        if member is None
        else await session.scalar(
            select(WorkspaceRole.codename).where(WorkspaceRole.id == member.role_id).with_for_update(read=True)
        )
    )
    grants = workspace_grants(role)
    if (
        user is None
        or workspace is None
        or any(("outboxmessage", required) not in grants for required in (ActionType.VIEW, action))
    ):
        raise PermissionError("outbox_denied")


async def current_actor(session, action, *, serialize=False):
    actor, tenant = get_current_user(), current_tenant_id()
    if actor is None or not actor.is_active:
        raise PermissionError("outbox_denied")
    await actor_authority(session, tenant, actor.id, action, serialize=serialize)
    return tenant, actor.id


async def targets_for(session, message):
    return list(
        (
            await session.scalars(
                select(OutboxTarget)
                .where(OutboxTarget.tenant_id == message.tenant_id, OutboxTarget.message_id == message.id)
                .order_by(OutboxTarget.id)
            )
        ).all()
    )


async def owned_message(session, tenant, message_id, *, lock=False):
    query = select(OutboxMessage).where(OutboxMessage.tenant_id == tenant, OutboxMessage.id == message_id)
    row = await session.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise OutboxUnavailable("outbox_unavailable")
    return row


async def resolve_route(session, message, target, now, *, consent=True):
    """Resolve fresh DB authority, never caller-provided flags or installation tokens.

    Bot sends deliberately fail closed: dev has neither customer-owned bot secret
    storage nor persisted same-bot start evidence. Numeric IDs or ContactConsent
    prose must not be promoted to started_bot=True (P15-T06 accepted contract).
    """
    if target.channel != "email":
        raise OutboxConflict("bot_evidence_unavailable")
    tenant = message.tenant_id
    contact = await session.scalar(
        select(Contact)
        .where(Contact.tenant_id == tenant, Contact.id == message.contact_id, Contact.merged_into_id.is_(None))
        .with_for_update()
    )
    identity = await session.scalar(
        select(ContactIdentity)
        .where(
            ContactIdentity.tenant_id == tenant,
            ContactIdentity.contact_id == message.contact_id,
            ContactIdentity.id == target.identity_id,
        )
        .with_for_update(read=True)
    )
    if (
        contact is None
        or identity is None
        or identity.kind != "email"
        or not identity.verified
        or identity.connection_id not in (None, target.connection_id)
    ):
        raise OutboxUnavailable("outbox_route_unavailable")
    connection = await session.scalar(
        select(WorkspaceConnection)
        .where(
            WorkspaceConnection.tenant_id == tenant,
            WorkspaceConnection.id == target.connection_id,
            WorkspaceConnection.owner_user_id == message.author_id,
        )
        .with_for_update(read=True)
    )
    if (
        connection is None
        or not connection.is_active
        or connection.state != "verified"
        or connection.provider_id not in {"mailru_mail", "yandex_mail"}
        or connection.auth_method != "app_password"
    ):
        raise OutboxUnavailable("outbox_connection_unavailable")
    secret = await session.scalar(
        select(WorkspaceConnectionSecret)
        .where(WorkspaceConnectionSecret.connection_id == connection.id)
        .with_for_update(read=True)
    )
    if secret is None or not secret.secret_encrypted:
        raise OutboxUnavailable("outbox_secret_unavailable")
    credential_hash = digest(
        {"config": connection.config, "provider": connection.provider_id, "secret_ciphertext": secret.secret_encrypted}
    )
    if consent:
        # Contact row lock serializes inserts/withdrawals through ContactManager.
        rows = list(
            (
                await session.scalars(
                    select(ContactConsent)
                    .where(
                        ContactConsent.tenant_id == tenant,
                        ContactConsent.contact_id == contact.id,
                        ContactConsent.channel == target.channel,
                        ContactConsent.purpose == message.purpose,
                    )
                    .order_by(ContactConsent.id.desc())
                    .with_for_update(read=True)
                )
            ).all()
        )
        # A future grant is not consent now, and a withdrawal must not be ignored.
        events = []
        for row in rows:
            if row.granted_at is not None and instant(row.granted_at) <= now:
                events.append((instant(row.granted_at), row.id, False))
            if row.withdrawn_at is not None and instant(row.withdrawn_at) <= now:
                events.append((instant(row.withdrawn_at), row.id, True))
        if not events or max(events)[2]:
            raise PermissionError("outbox_consent_required")
    return identity.value_encrypted, credential_hash, connection, secret


async def verify_snapshot(session, message, target, now):
    recipient, credential, connection, secret = await resolve_route(session, message, target, now)
    if recipient != target.recipient_encrypted or credential != target.credential_hash:
        raise OutboxConflict("outbox_route_changed")
    return connection, secret


class OutboxStore:
    @with_db_session
    async def draft(self, request: DraftIn, idempotency_key, session):
        request = DraftIn.model_validate(request)
        if not isinstance(idempotency_key, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", idempotency_key) is None:
            raise ValueError("invalid_idempotency_key")
        # Tenant lock serializes competing inserts; DB unique key is the final fence.
        tenant, actor = await current_actor(session, ActionType.UPDATE, serialize=True)
        request_hash = digest(request.model_dump(mode="json"))
        existing = await session.scalar(
            select(OutboxMessage).where(
                OutboxMessage.tenant_id == tenant,
                OutboxMessage.author_id == actor,
                OutboxMessage.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise OutboxConflict("outbox_idempotency_mismatch")
            return projection(existing, await targets_for(session, existing))
        message = OutboxMessage(
            tenant_id=tenant,
            author_id=actor,
            contact_id=request.contact_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            subject_encrypted=request.subject or None,
            content_encrypted=request.body,
            content_hash="",
            state="validated",
            purpose=request.purpose,
            timezone=request.timezone,
        )
        targets = []
        now = datetime.now(UTC)
        for index, requested in enumerate(request.targets):
            target = OutboxTarget(
                tenant_id=tenant,
                **requested.model_dump(),
                attempts=0,
                state="validated",
                idempotency_key=digest([tenant, actor, idempotency_key, index])[7:],
            )
            recipient, credential, _, _ = await resolve_route(session, message, target, now, consent=False)
            target.recipient_encrypted, target.credential_hash = recipient, credential
            target.content_hash = transitions.compute_message_hash(
                recipient, target.channel, request.subject, request.body
            )
            targets.append(target)
        message.content_hash = content_digest(message, targets)
        session.add(message)
        await session.flush()
        for target in targets:
            target.message_id = message.id
            session.add(target)
        await session.flush()
        return projection(message, targets)

    @with_db_session
    async def schedule(self, request: ScheduleIn, session):
        request = ScheduleIn.model_validate(request)
        # Explicit CONFIGURE, never inherited from UPDATE or an approval status.
        tenant, actor = await current_actor(session, ActionType.CONFIGURE)
        message = await owned_message(session, tenant, request.message_id, lock=True)
        # Until reply provenance is integrated, a marker is a denial, not consent.
        # Reject even idempotent schedules rather than accepting missing evidence.
        if message.reply_evidence_required:
            raise OutboxConflict("outbox_reply_not_implemented")
        targets = await targets_for(session, message)
        now = datetime.now(UTC)
        scheduled = due_after_quiet_hours(request.scheduled_at, message.timezone)
        if (
            message.author_id != actor
            or message.content_hash != request.expected_content_hash
            or message.content_hash != content_digest(message, targets)
        ):
            raise OutboxConflict("outbox_intent_mismatch")
        if message.state == "approved" and message.scheduled_at == scheduled:
            return projection(message, targets)  # Idempotent; never extends TTL.
        if message.state != "validated" or any(t.state != "validated" or t.attempts for t in targets):
            raise OutboxConflict("outbox_not_schedulable")
        expires = now + timedelta(seconds=request.ttl_seconds)
        if scheduled < now or scheduled >= expires:
            raise OutboxConflict("outbox_schedule_outside_approval")
        message.scheduled_at, message.approved_by, message.approval_expires_at = scheduled, actor, expires
        message.approved_hash = approval_digest(message)
        for target in targets:
            await verify_snapshot(session, message, target, now)
            approved = transitions.approve_outbox_item(target_item(message, target), str(actor), expires.isoformat())
            capability = CAPABILITIES[target.channel]
            # Ledger contains hashes/references only, never body, subject or recipient.
            payload = {"outbox_id": message.id, "target_id": target.id, "intent_hash": message.approved_hash}
            command = await command_ledger.preview_command(
                capability=capability,
                idempotency_key=target.idempotency_key,
                target_refs=[f"outbox:{target.id}"],
                payload=payload,
                connection_id=target.connection_id,
                preview_snapshot={"outbox_id": message.id, "targets_count": 1},
                session=session,
            )
            await command_ledger.approve_command(
                command["id"],
                expected_payload_hash=command["payload_hash"],
                ttl_seconds=request.ttl_seconds,
                session=session,
            )
            target.command_id, target.state = command["id"], approved["state"]
            target.dispatch_after = scheduled
        rollup(message, targets)
        await session.flush()
        return projection(message, targets)

    @with_db_session
    async def get(self, message_id, session):
        tenant, _ = await current_actor(session, ActionType.VIEW)
        message = await owned_message(session, tenant, message_id)
        return projection(message, await targets_for(session, message))

    @with_db_session
    async def scheduled(self, session, *, limit=50, before_id=None):
        tenant, _ = await current_actor(session, ActionType.VIEW)
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or (before_id is not None and (type(before_id) is not int or before_id <= 0))
        ):
            raise ValueError("invalid_page")
        query = select(OutboxMessage).where(
            OutboxMessage.tenant_id == tenant, OutboxMessage.state.in_(("approved", "claimed"))
        )
        if before_id is not None:
            query = query.where(OutboxMessage.id < before_id)
        rows = list((await session.scalars(query.order_by(OutboxMessage.id.desc()).limit(limit + 1))).all())
        return {
            "items": [projection(m, await targets_for(session, m)) for m in rows[:limit]],
            "limit": limit,
            "next_before_id": rows[limit - 1].id if len(rows) > limit else None,
        }

    @with_db_session
    async def cancel(self, message_id, session):
        tenant, actor = await current_actor(session, ActionType.UPDATE)
        message = await owned_message(session, tenant, message_id, lock=True)
        targets = await targets_for(session, message)
        if any(t.attempts or t.state in {"claimed", "sent", "uncertain_delivery", "failed"} for t in targets):
            raise OutboxConflict("outbox_already_claimed")
        for target in targets:
            target.state = transitions.cancel_outbox_item(target_item(message, target), str(actor))["state"]
            if target.command_id is not None:
                command, children = await command_rows(session, message, target)
                if command is None or command.state not in {"previewed", "approved", "cancelled"}:
                    raise OutboxConflict("outbox_command_already_claimed")
                command.state = "cancelled"
                for child in children:
                    child.state = "cancelled"
        rollup(message, targets)
        await session.flush()
        return projection(message, targets)


async def command_rows(session, message, target):
    command = await session.scalar(
        select(BusinessCommand)
        .where(BusinessCommand.tenant_id == message.tenant_id, BusinessCommand.id == target.command_id)
        .with_for_update()
    )
    rows = list(
        (
            await session.scalars(
                select(BusinessCommandTarget)
                .where(
                    BusinessCommandTarget.tenant_id == message.tenant_id,
                    BusinessCommandTarget.command_id == target.command_id,
                )
                .with_for_update()
            )
        ).all()
    )
    return command, rows


def verify_approval(message, targets, target, command, command_targets, now, *, claimed=False):
    # Invoked on both claim and immediately before provider I/O. No caller-set
    # marker can grant D15 while reply provenance is not yet wired through.
    if getattr(message, "reply_evidence_required", False):
        raise OutboxConflict("outbox_reply_not_implemented")
    state = "claimed" if claimed else "approved"
    expected_payload = {"outbox_id": message.id, "target_id": target.id, "intent_hash": message.approved_hash}
    if (
        message.approved_by != message.author_id
        or message.approval_expires_at is None
        or now >= instant(message.approval_expires_at)
        or message.content_hash != content_digest(message, targets)
        or message.approved_hash != approval_digest(message)
        or target.state != state
        or target.attempts != int(claimed)
        or command is None
        or command.state != state
        or command.capability != CAPABILITIES[target.channel]
        or command.capability_version != 1
        or command.actor_user_id != message.author_id
        or command.approved_user_id != message.approved_by
        or command.connection_id != target.connection_id
        or command.payload != expected_payload
        or command.target_refs != [f"outbox:{target.id}"]
        or command.approval_expires_at is None
        or now >= instant(command.approval_expires_at)
        or len(command_targets) != 1
        or command_targets[0].target_ref != f"outbox:{target.id}"
        or command_targets[0].state != ("claimed" if claimed else "pending")
    ):
        raise OutboxConflict("outbox_approval_invalid")
    canonical = canonical_payload_hash(command.capability, command.target_refs, command.payload)
    if command.payload_hash != canonical or command.approved_payload_hash != canonical:
        raise OutboxConflict("outbox_command_hash_mismatch")
    if target.content_hash != transitions.compute_message_hash(
        target.recipient_encrypted, target.channel, message.subject_encrypted or "", message.content_encrypted
    ):
        raise OutboxConflict("outbox_target_hash_mismatch")
    if not _is_capability_enabled(get_capability(command.capability)):
        raise PermissionError("outbox_capability_disabled")


async def persist_outcome(session, message, targets, target, result, now):
    # All unclassified failures are ambiguous; never turn an exception after I/O
    # into a retryable failed item. Only a typed definitive rejection is failed.
    if not isinstance(result, SendResult) or result.outcome not in {"sent", "unknown", "blocked", "rejected"}:
        result = SendResult("unknown", "outbox_invalid_result")
    item = target_item(message, target)
    if result.outcome == "sent":
        state = transitions.confirm_delivery(item, result.receipt_id or result.message_id)["state"]
    elif result.outcome == "unknown":
        state = transitions.timeout_delivery(item, "outcome_unconfirmed")["state"]
    else:
        state = transitions.fail_delivery(item)["state"]
    target.state = state
    # No provider exception text or content. Sender codes are code-owned tokens.
    target.error_code = result.code if re.fullmatch(r"[a-z0-9_]{1,64}", result.code or "") else "outbox_unclassified"
    target.provider_message_id = (
        (result.receipt_id or result.message_id)[:255]
        if state == "sent" and (result.receipt_id or result.message_id)
        else None
    )
    command, children = await command_rows(session, message, target)
    if command is not None:
        command.state = {"sent": "succeeded", "uncertain_delivery": "unknown", "failed": "failed"}[state]
        command.executed_at, command.error_code = now, target.error_code
        for child in children:
            child.state, child.executed_at = command.state, now
            child.error_code = target.error_code
            child.provider_receipt = target.provider_message_id[:160] if target.provider_message_id else None
    rollup(message, targets)
    await session.flush()


class OutboxDispatcher:
    def __init__(self, *, session_factory=async_session_maker, sender=send_email):
        self.session_factory, self.sender = session_factory, sender

    @staticmethod
    def tenant():
        tenant = current_tenant_id()
        if is_bypass() or type(tenant) is not int or tenant <= 0:
            raise PermissionError("outbox_workspace_required")
        return tenant

    async def claim(self, now):
        tenant = self.tenant()
        async with self.session_factory() as session, session.begin():
            # Parent serializes cancellation and the whole intent snapshot. Other
            # workers skip this message, not another worker's in-flight target.
            query = (
                select(OutboxMessage)
                .join(
                    OutboxTarget,
                    (OutboxTarget.tenant_id == OutboxMessage.tenant_id) & (OutboxTarget.message_id == OutboxMessage.id),
                )
                .where(
                    OutboxMessage.tenant_id == tenant,
                    OutboxTarget.state == "approved",
                    OutboxTarget.dispatch_after <= now,
                    OutboxMessage.scheduled_at <= now,
                )
                .order_by(OutboxMessage.scheduled_at, OutboxMessage.id)
                .with_for_update(of=OutboxMessage, skip_locked=True)
                .limit(1)
            )
            message = await session.scalar(query)
            if message is None:
                return None
            targets = await targets_for(session, message)
            target = next((t for t in targets if t.state == "approved" and t.dispatch_after <= now), None)
            if target is None:
                return None
            # Quiet hours can also begin after the scheduled time (worker outage).
            deferred = due_after_quiet_hours(now, message.timezone)
            if deferred > now:
                # Do not mutate the approved schedule/hash; defer admission only.
                target.dispatch_after = deferred
                return {"deferred": True}
            command, children = await command_rows(session, message, target)
            try:
                await actor_authority(session, tenant, message.author_id, ActionType.CONFIGURE)
                verify_approval(message, targets, target, command, children, now)
                await verify_snapshot(session, message, target, now)
                claimed = transitions.claim_outbox_item(target_item(message, target), now.isoformat())
            except (PermissionError, OutboxConflict, OutboxUnavailable, transitions.CommunicationError):
                target.state = transitions.reject_approved_item(target_item(message, target))["state"]
                target.error_code = "outbox_admission_denied"
                if command is not None and command.state == "approved":
                    command.state, command.error_code = "failed", target.error_code
                    for child in children:
                        child.state, child.error_code = "failed", target.error_code
                rollup(message, targets)
                return {"denied": True}
            target.state, target.attempts = claimed["state"], claimed["attempts_count"]
            target.claimed_at, target.claim_token = now, str(uuid4())
            command.state, command.claimed_at = "claimed", now
            for child in children:
                child.state = "claimed"
            rollup(message, targets)
            await session.flush()
            ticket = (message.id, target.id, target.claim_token)
        # Successful transaction exit is mandatory. No caller-owned session can
        # accidentally defer this commit until after a provider side effect.
        return ticket

    async def deliver(self, ticket, now):
        tenant = self.tenant()
        message_id, target_id, token = ticket
        # Durably consume the ticket before I/O, separately from outcome commit.
        # Without this fence the original ticket could replay after a provider ACK
        # followed by a database commit failure. A lost consumed ticket is unknown,
        # never permission to send again, even within CLAIM_GRACE.
        async with self.session_factory() as session, session.begin():
            message = await owned_message(session, tenant, message_id, lock=True)
            targets = await targets_for(session, message)
            target = next((t for t in targets if t.id == target_id), None)
            if (
                target is None
                or target.state != "claimed"
                or target.claim_token != token
                or target.delivery_started_at is not None
            ):
                return False
            target.delivery_started_at = now
            await session.flush()
        async with self.session_factory() as session, session.begin():
            message = await owned_message(session, tenant, message_id, lock=True)
            targets = await targets_for(session, message)
            target = next((t for t in targets if t.id == target_id), None)
            if target is None or target.state != "claimed" or target.claim_token != token:
                return False
            if now - instant(target.claimed_at) >= CLAIM_GRACE:
                await persist_outcome(
                    session, message, targets, target, SendResult("unknown", "outbox_worker_lost"), now
                )
                return False
            command, children = await command_rows(session, message, target)
            try:
                await actor_authority(session, tenant, message.author_id, ActionType.CONFIGURE)
                verify_approval(message, targets, target, command, children, now, claimed=True)
                connection, secret = await verify_snapshot(session, message, target, now)
                account = MailAccount(
                    SendBinding(
                        tenant, connection.id, connection.owner_user_id, message.author_id, active=True, verified=True
                    ),
                    connection.provider_id,
                    connection.config.get("account_login", ""),
                    decrypt_secret(secret.secret_encrypted),
                    send_enabled=connection.config.get("send_enabled") is True,
                )
            except (PermissionError, OutboxConflict, OutboxUnavailable, ValueError, RuntimeError):
                result = SendResult("blocked", "outbox_authority_changed")
            else:
                # Locks span I/O to serialize connection/consent/actor revocation.
                # Cancellation propagates: the previously committed claim remains.
                try:
                    result = await self.sender(
                        account,
                        EmailRequest(
                            target.recipient_encrypted, message.subject_encrypted or "", message.content_encrypted
                        ),
                    )
                except Exception:
                    result = SendResult("unknown", "outbox_transport_unconfirmed")
            await persist_outcome(session, message, targets, target, result, now)
        return True

    async def recover(self, now, *, limit=100):
        tenant = self.tenant()
        async with self.session_factory() as session, session.begin():
            messages = list(
                (
                    await session.scalars(
                        select(OutboxMessage)
                        .where(
                            OutboxMessage.tenant_id == tenant,
                            OutboxMessage.id.in_(
                                select(OutboxTarget.message_id).where(
                                    OutboxTarget.tenant_id == tenant,
                                    OutboxTarget.state == "claimed",
                                    OutboxTarget.claimed_at <= now - CLAIM_GRACE,
                                )
                            ),
                        )
                        .order_by(OutboxMessage.id)
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            count = 0
            for message in messages:
                targets = await targets_for(session, message)
                for target in targets:
                    if target.state == "claimed" and instant(target.claimed_at) <= now - CLAIM_GRACE:
                        await persist_outcome(
                            session, message, targets, target, SendResult("unknown", "outbox_worker_lost"), now
                        )
                        count += 1
            return count

    async def run(self, *, limit=20, now=None):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid_dispatch_limit")
        fixed_clock = now is not None
        now = instant(now or datetime.now(UTC))
        recovered = await self.recover(now)
        handled = denied = deferred = 0
        for _ in range(limit):
            ticket = await self.claim(now)
            if ticket is None:
                break
            if isinstance(ticket, dict):
                denied += int(ticket.get("denied", False))
                deferred += int(ticket.get("deferred", False))
                continue
            handled += int(await self.deliver(ticket, now if fixed_clock else datetime.now(UTC)))
        return {
            "job": "comms.outbox.dispatch",
            "handled": handled,
            "denied": denied,
            "uncertain": recovered,
            "deferred": deferred,
        }


outbox_store = OutboxStore()
