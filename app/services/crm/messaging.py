"""Approved greeting delivery (P14-T05) and consented batch messaging with unsubscribe (P14-T06).

Invariants (Q-19 / D7 / D10 / D14 / D15 / D16 / D17):
- D10: Schedules strictly through the P15-T05 persistent outbox (`OutboxStore`) and P15-T06
  channel senders; never creates a second outbox or invokes transport senders directly.
- D7: Allowed delivery channels are strictly `("email", "telegram", "max")`. Phone, Bitrix24,
  and MTProto user-account bulk sends are rejected.
- D15: Both `greeting` and `marketing` purposes require an active, non-withdrawn `ContactConsent`
  row for `(tenant_id, contact_id, channel, purpose)`. A channel or identity imported on a
  Bitrix24 CRM card is a candidate only, never consent. Batch runs emit an explicit
  `coverage_warning` whenever contacts lack recorded consent.
- D16: Every greeting and marketing message includes a signed one-click unsubscribe link/command.
  Clicking the unsubscribe endpoint requires no login, records a `ContactConsent` withdrawal row
  for `(tenant_id, contact_id, channel, purpose)`, and cancels any unclaimed scheduled outbox
  messages for that contact/channel/purpose immediately.
- D17: Manual consent recording by a manager requires `contact.UPDATE`, non-empty `basis` and
  `source_evidence`, a timezone-aware timestamp, and writes an `ActionAuditEvent` naming the manager.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.tenant_context import current_tenant_id, is_bypass, tenant_scope
from app.integrations.bitrix24.client import Bitrix24Error
from app.models.actions.business_command import BusinessCommand, BusinessCommandTarget
from app.models.audit.action_audit_event import ActionAuditEvent
from app.models.communications.outbox import OutboxMessage, OutboxTarget
from app.models.contacts.core import Contact, ContactConsent, ContactIdentity
from app.models.crm.entity import CrmEntity
from app.models.crm.greeting_history import GreetingHistory
from app.models.managers.contact_manager import ContactManager, ContactUnavailable
from app.services.commands.registry import canonical_payload_hash
from app.services.communications import outbox as outbox_transitions
from app.services.communications.outbox_contracts import DraftIn, ScheduleIn, TargetIn
from app.services.communications.outbox_store import (
    OutboxConflict,
    OutboxUnavailable,
    instant,
    outbox_store,
)
from app.services.communications.outbox_store import rollup as outbox_rollup
from app.services.communications.outbox_store import target_item as outbox_target_item

UTC = timezone.utc
ALLOWED_DELIVERY_CHANNELS: tuple[str, ...] = ("email", "telegram", "max")
CHANNEL_TO_IDENTITY_KIND: dict[str, str] = {
    "email": "email",
    "telegram": "telegram_id",
    "max": "max_id",
}
ALLOWED_CAMPAIGN_PURPOSES: tuple[str, ...] = ("greeting", "marketing")
MAX_BATCH_CONTACTS = 500
UNSUBSCRIBE_DOMAIN_TAG = b"p14-t06-unsubscribe-v1"
_PLACEHOLDER_RE = re.compile(r"\{(name|company)\}")


def _unsubscribe_secret() -> bytes:
    raw = getattr(settings, "SECRET_KEY", "") or "fallback-secret-key"
    return raw.encode("utf-8")


def create_unsubscribe_token(
    *,
    tenant_id: int,
    contact_id: int,
    channel: str,
    purpose: str,
    issued_at: int | None = None,
) -> str:
    """Create a URL-safe HMAC-SHA256 token for one-click unsubscribe without login (D16)."""
    if type(tenant_id) is not int or tenant_id <= 0 or type(contact_id) is not int or contact_id <= 0:
        raise Bitrix24Error("invalid_unsubscribe_scope", status_code=422)
    if channel not in ALLOWED_DELIVERY_CHANNELS or purpose not in ALLOWED_CAMPAIGN_PURPOSES:
        raise Bitrix24Error("invalid_unsubscribe_scope", status_code=422)

    iat = int(issued_at if issued_at is not None else datetime.now(UTC).timestamp())
    payload_bytes = json.dumps(
        {"c": contact_id, "ch": channel, "iat": iat, "p": purpose, "t": tenant_id},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode("ascii").rstrip("=")
    signing_input = UNSUBSCRIBE_DOMAIN_TAG + b"." + payload_b64.encode("ascii")
    sig_bytes = hmac.new(_unsubscribe_secret(), signing_input, hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig_bytes).decode("ascii").rstrip("=")
    return f"{payload_b64}.{sig_b64}"


def verify_unsubscribe_token(token: str) -> dict[str, Any]:
    """Verify a one-click unsubscribe token and return `{tenant_id, contact_id, channel, purpose, issued_at}`."""
    if not isinstance(token, str) or "." not in token or len(token) > 1024:
        raise Bitrix24Error("invalid_unsubscribe_token", "Invalid unsubscribe token", status_code=422)
    payload_b64, sig_b64 = token.split(".", 1)
    if not payload_b64 or not sig_b64:
        raise Bitrix24Error("invalid_unsubscribe_token", "Invalid unsubscribe token", status_code=422)

    signing_input = UNSUBSCRIBE_DOMAIN_TAG + b"." + payload_b64.encode("ascii")
    expected_sig = (
        base64.urlsafe_b64encode(hmac.new(_unsubscribe_secret(), signing_input, hashlib.sha256).digest())
        .decode("ascii")
        .rstrip("=")
    )
    if not hmac.compare_digest(sig_b64, expected_sig):
        raise Bitrix24Error("invalid_unsubscribe_token", "Unsubscribe token signature mismatch", status_code=422)

    try:
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except Exception as exc:
        raise Bitrix24Error(
            "invalid_unsubscribe_token", "Malformed unsubscribe token payload", status_code=422
        ) from exc

    if not isinstance(data, dict):
        raise Bitrix24Error("invalid_unsubscribe_token", status_code=422)
    tenant_id = data.get("t")
    contact_id = data.get("c")
    channel = data.get("ch")
    purpose = data.get("p")
    issued_at = data.get("iat")
    if (
        type(tenant_id) is not int
        or tenant_id <= 0
        or type(contact_id) is not int
        or contact_id <= 0
        or channel not in ALLOWED_DELIVERY_CHANNELS
        or purpose not in ALLOWED_CAMPAIGN_PURPOSES
        or type(issued_at) is not int
    ):
        raise Bitrix24Error("invalid_unsubscribe_token", status_code=422)

    return {
        "tenant_id": tenant_id,
        "contact_id": contact_id,
        "channel": channel,
        "purpose": purpose,
        "issued_at": issued_at,
    }


def build_unsubscribe_url(token: str, *, base_url: str = "") -> str:
    """Build the public one-click unsubscribe URL for a token."""
    prefix = base_url.rstrip("/") if base_url else ""
    return f"{prefix}/api/v1/integrations/bitrix/messaging/unsubscribe?token={token}"


def append_unsubscribe_footer(body: str, *, unsubscribe_url: str, channel: str) -> str:
    """Append a channel-appropriate one-click unsubscribe link/command to message body (D7, D16)."""
    clean_body = (body or "").rstrip()
    if unsubscribe_url in clean_body:
        return clean_body
    if channel == "email":
        return f"{clean_body}\n\n---\nОтписаться от рассылки в один клик (без входа): {unsubscribe_url}"
    return f"{clean_body}\n\nОтписаться в один клик: {unsubscribe_url} (или отправьте /stop)"


def render_personalized_template(
    template: str,
    *,
    name: str | None,
    company: str | None = None,
    single_line: bool = False,
) -> str:
    """Render `{name}` and `{company}` in a single pass without recursive expansion."""
    if not isinstance(template, str) or "\x00" in template:
        raise Bitrix24Error("invalid_message_template", status_code=422)

    safe_name = (name or "").strip() or "Уважаемый клиент"
    safe_company = (company or "").strip()

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        return safe_name if key == "name" else safe_company

    rendered = _PLACEHOLDER_RE.sub(_replace, template)
    if single_line:
        rendered = rendered.replace("\r", " ").replace("\n", " ")
        rendered = re.sub(r"\s{2,}", " ", rendered).strip()
    return rendered


async def has_active_consent(
    session,
    *,
    tenant_id: int,
    contact_id: int,
    channel: str,
    purpose: str,
    now: datetime,
) -> bool:
    """Check whether `(tenant_id, contact_id, channel, purpose)` has an active, non-withdrawn opt-in (D15)."""
    rows = list(
        (
            await session.execute(
                select(ContactConsent)
                .where(
                    ContactConsent.tenant_id == tenant_id,
                    ContactConsent.contact_id == contact_id,
                    ContactConsent.channel == channel,
                    ContactConsent.purpose == purpose,
                )
                .order_by(ContactConsent.id.desc())
            )
        )
        .scalars()
        .all()
    )
    events: list[tuple[datetime, int, bool]] = []
    for row in rows:
        if row.granted_at is not None and instant(row.granted_at) <= now:
            events.append((instant(row.granted_at), row.id, False))
        if row.withdrawn_at is not None and instant(row.withdrawn_at) <= now:
            events.append((instant(row.withdrawn_at), row.id, True))
    return bool(events and not max(events)[2])


async def _resolve_contact_identity(
    session,
    *,
    tenant_id: int,
    contact_id: int,
    channel: str,
    connection_id: int,
    identity_id: int | None = None,
) -> ContactIdentity | None:
    expected_kind = CHANNEL_TO_IDENTITY_KIND.get(channel)
    if expected_kind is None:
        return None
    identities = list(
        (
            await session.execute(
                select(ContactIdentity)
                .where(
                    ContactIdentity.tenant_id == tenant_id,
                    ContactIdentity.contact_id == contact_id,
                )
                .order_by(ContactIdentity.id.asc())
            )
        )
        .scalars()
        .all()
    )
    for ident in identities:
        if ident.kind != expected_kind or not ident.verified:
            continue
        if identity_id is not None and ident.id != identity_id:
            continue
        if ident.connection_id not in (None, connection_id):
            continue
        return ident
    return None


def _map_outbox_state_to_greeting_status(state: str | None) -> str:
    if state in {"approved", "claimed", "validated"}:
        return "approved"
    if state == "sent":
        return "sent"
    if state == "failed":
        return "failed"
    if state == "uncertain_delivery":
        return "unknown"
    if state == "cancelled":
        return "skipped"
    return "draft"


class CrmMessagingService:
    """Service layer for P14-T05 greeting delivery and P14-T06 batch messaging, consent & unsubscribe."""

    def __init__(self, *, outbox=None) -> None:
        self._outbox = outbox or outbox_store

    async def record_manual_consent(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        contact_id: int,
        channel: str,
        purpose: str,
        action: Literal["grant", "withdraw"] = "grant",
        basis: str,
        source_evidence: str,
        occurred_at: datetime | None = None,
        session=None,
    ) -> dict[str, Any]:
        """Record manual opt-in or withdrawal by a manager with basis, evidence, and an audit row (D17)."""
        if is_bypass() or current_tenant_id() != tenant_id or type(actor_id) is not int or actor_id <= 0:
            raise Bitrix24Error("connection_denied", status_code=403)
        if channel not in ALLOWED_DELIVERY_CHANNELS:
            raise Bitrix24Error("invalid_channel", "Consent channel must be email, telegram, or max", status_code=422)
        if purpose not in ("transactional", "greeting", "marketing"):
            raise Bitrix24Error("invalid_purpose", status_code=422)
        if action not in ("grant", "withdraw"):
            raise Bitrix24Error("invalid_consent_action", status_code=422)

        if session is None:
            async with async_session_maker() as owned, owned.begin():
                return await self.record_manual_consent(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    contact_id=contact_id,
                    channel=channel,
                    purpose=purpose,
                    action=action,
                    basis=basis,
                    source_evidence=source_evidence,
                    occurred_at=occurred_at,
                    session=owned,
                )

        ts = instant(occurred_at or datetime.now(UTC))
        manager = ContactManager()
        try:
            row = await manager.record_consent(
                contact_id,
                channel,
                purpose,
                basis,
                source_evidence,
                session=session,
                granted_at=ts if action == "grant" else None,
                withdrawn_at=ts if action == "withdraw" else None,
            )
        except PermissionError as exc:
            raise Bitrix24Error("consent_permission_denied", status_code=403) from exc
        except ContactUnavailable as exc:
            raise Bitrix24Error("contact_not_found", status_code=404) from exc
        except ValueError as exc:
            raise Bitrix24Error("invalid_consent_input", str(exc), status_code=422) from exc

        audit_event = await ActionAuditEvent.objects.record_success(
            session=session,
            actor_kind="user",
            actor_id=actor_id,
            action="crm.contact_update",
            resource_kind="contact",
            resource_id=contact_id,
        )

        return {
            "id": row.id,
            "tenant_id": row.tenant_id,
            "contact_id": row.contact_id,
            "channel": row.channel,
            "purpose": row.purpose,
            "action": action,
            "granted_at": row.granted_at,
            "withdrawn_at": row.withdrawn_at,
            "recorded_by_user_id": actor_id,
            "audit_event_id": getattr(audit_event, "id", None),
        }

    async def process_one_click_unsubscribe(
        self,
        token: str,
        *,
        now: datetime | None = None,
        session=None,
    ) -> dict[str, Any]:
        """Process a one-click unsubscribe link without requiring login (D16)."""
        verified = verify_unsubscribe_token(token)
        tenant_id = verified["tenant_id"]
        contact_id = verified["contact_id"]
        channel = verified["channel"]
        purpose = verified["purpose"]
        effective_now = instant(now or datetime.now(UTC))

        if session is None:
            async with async_session_maker() as owned, owned.begin():
                return await self.process_one_click_unsubscribe(token, now=effective_now, session=owned)

        with tenant_scope(tenant_id):
            contact = (
                await session.execute(
                    select(Contact).where(
                        Contact.id == contact_id,
                        Contact.tenant_id == tenant_id,
                    )
                )
            ).scalar_one_or_none()
            if contact is None or contact.merged_into_id is not None:
                raise Bitrix24Error("contact_not_found", status_code=404)

            consent_row = ContactConsent(
                tenant_id=tenant_id,
                contact_id=contact.id,
                channel=channel,
                purpose=purpose,
                basis_encrypted="one_click_unsubscribe",
                source_evidence_encrypted="one_click_unsubscribe_token",
                granted_at=None,
                withdrawn_at=effective_now,
            )
            session.add(consent_row)

            # Immediately cancel any unclaimed scheduled outbox messages for this contact/channel/purpose.
            outbox_msgs = list(
                (
                    await session.execute(
                        select(OutboxMessage).where(
                            OutboxMessage.tenant_id == tenant_id,
                            OutboxMessage.contact_id == contact.id,
                            OutboxMessage.purpose == purpose,
                        )
                    )
                )
                .scalars()
                .all()
            )
            cancelled_targets = 0
            for msg in outbox_msgs:
                if msg.state not in {"validated", "approved"}:
                    continue
                targets = list(
                    (
                        await session.execute(
                            select(OutboxTarget).where(
                                OutboxTarget.tenant_id == tenant_id,
                                OutboxTarget.message_id == msg.id,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                changed = False
                for tgt in targets:
                    if tgt.channel == channel and tgt.state in {"validated", "approved"} and tgt.attempts == 0:
                        tgt.state = outbox_transitions.cancel_outbox_item(
                            outbox_target_item(msg, tgt), str(msg.author_id)
                        )["state"]
                        cancelled_targets += 1
                        changed = True
                        if tgt.command_id is not None:
                            cmd = (
                                await session.execute(
                                    select(BusinessCommand).where(
                                        BusinessCommand.tenant_id == tenant_id,
                                        BusinessCommand.id == tgt.command_id,
                                    )
                                )
                            ).scalar_one_or_none()
                            if cmd is not None and cmd.state in {"previewed", "approved"}:
                                cmd.state = "cancelled"
                            cmd_targets = list(
                                (
                                    await session.execute(
                                        select(BusinessCommandTarget).where(
                                            BusinessCommandTarget.tenant_id == tenant_id,
                                            BusinessCommandTarget.command_id == tgt.command_id,
                                        )
                                    )
                                )
                                .scalars()
                                .all()
                            )
                            for ct in cmd_targets:
                                if ct.state == "pending":
                                    ct.state = "cancelled"
                if changed and targets:
                    outbox_rollup(msg, targets)

            if purpose == "greeting":
                greetings = list(
                    (
                        await session.execute(
                            select(GreetingHistory).where(
                                GreetingHistory.tenant_id == tenant_id,
                                GreetingHistory.contact_id == contact.id,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                for g in greetings:
                    if g.channel == channel and g.status == "approved":
                        g.status = "skipped"

            await session.flush()
            await ActionAuditEvent.objects.record_success(
                session=session,
                actor_kind="service",
                actor_id="task_runner",
                action="crm.contact_update",
                resource_kind="contact",
                resource_id=contact.id,
            )

        return {
            "unsubscribed": True,
            "tenant_id": tenant_id,
            "contact_id": contact_id,
            "channel": channel,
            "purpose": purpose,
            "withdrawn_at": effective_now,
            "cancelled_outbox_targets": cancelled_targets,
            "message": "Вы успешно отписались от рассылки.",
        }

    async def send_greeting(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        connection_id: int,
        greeting_id: int | None = None,
        contact_id: int | None = None,
        event_type: str = "birthday",
        year: int | None = None,
        channel: str | None = None,
        identity_id: int | None = None,
        scheduled_at: datetime | None = None,
        ttl_seconds: int = 86400,
        timezone_name: str = "Europe/Moscow",
        idempotency_key: str | None = None,
        outbox=None,
        now: datetime | None = None,
        session=None,
    ) -> dict[str, Any]:
        """Schedule an approved greeting via the P15-T05 outbox after verifying `greeting` opt-in consent (P14-T05)."""
        if is_bypass() or current_tenant_id() != tenant_id or type(actor_id) is not int or actor_id <= 0:
            raise Bitrix24Error("connection_denied", status_code=403)
        if type(connection_id) is not int or connection_id <= 0:
            raise Bitrix24Error("invalid_connection_id", status_code=422)

        if session is None:
            async with async_session_maker() as owned, owned.begin():
                return await self.send_greeting(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    connection_id=connection_id,
                    greeting_id=greeting_id,
                    contact_id=contact_id,
                    event_type=event_type,
                    year=year,
                    channel=channel,
                    identity_id=identity_id,
                    scheduled_at=scheduled_at,
                    ttl_seconds=ttl_seconds,
                    timezone_name=timezone_name,
                    idempotency_key=idempotency_key,
                    outbox=outbox,
                    now=now,
                    session=owned,
                )

        effective_now = instant(now or datetime.now(UTC))
        eff_scheduled = instant(scheduled_at) if scheduled_at is not None else (effective_now + timedelta(minutes=1))

        # Locate the GreetingHistory draft
        row: GreetingHistory | None = None
        if greeting_id is not None:
            rows = list(
                (
                    await session.execute(
                        select(GreetingHistory).where(
                            GreetingHistory.tenant_id == tenant_id,
                            GreetingHistory.contact_id
                            == (contact_id if contact_id is not None else GreetingHistory.contact_id),
                        )
                    )
                )
                .scalars()
                .all()
            )
            row = next((r for r in rows if r.id == greeting_id), None)
        elif contact_id is not None:
            target_year = int(year) if year is not None else effective_now.year
            row = (
                await session.execute(
                    select(GreetingHistory).where(
                        GreetingHistory.tenant_id == tenant_id,
                        GreetingHistory.contact_id == contact_id,
                        GreetingHistory.event_type == event_type,
                        GreetingHistory.year == target_year,
                    )
                )
            ).scalar_one_or_none()

        if row is None:
            raise Bitrix24Error("greeting_not_found", status_code=404)
        if row.status == "sent":
            raise Bitrix24Error("greeting_already_processed", status_code=409)
        if row.status == "approved" and row.command_id is not None:
            raise Bitrix24Error("greeting_already_processed", status_code=409)

        chosen_channel = channel or row.channel or "email"
        if chosen_channel not in ALLOWED_DELIVERY_CHANNELS:
            raise Bitrix24Error(
                "invalid_channel",
                "Greeting delivery supports only email, telegram, or max channels (D7)",
                status_code=422,
            )

        contact = (
            await session.execute(
                select(Contact).where(
                    Contact.id == row.contact_id,
                    Contact.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        if contact is None or contact.merged_into_id is not None:
            raise Bitrix24Error("contact_not_found", status_code=404)

        # D15: Explicit opt-in consent for the `greeting` purpose is mandatory.
        consented = await has_active_consent(
            session,
            tenant_id=tenant_id,
            contact_id=contact.id,
            channel=chosen_channel,
            purpose="greeting",
            now=effective_now,
        )
        if not consented:
            raise Bitrix24Error(
                "consent_required",
                "Recorded opt-in consent for purpose 'greeting' is required before sending (D15)",
                status_code=403,
            )

        identity = await _resolve_contact_identity(
            session,
            tenant_id=tenant_id,
            contact_id=contact.id,
            channel=chosen_channel,
            connection_id=connection_id,
            identity_id=identity_id,
        )
        if identity is None:
            raise Bitrix24Error("recipient_identity_unavailable", status_code=422)

        unsub_token = create_unsubscribe_token(
            tenant_id=tenant_id,
            contact_id=contact.id,
            channel=chosen_channel,
            purpose="greeting",
            issued_at=int(effective_now.timestamp()),
        )
        unsub_url = build_unsubscribe_url(unsub_token)
        body_with_unsub = append_unsubscribe_footer(
            row.draft_text_encrypted,
            unsubscribe_url=unsub_url,
            channel=chosen_channel,
        )
        eff_idem_key = idempotency_key or f"greeting-{tenant_id}-{row.id}-{chosen_channel}"

        effective_outbox = outbox or self._outbox
        try:
            draft_res = await effective_outbox.draft(
                DraftIn(
                    contact_id=contact.id,
                    subject=row.subject_encrypted or "",
                    body=body_with_unsub,
                    purpose="greeting",
                    timezone=timezone_name,
                    targets=[
                        TargetIn(
                            channel=chosen_channel,  # type: ignore[arg-type]
                            connection_id=connection_id,
                            identity_id=identity.id,
                        )
                    ],
                ),
                eff_idem_key,
                session=session,
            )
            sched_res = await effective_outbox.schedule(
                ScheduleIn(
                    message_id=draft_res["id"],
                    expected_content_hash=draft_res["content_hash"],
                    scheduled_at=eff_scheduled,
                    ttl_seconds=ttl_seconds,
                ),
                session=session,
            )
        except PermissionError as exc:
            raise Bitrix24Error("outbox_permission_denied", status_code=403) from exc
        except OutboxUnavailable as exc:
            raise Bitrix24Error("outbox_route_unavailable", status_code=422) from exc
        except (OutboxConflict, outbox_transitions.CommunicationError, ValueError) as exc:
            raise Bitrix24Error(str(exc) or "outbox_conflict", status_code=409) from exc

        target_out = sched_res["targets"][0]
        row.channel = chosen_channel
        row.command_id = target_out.get("command_id")
        row.draft_text_encrypted = body_with_unsub
        row.status = _map_outbox_state_to_greeting_status(sched_res.get("state"))
        if row.status == "sent":
            row.sent_at = effective_now
        await session.flush()

        await ActionAuditEvent.objects.record_success(
            session=session,
            actor_kind="user",
            actor_id=actor_id,
            action="comms.campaign_approve",
            resource_kind="campaign",
            resource_id=row.id,
        )

        return {
            "id": row.id,
            "tenant_id": row.tenant_id,
            "contact_id": row.contact_id,
            "event_type": row.event_type,
            "event_date": row.event_date,
            "year": row.year,
            "channel": row.channel,
            "status": row.status,
            "command_id": row.command_id,
            "outbox_message_id": sched_res["id"],
            "outbox_target_id": target_out["id"],
            "outbox_state": sched_res["state"],
            "scheduled_at": sched_res.get("scheduled_at"),
            "unsubscribe_url": unsub_url,
            "send_enabled": True,
        }

    async def get_greeting_delivery_status(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        greeting_id: int,
        session=None,
    ) -> dict[str, Any]:
        """Synchronize and return greeting delivery status from the P15-T05 outbox."""
        if is_bypass() or current_tenant_id() != tenant_id or type(actor_id) is not int or actor_id <= 0:
            raise Bitrix24Error("connection_denied", status_code=403)

        if session is None:
            async with async_session_maker() as owned, owned.begin():
                return await self.get_greeting_delivery_status(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    greeting_id=greeting_id,
                    session=owned,
                )

        rows = list(
            (
                await session.execute(
                    select(GreetingHistory).where(
                        GreetingHistory.tenant_id == tenant_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        row = next((r for r in rows if r.id == greeting_id), None)
        if row is None:
            raise Bitrix24Error("greeting_not_found", status_code=404)

        outbox_message_id = None
        outbox_target_id = None
        outbox_state = None
        provider_message_id = None
        error_code = None

        if row.command_id is not None:
            targets = list(
                (
                    await session.execute(
                        select(OutboxTarget).where(
                            OutboxTarget.tenant_id == tenant_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            matched_target = next((t for t in targets if t.command_id == row.command_id), None)
            if matched_target is not None:
                outbox_message_id = matched_target.message_id
                outbox_target_id = matched_target.id
                outbox_state = matched_target.state
                provider_message_id = matched_target.provider_message_id
                error_code = matched_target.error_code
                mapped = _map_outbox_state_to_greeting_status(matched_target.state)
                if mapped != row.status:
                    row.status = mapped
                    if mapped == "sent" and row.sent_at is None:
                        row.sent_at = datetime.now(UTC)
                    await session.flush()

        return {
            "id": row.id,
            "tenant_id": row.tenant_id,
            "contact_id": row.contact_id,
            "event_type": row.event_type,
            "event_date": row.event_date,
            "year": row.year,
            "channel": row.channel,
            "status": row.status,
            "command_id": row.command_id,
            "sent_at": row.sent_at,
            "outbox_message_id": outbox_message_id,
            "outbox_target_id": outbox_target_id,
            "outbox_state": outbox_state,
            "provider_message_id": provider_message_id,
            "error_code": error_code,
        }

    async def send_batch_messages(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        contact_ids: list[int],
        channel: str,
        connection_id: int,
        subject_template: str = "",
        body_template: str,
        purpose: Literal["marketing", "greeting"] = "marketing",
        scheduled_at: datetime | None = None,
        ttl_seconds: int = 86400,
        timezone_name: str = "Europe/Moscow",
        idempotency_key: str,
        outbox=None,
        now: datetime | None = None,
        session=None,
    ) -> dict[str, Any]:
        """Schedule a personalized, consent-checked batch campaign via the P15-T05 outbox (P14-T06)."""
        if is_bypass() or current_tenant_id() != tenant_id or type(actor_id) is not int or actor_id <= 0:
            raise Bitrix24Error("connection_denied", status_code=403)
        if channel not in ALLOWED_DELIVERY_CHANNELS:
            raise Bitrix24Error(
                "invalid_channel",
                "Batch messaging supports only email, telegram, or max channels (D7)",
                status_code=422,
            )
        if purpose not in ALLOWED_CAMPAIGN_PURPOSES:
            raise Bitrix24Error("invalid_purpose", status_code=422)
        if not contact_ids or len(contact_ids) > MAX_BATCH_CONTACTS:
            raise Bitrix24Error("invalid_batch_size", status_code=422)
        if not isinstance(idempotency_key, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", idempotency_key) is None:
            raise Bitrix24Error("invalid_idempotency_key", status_code=422)
        if not body_template or not body_template.strip():
            raise Bitrix24Error("invalid_message_template", status_code=422)

        if session is None:
            async with async_session_maker() as owned, owned.begin():
                return await self.send_batch_messages(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    contact_ids=contact_ids,
                    channel=channel,
                    connection_id=connection_id,
                    subject_template=subject_template,
                    body_template=body_template,
                    purpose=purpose,
                    scheduled_at=scheduled_at,
                    ttl_seconds=ttl_seconds,
                    timezone_name=timezone_name,
                    idempotency_key=idempotency_key,
                    outbox=outbox,
                    now=now,
                    session=owned,
                )

        effective_now = instant(now or datetime.now(UTC))
        eff_scheduled = instant(scheduled_at) if scheduled_at is not None else (effective_now + timedelta(minutes=1))
        batch_capability = "comms.batch_campaign.v1"

        # Check idempotency against existing batch BusinessCommand rows
        existing_cmds = list(
            (
                await session.execute(
                    select(BusinessCommand).where(
                        BusinessCommand.tenant_id == tenant_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        existing_batch = next(
            (
                c
                for c in existing_cmds
                if c.actor_user_id == actor_id
                and c.capability == batch_capability
                and c.idempotency_key == idempotency_key
            ),
            None,
        )
        if existing_batch is not None:
            return await self.get_batch_status(
                tenant_id=tenant_id,
                actor_id=actor_id,
                batch_id=existing_batch.id,
                session=session,
            )

        dedup_contact_ids = list(dict.fromkeys(int(cid) for cid in contact_ids))

        contacts_all = list(
            (
                await session.execute(
                    select(Contact).where(
                        Contact.tenant_id == tenant_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        contacts_by_id = {c.id: c for c in contacts_all if c.merged_into_id is None}

        crm_entities = list(
            (
                await session.execute(
                    select(CrmEntity).where(
                        CrmEntity.tenant_id == tenant_id,
                        CrmEntity.is_deleted_remote.is_(False),
                    )
                )
            )
            .scalars()
            .all()
        )
        companies_by_ext: dict[tuple[int, str], str] = {}
        crm_by_contact: dict[int, CrmEntity] = {}
        for ent in crm_entities:
            if ent.entity_kind == "company":
                companies_by_ext[(ent.connection_id, ent.external_id)] = ent.title_encrypted
            if ent.contact_id is not None and ent.contact_id not in crm_by_contact:
                crm_by_contact[ent.contact_id] = ent

        effective_outbox = outbox or self._outbox
        items: list[dict[str, Any]] = []
        skipped_no_consent: list[int] = []
        skipped_no_route: list[int] = []
        scheduled_message_ids: list[int] = []

        for cid in dedup_contact_ids:
            contact = contacts_by_id.get(cid)
            if contact is None:
                skipped_no_route.append(cid)
                items.append(
                    {
                        "contact_id": cid,
                        "status": "skipped",
                        "reason": "contact_unavailable",
                        "outbox_message_id": None,
                        "outbox_target_id": None,
                        "command_id": None,
                    }
                )
                continue

            # D15: Every batch target must have a recorded ContactConsent for (channel, purpose).
            consented = await has_active_consent(
                session,
                tenant_id=tenant_id,
                contact_id=cid,
                channel=channel,
                purpose=purpose,
                now=effective_now,
            )
            if not consented:
                skipped_no_consent.append(cid)
                items.append(
                    {
                        "contact_id": cid,
                        "status": "skipped",
                        "reason": "consent_required",
                        "outbox_message_id": None,
                        "outbox_target_id": None,
                        "command_id": None,
                    }
                )
                continue

            identity = await _resolve_contact_identity(
                session,
                tenant_id=tenant_id,
                contact_id=cid,
                channel=channel,
                connection_id=connection_id,
            )
            if identity is None:
                skipped_no_route.append(cid)
                items.append(
                    {
                        "contact_id": cid,
                        "status": "skipped",
                        "reason": "missing_verified_identity",
                        "outbox_message_id": None,
                        "outbox_target_id": None,
                        "command_id": None,
                    }
                )
                continue

            linked_crm = crm_by_contact.get(cid)
            company_title = None
            if linked_crm and linked_crm.company_external_id:
                company_title = companies_by_ext.get((linked_crm.connection_id, linked_crm.company_external_id))

            rendered_subject = (
                render_personalized_template(
                    subject_template,
                    name=contact.display_name_encrypted,
                    company=company_title,
                    single_line=True,
                )
                if subject_template
                else ""
            )
            rendered_body = render_personalized_template(
                body_template,
                name=contact.display_name_encrypted,
                company=company_title,
                single_line=False,
            )
            unsub_token = create_unsubscribe_token(
                tenant_id=tenant_id,
                contact_id=cid,
                channel=channel,
                purpose=purpose,
                issued_at=int(effective_now.timestamp()),
            )
            unsub_url = build_unsubscribe_url(unsub_token)
            final_body = append_unsubscribe_footer(
                rendered_body,
                unsubscribe_url=unsub_url,
                channel=channel,
            )

            item_idem_key = f"{idempotency_key}:c{cid}"
            try:
                draft_res = await effective_outbox.draft(
                    DraftIn(
                        contact_id=cid,
                        subject=rendered_subject,
                        body=final_body,
                        purpose=purpose,
                        timezone=timezone_name,
                        targets=[
                            TargetIn(
                                channel=channel,  # type: ignore[arg-type]
                                connection_id=connection_id,
                                identity_id=identity.id,
                            )
                        ],
                    ),
                    item_idem_key,
                    session=session,
                )
                sched_res = await effective_outbox.schedule(
                    ScheduleIn(
                        message_id=draft_res["id"],
                        expected_content_hash=draft_res["content_hash"],
                        scheduled_at=eff_scheduled,
                        ttl_seconds=ttl_seconds,
                    ),
                    session=session,
                )
                t_out = sched_res["targets"][0]
                scheduled_message_ids.append(sched_res["id"])
                items.append(
                    {
                        "contact_id": cid,
                        "status": sched_res["state"],
                        "reason": None,
                        "outbox_message_id": sched_res["id"],
                        "outbox_target_id": t_out["id"],
                        "command_id": t_out.get("command_id"),
                        "unsubscribe_url": unsub_url,
                    }
                )
            except PermissionError as exc:
                raise Bitrix24Error("outbox_permission_denied", status_code=403) from exc
            except (OutboxUnavailable, OutboxConflict, outbox_transitions.CommunicationError, ValueError) as exc:
                skipped_no_route.append(cid)
                items.append(
                    {
                        "contact_id": cid,
                        "status": "skipped",
                        "reason": str(exc) or "outbox_unavailable",
                        "outbox_message_id": None,
                        "outbox_target_id": None,
                        "command_id": None,
                    }
                )

        warning_parts: list[str] = []
        if skipped_no_consent:
            warning_parts.append(
                f"Пропущено контактов без подтверждённого согласия (opt-in D15, цель '{purpose}'): "
                f"{len(skipped_no_consent)} из {len(dedup_contact_ids)}."
            )
        if skipped_no_route:
            warning_parts.append(
                f"Пропущено контактов без подтверждённого канала/маршрута '{channel}': {len(skipped_no_route)}."
            )
        coverage_warning = " ".join(warning_parts) if warning_parts else None

        target_refs = [f"outbox_message:{mid}" for mid in scheduled_message_ids] or [
            f"contact:{cid}" for cid in dedup_contact_ids[:20]
        ]
        batch_payload = {
            "channel": channel,
            "purpose": purpose,
            "connection_id": connection_id,
            "total_requested": len(dedup_contact_ids),
            "scheduled_message_ids": scheduled_message_ids,
            "skipped_no_consent": skipped_no_consent,
            "skipped_no_route": skipped_no_route,
        }
        payload_hash = canonical_payload_hash(batch_capability, target_refs, batch_payload)
        batch_cmd = BusinessCommand(
            tenant_id=tenant_id,
            capability=batch_capability,
            capability_version=1,
            approval_policy="per_batch",
            actor_user_id=actor_id,
            connection_id=connection_id,
            idempotency_key=idempotency_key,
            target_refs=target_refs,
            payload=batch_payload,
            payload_hash=payload_hash,
            preview_snapshot={
                "coverage_warning": coverage_warning,
                "items": items,
            },
            state="approved" if scheduled_message_ids else "cancelled",
            approved_user_id=actor_id,
            approved_payload_hash=payload_hash,
            approved_at=effective_now,
            approval_expires_at=effective_now + timedelta(seconds=ttl_seconds),
        )
        session.add(batch_cmd)
        await session.flush()

        await ActionAuditEvent.objects.record_success(
            session=session,
            actor_kind="user",
            actor_id=actor_id,
            action="comms.campaign_send",
            resource_kind="campaign",
            resource_id=batch_cmd.id,
        )

        return await self.get_batch_status(
            tenant_id=tenant_id,
            actor_id=actor_id,
            batch_id=batch_cmd.id,
            session=session,
        )

    async def get_batch_status(
        self,
        *,
        tenant_id: int,
        actor_id: int,
        batch_id: int,
        session=None,
    ) -> dict[str, Any]:
        """Return live per-contact and aggregate delivery status for a batch campaign from the P15-T05 outbox."""
        if is_bypass() or current_tenant_id() != tenant_id or type(actor_id) is not int or actor_id <= 0:
            raise Bitrix24Error("connection_denied", status_code=403)
        if type(batch_id) is not int or batch_id <= 0:
            raise Bitrix24Error("batch_not_found", status_code=404)

        if session is None:
            async with async_session_maker() as owned, owned.begin():
                return await self.get_batch_status(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    batch_id=batch_id,
                    session=owned,
                )

        cmds = list(
            (
                await session.execute(
                    select(BusinessCommand).where(
                        BusinessCommand.tenant_id == tenant_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        batch_cmd = next(
            (
                c
                for c in cmds
                if c.id == batch_id
                and (c.capability == "comms.batch_campaign.v1" or str(c.capability).startswith("crm.batch."))
            ),
            None,
        )
        if batch_cmd is None:
            raise Bitrix24Error("batch_not_found", status_code=404)

        payload = batch_cmd.payload if isinstance(batch_cmd.payload, dict) else {}
        snapshot = batch_cmd.preview_snapshot if isinstance(batch_cmd.preview_snapshot, dict) else {}
        stored_items = snapshot.get("items") if isinstance(snapshot.get("items"), list) else []

        # Refresh live outbox target states
        outbox_targets = list(
            (
                await session.execute(
                    select(OutboxTarget).where(
                        OutboxTarget.tenant_id == tenant_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        targets_by_id = {t.id: t for t in outbox_targets}
        targets_by_msg = {t.message_id: t for t in outbox_targets}

        counts: dict[str, int] = {
            "approved": 0,
            "claimed": 0,
            "sent": 0,
            "failed": 0,
            "uncertain_delivery": 0,
            "cancelled": 0,
            "skipped": 0,
        }
        live_items: list[dict[str, Any]] = []
        for item in stored_items:
            if not isinstance(item, dict):
                continue
            updated = dict(item)
            tgt_id = updated.get("outbox_target_id")
            msg_id = updated.get("outbox_message_id")
            live_tgt = targets_by_id.get(tgt_id) if tgt_id is not None else targets_by_msg.get(msg_id)
            if live_tgt is not None:
                updated["status"] = live_tgt.state
                updated["provider_message_id"] = live_tgt.provider_message_id
                updated["error_code"] = live_tgt.error_code
            st = str(updated.get("status") or "skipped")
            counts[st] = counts.get(st, 0) + 1
            live_items.append(updated)

        skipped_no_consent_ids = list(payload.get("skipped_no_consent") or [])
        skipped_no_route_ids = list(payload.get("skipped_no_route") or [])
        total_req = int(payload.get("total_requested") or len(live_items))
        return {
            "batch_id": batch_cmd.id,
            "tenant_id": batch_cmd.tenant_id,
            "channel": payload.get("channel", "email"),
            "purpose": payload.get("purpose", "marketing"),
            "state": batch_cmd.state,
            "requested_count": total_req,
            "total_requested": total_req,
            "scheduled_count": len(payload.get("scheduled_message_ids") or []),
            "skipped_no_consent_count": len(skipped_no_consent_ids),
            "skipped_no_consent_contact_ids": skipped_no_consent_ids,
            "skipped_no_route_count": len(skipped_no_route_ids),
            "skipped_no_route_contact_ids": skipped_no_route_ids,
            "coverage_warning": snapshot.get("coverage_warning"),
            "counts": counts,
            "items": live_items,
        }


crm_messaging_service = CrmMessagingService()
