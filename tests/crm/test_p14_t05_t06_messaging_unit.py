"""P14-T05 & P14-T06 unit and HTTP contract tests for greeting delivery, batch messaging, consent, and unsubscribe.

Covers:
- P14-T05 (`CrmGreetingsService.send_greeting` / `CrmMessagingService.send_greeting`):
  - Schedules approved birthday/anniversary greetings over `email`, `telegram`, and `max`
    through the P15-T05 `OutboxStore` (`draft` + `schedule`) without creating a second outbox or sender (D10).
  - Enforces D7 channel restrictions (`email`, `telegram`, `max` only; rejects `phone`, `bitrix24`, `mtproto`).
  - Enforces D15 explicit opt-in consent for `purpose="greeting"`: a channel/identity on a Bitrix24 CRM card
    without a recorded `ContactConsent` row is rejected with `403 consent_required`.
  - Prevents repeat greetings for the same `(tenant_id, contact_id, event_type, year)`
    (`409 greeting_already_processed`).
  - Synchronizes delivery status (`approved`, `sent`, `failed`, `unknown`, `skipped`) from `OutboxTarget`.
- P14-T06 (`CrmMessagingService.send_batch_messages`, `get_batch_status`, `process_one_click_unsubscribe`,
  `record_manual_consent`):
  - Personalizes `{name}` and `{company}` single-pass (no recursive expansion, no CR/LF injection in subject).
  - Handles 100+ contacts in a batch (`120` contacts tested), filtering out unconsented or withdrawn contacts
    and returning an explicit `coverage_warning` + `skipped_no_consent_count` (D15).
  - Appends a signed one-click unsubscribe link/command to every greeting and batch message (D7, D16).
  - Public `GET|POST /api/v1/integrations/bitrix/messaging/unsubscribe` works through `ApiScopeMiddleware`
    with **no login / no bearer token** (D16), records a `ContactConsent` withdrawal row, and immediately
    cancels unclaimed scheduled `OutboxMessage` / `OutboxTarget` rows.
  - Manual consent recording (`POST /api/v1/integrations/bitrix/contacts/{contact_id}/consents`, D17)
    enforces `contact.UPDATE`, requires `basis` and `source_evidence`, and writes an `ActionAuditEvent`
    naming the manager (`actor_kind="user"`, `actor_id=manager.id`).
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1.endpoints import bitrix as bitrix_endpoints
from app.core import api_scope
from app.core.api_scope import ApiScopeMiddleware
from app.core.config import settings
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.integrations.bitrix24.client import Bitrix24Error
from app.models.actions.business_command import BusinessCommand, BusinessCommandTarget
from app.models.audit.action_audit_event import ActionAuditEvent
from app.models.communications.outbox import OutboxMessage, OutboxTarget
from app.models.contacts.core import Contact, ContactConsent, ContactIdentity
from app.models.crm.entity import CrmEntity
from app.models.crm.greeting_history import GreetingHistory
from app.services.commands.registry import canonical_payload_hash
from app.services.communications.outbox_contracts import DraftIn, ScheduleIn
from app.services.crm.greetings import CrmGreetingsService
from app.services.crm.messaging import (
    CrmMessagingService,
    create_unsubscribe_token,
    has_active_consent,
    render_personalized_template,
    verify_unsubscribe_token,
)
from app.services.user.auth import get_authenticated_user
from app.types import ActionType

_SIGNING_SEED = "unit-test-" + ("ab" * 16)
os.environ.setdefault("SECRET_KEY", _SIGNING_SEED)
os.environ.setdefault("POSTGRES_URL", "postgresql+asyncpg://localhost:5432/test_db")
os.environ.setdefault("CREDENTIALS_KEY", Fernet.generate_key().decode("ascii"))
os.environ.setdefault("PII_ENCRYPTION_KEYS", Fernet.generate_key().decode("ascii"))
os.environ.setdefault("PII_INDEX_KEY", "i" * 64)

__source_only__ = True

UTC = timezone.utc


@pytest.fixture(autouse=True)
def _pii_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode("ascii"))
    monkeypatch.setattr(settings, "PII_INDEX_KEY", "i" * 64)
    monkeypatch.setattr(settings, "SECRET_KEY", _SIGNING_SEED)


class _ScalarResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalars(self) -> "_ScalarResult":
        return self

    def all(self) -> list[Any]:
        return list(self._rows)


class FakeMessagingSession:
    """In-memory SQLAlchemy-compatible session for CRM messaging, consents, greetings, and outbox rows."""

    def __init__(self) -> None:
        self.contacts: dict[int, Contact] = {}
        self.identities: dict[int, ContactIdentity] = {}
        self.consents: dict[int, ContactConsent] = {}
        self.crm_entities: list[CrmEntity] = []
        self.greetings: dict[int, GreetingHistory] = {}
        self.outbox_messages: dict[int, OutboxMessage] = {}
        self.outbox_targets: dict[int, OutboxTarget] = {}
        self.commands: dict[int, BusinessCommand] = {}
        self.command_targets: dict[int, BusinessCommandTarget] = {}
        self._next_id = 1

    def add(self, obj: Any) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = self._next_id
            self._next_id += 1
        if isinstance(obj, Contact):
            self.contacts[obj.id] = obj
        elif isinstance(obj, ContactIdentity):
            self.identities[obj.id] = obj
        elif isinstance(obj, ContactConsent):
            self.consents[obj.id] = obj
        elif isinstance(obj, CrmEntity):
            self.crm_entities.append(obj)
        elif isinstance(obj, GreetingHistory):
            self.greetings[obj.id] = obj
        elif isinstance(obj, OutboxMessage):
            self.outbox_messages[obj.id] = obj
        elif isinstance(obj, OutboxTarget):
            self.outbox_targets[obj.id] = obj
        elif isinstance(obj, BusinessCommand):
            self.commands[obj.id] = obj
        elif isinstance(obj, BusinessCommandTarget):
            self.command_targets[obj.id] = obj

    async def flush(self) -> None:
        for collection in (
            self.contacts.values(),
            self.identities.values(),
            self.consents.values(),
            self.greetings.values(),
            self.outbox_messages.values(),
            self.outbox_targets.values(),
            self.commands.values(),
            self.command_targets.values(),
        ):
            for obj in collection:
                if getattr(obj, "id", None) is None:
                    obj.id = self._next_id
                    self._next_id += 1

    async def refresh(self, _obj: Any) -> None:
        return None

    async def execute(self, stmt: Any) -> _ScalarResult:
        params = stmt.compile().params
        tables = {getattr(f, "name", "") for f in stmt.get_final_froms()}
        tid = params.get("tenant_id_1")

        if "contacts" in tables:
            cid = params.get("id_1")
            rows = [
                c
                for c in self.contacts.values()
                if (tid is None or c.tenant_id == tid) and (cid is None or c.id == cid)
            ]
            return _ScalarResult(rows)

        if "contact_identities" in tables:
            cid = params.get("contact_id_1")
            rows = [
                i
                for i in sorted(self.identities.values(), key=lambda x: x.id)
                if (tid is None or i.tenant_id == tid) and (cid is None or i.contact_id == cid)
            ]
            return _ScalarResult(rows)

        if "contact_consents" in tables:
            cid = params.get("contact_id_1")
            ch = params.get("channel_1")
            purp = params.get("purpose_1")
            rows = [
                c
                for c in sorted(self.consents.values(), key=lambda x: x.id, reverse=True)
                if (tid is None or c.tenant_id == tid)
                and (cid is None or c.contact_id == cid)
                and (ch is None or c.channel == ch)
                and (purp is None or c.purpose == purp)
            ]
            return _ScalarResult(rows)

        if "crm_entities" in tables:
            rows = [e for e in self.crm_entities if (tid is None or e.tenant_id == tid) and not e.is_deleted_remote]
            return _ScalarResult(rows)

        if "greeting_history" in tables:
            cid = params.get("contact_id_1")
            ev_type = params.get("event_type_1")
            yr = params.get("year_1")
            rows = [
                g
                for g in self.greetings.values()
                if (tid is None or g.tenant_id == tid)
                and (cid is None or g.contact_id == cid)
                and (ev_type is None or g.event_type == ev_type)
                and (yr is None or g.year == yr)
            ]
            return _ScalarResult(rows)

        if "outbox_messages" in tables:
            cid = params.get("contact_id_1")
            purp = params.get("purpose_1")
            rows = [
                m
                for m in self.outbox_messages.values()
                if (tid is None or m.tenant_id == tid)
                and (cid is None or m.contact_id == cid)
                and (purp is None or m.purpose == purp)
            ]
            return _ScalarResult(rows)

        if "outbox_targets" in tables:
            mid = params.get("message_id_1")
            rows = [
                t
                for t in self.outbox_targets.values()
                if (tid is None or t.tenant_id == tid) and (mid is None or t.message_id == mid)
            ]
            return _ScalarResult(rows)

        if "business_commands" in tables:
            cmd_id = params.get("id_1")
            rows = [
                c
                for c in self.commands.values()
                if (tid is None or c.tenant_id == tid) and (cmd_id is None or c.id == cmd_id)
            ]
            return _ScalarResult(rows)

        if "business_command_targets" in tables:
            cmd_id = params.get("command_id_1")
            rows = [
                ct
                for ct in self.command_targets.values()
                if (tid is None or ct.tenant_id == tid) and (cmd_id is None or ct.command_id == cmd_id)
            ]
            return _ScalarResult(rows)

        return _ScalarResult([])


class FakeOutboxStoreAdapter:
    """In-memory adapter implementing the P15-T05 `OutboxStore.draft` and `OutboxStore.schedule` contract."""

    def __init__(self, *, tenant_id: int = 10, author_id: int = 101) -> None:
        self.tenant_id = tenant_id
        self.author_id = author_id
        self.draft_calls: list[tuple[DraftIn, str]] = []
        self.schedule_calls: list[ScheduleIn] = []

    async def draft(self, data: DraftIn, idempotency_key: str, *, session: FakeMessagingSession) -> dict[str, Any]:
        self.draft_calls.append((data, idempotency_key))
        target_refs = [f"{t.channel}:{t.connection_id}:{t.identity_id}" for t in data.targets]
        content_hash = canonical_payload_hash(
            "comms.email.send.v1",
            target_refs,
            {
                "contact_id": data.contact_id,
                "subject": data.subject,
                "body": data.body,
                "purpose": data.purpose,
                "targets": [t.model_dump() for t in data.targets],
            },
        )
        msg = OutboxMessage(
            tenant_id=self.tenant_id,
            author_id=self.author_id,
            contact_id=data.contact_id,
            purpose=data.purpose,
            state="validated",
            timezone=data.timezone,
            subject_encrypted=data.subject,
            content_encrypted=data.body,
            content_hash=content_hash,
            request_hash=content_hash,
            idempotency_key=idempotency_key,
        )
        session.add(msg)
        target_dicts = []
        for t_in in data.targets:
            tgt = OutboxTarget(
                tenant_id=self.tenant_id,
                message_id=msg.id,
                channel=t_in.channel,
                connection_id=t_in.connection_id,
                identity_id=t_in.identity_id,
                recipient_encrypted=f"{t_in.channel}:{t_in.identity_id}",
                credential_hash=content_hash,
                content_hash=content_hash,
                idempotency_key=f"{idempotency_key}:{t_in.channel}:{t_in.identity_id}",
                state="validated",
                attempts=0,
            )
            session.add(tgt)
            target_dicts.append(
                {
                    "id": tgt.id,
                    "channel": tgt.channel,
                    "connection_id": tgt.connection_id,
                    "identity_id": tgt.identity_id,
                    "state": tgt.state,
                    "command_id": None,
                }
            )
        await session.flush()
        return {
            "id": msg.id,
            "tenant_id": msg.tenant_id,
            "author_id": msg.author_id,
            "contact_id": msg.contact_id,
            "purpose": msg.purpose,
            "state": msg.state,
            "content_hash": msg.content_hash,
            "targets": target_dicts,
        }

    async def schedule(self, data: ScheduleIn, *, session: FakeMessagingSession) -> dict[str, Any]:
        self.schedule_calls.append(data)
        msg = session.outbox_messages[data.message_id]
        assert msg.content_hash == data.expected_content_hash
        now = datetime.now(UTC)
        msg.state = "approved"
        msg.approved_by = self.author_id
        msg.approved_hash = msg.content_hash
        msg.scheduled_at = data.scheduled_at
        msg.approval_expires_at = data.scheduled_at + timedelta(seconds=data.ttl_seconds)

        targets = [t for t in session.outbox_targets.values() if t.message_id == msg.id]
        target_dicts = []
        for tgt in targets:
            target_ref = f"{tgt.channel}:{tgt.connection_id}:{tgt.identity_id}"
            cap = "comms.email.send.v1" if tgt.channel == "email" else f"comms.{tgt.channel}.bot_send.v1"
            cmd = BusinessCommand(
                tenant_id=self.tenant_id,
                actor_user_id=self.author_id,
                capability=cap,
                approval_policy="per_item",
                state="approved",
                idempotency_key=f"outbox-target-{tgt.id}",
                target_refs=[target_ref],
                payload={"message_id": msg.id, "target_id": tgt.id},
                payload_hash=msg.content_hash,
                approved_payload_hash=msg.content_hash,
                approved_user_id=self.author_id,
                approved_at=now,
                approval_expires_at=msg.approval_expires_at,
                preview_snapshot={},
            )
            session.add(cmd)
            session.add(
                BusinessCommandTarget(
                    tenant_id=self.tenant_id,
                    command_id=cmd.id,
                    target_ref=target_ref,
                    target_idempotency_key=f"outbox-target-{tgt.id}",
                    state="pending",
                )
            )
            tgt.state = "approved"
            tgt.command_id = cmd.id
            target_dicts.append(
                {
                    "id": tgt.id,
                    "channel": tgt.channel,
                    "connection_id": tgt.connection_id,
                    "identity_id": tgt.identity_id,
                    "state": tgt.state,
                    "command_id": tgt.command_id,
                }
            )
        await session.flush()
        return {
            "id": msg.id,
            "state": msg.state,
            "scheduled_at": msg.scheduled_at,
            "targets": target_dicts,
        }


def test_p14_t06_personalization_and_unsubscribe_token_tamper_resistance() -> None:
    # Single-pass replacement: {name} containing "{company}" must NOT expand recursively
    rendered = render_personalized_template(
        "Здравствуйте, {name} из {company}!",
        name="Агент {company}",
        company="ООО Альфа",
    )
    assert rendered == "Здравствуйте, Агент {company} из ООО Альфа!"

    # Subject single-line sanitization strips CR/LF
    subj = render_personalized_template(
        "Тема для {name}\r\nBcc: attacker@example.com",
        name="Иван\nПетров",
        single_line=True,
    )
    assert "\r" not in subj and "\n" not in subj

    token = create_unsubscribe_token(
        tenant_id=10,
        contact_id=42,
        channel="email",
        purpose="marketing",
        issued_at=1760000000,
    )
    verified = verify_unsubscribe_token(token)
    assert verified == {
        "tenant_id": 10,
        "contact_id": 42,
        "channel": "email",
        "purpose": "marketing",
        "issued_at": 1760000000,
    }

    # Tampered signature is rejected
    payload_part, sig_part = token.split(".", 1)
    bad_token = f"{payload_part}.{'A' if sig_part[0] != 'A' else 'B'}{sig_part[1:]}"
    with pytest.raises(Bitrix24Error) as exc_info:
        verify_unsubscribe_token(bad_token)
    assert exc_info.value.code == "invalid_unsubscribe_token"
    assert exc_info.value.status_code == 422

    # Public API allowlist includes /api/v1/integrations/bitrix/messaging/unsubscribe
    assert api_scope._is_public("/api/v1/integrations/bitrix/messaging/unsubscribe") is True


@pytest.mark.asyncio
async def test_p14_t05_greeting_delivery_via_email_telegram_and_max_with_d15_consent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeMessagingSession()
    fake_outbox = FakeOutboxStoreAdapter(tenant_id=10, author_id=101)
    now = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)
    monkeypatch.setattr(ActionAuditEvent.objects, "record_success", AsyncMock(return_value=SimpleNamespace(id=1)))

    # Contact with email, telegram_id, and max_id on CRM card, but NO consent yet
    c1 = Contact(
        id=1,
        tenant_id=10,
        display_name_encrypted="Екатерина Смирнова",
        birthday_encrypted="1992-11-11",
        birthday_has_year=True,
        anniversary_encrypted="2021-11-15",
        anniversary_has_year=True,
        preferred_channel="email",
    )
    session.add(c1)
    session.add(
        ContactIdentity(
            id=11,
            tenant_id=10,
            contact_id=1,
            kind="email",
            value_encrypted="ekaterina@example.com",
            blind_index="h_email",
            connection_id=5,
            verified=True,
        )
    )
    session.add(
        ContactIdentity(
            id=12,
            tenant_id=10,
            contact_id=1,
            kind="telegram_id",
            value_encrypted="99887766",
            blind_index="h_tg",
            connection_id=6,
            verified=True,
        )
    )
    session.add(
        ContactIdentity(
            id=13,
            tenant_id=10,
            contact_id=1,
            kind="max_id",
            value_encrypted="max_554433",
            blind_index="h_max",
            connection_id=7,
            verified=True,
        )
    )

    greetings_svc = CrmGreetingsService()
    messaging_svc = CrmMessagingService(outbox=fake_outbox)

    with tenant_scope(10):
        # 1. Generate birthday greeting draft
        draft = await greetings_svc.generate_greeting_draft(
            tenant_id=10,
            actor_id=101,
            contact_id=1,
            event_type="birthday",
            year=2026,
            channel="email",
            reference_date=date(2026, 11, 8),
            session=session,
        )
        assert draft["status"] == "draft"

        # 2. D15: Attempting to send without a recorded ContactConsent row for purpose='greeting' fails with 403
        with pytest.raises(Bitrix24Error) as exc_no_consent:
            await messaging_svc.send_greeting(
                tenant_id=10,
                actor_id=101,
                connection_id=5,
                greeting_id=draft["id"],
                now=now,
                session=session,
            )
        assert exc_no_consent.value.code == "consent_required"
        assert exc_no_consent.value.status_code == 403
        assert len(fake_outbox.draft_calls) == 0

        # 3. D7: Disallowed channel ('phone' or 'bitrix24') is rejected with 422
        with pytest.raises(Bitrix24Error) as exc_bad_channel:
            await messaging_svc.send_greeting(
                tenant_id=10,
                actor_id=101,
                connection_id=5,
                greeting_id=draft["id"],
                channel="phone",
                now=now,
                session=session,
            )
        assert exc_bad_channel.value.code == "invalid_channel"
        assert exc_bad_channel.value.status_code == 422

        # 4. Grant opt-in consent for email/telegram/max with purpose='greeting'
        for ch in ("email", "telegram", "max"):
            session.add(
                ContactConsent(
                    tenant_id=10,
                    contact_id=1,
                    channel=ch,
                    purpose="greeting",
                    basis_encrypted="client_opt_in",
                    source_evidence_encrypted=f"form:{ch}",
                    granted_at=now - timedelta(days=1),
                )
            )

        # 5. Send birthday greeting via email through OutboxStore
        sent_email = await messaging_svc.send_greeting(
            tenant_id=10,
            actor_id=101,
            connection_id=5,
            greeting_id=draft["id"],
            channel="email",
            now=now,
            session=session,
        )
        assert sent_email["status"] == "approved"
        assert sent_email["channel"] == "email"
        assert sent_email["command_id"] is not None
        assert "/api/v1/integrations/bitrix/messaging/unsubscribe?token=" in sent_email["unsubscribe_url"]
        assert sent_email["unsubscribe_url"] in fake_outbox.draft_calls[-1][0].body

        # 6. Repeat send for the same greeting in the same year is refused with 409
        with pytest.raises(Bitrix24Error) as exc_dup:
            await messaging_svc.send_greeting(
                tenant_id=10,
                actor_id=101,
                connection_id=5,
                greeting_id=draft["id"],
                channel="email",
                now=now,
                session=session,
            )
        assert exc_dup.value.code == "greeting_already_processed"
        assert exc_dup.value.status_code == 409

        # 7. Simulate P15-T06 sender completing delivery -> get_greeting_delivery_status updates status to 'sent'
        outbox_tgt = session.outbox_targets[sent_email["outbox_target_id"]]
        outbox_tgt.state = "sent"
        outbox_tgt.provider_message_id = "<smtp-msg-001@example.com>"
        status_out = await messaging_svc.get_greeting_delivery_status(
            tenant_id=10,
            actor_id=101,
            greeting_id=draft["id"],
            session=session,
        )
        assert status_out["status"] == "sent"
        assert status_out["provider_message_id"] == "<smtp-msg-001@example.com>"
        assert status_out["sent_at"] is not None

        # 8. Deliver anniversary greeting over Telegram (year=2026) and MAX (year=2027)
        draft_tg = await greetings_svc.generate_greeting_draft(
            tenant_id=10,
            actor_id=101,
            contact_id=1,
            event_type="anniversary",
            year=2026,
            channel="telegram",
            reference_date=date(2026, 11, 8),
            session=session,
        )
        sent_tg = await messaging_svc.send_greeting(
            tenant_id=10,
            actor_id=101,
            connection_id=6,
            greeting_id=draft_tg["id"],
            channel="telegram",
            now=now,
            session=session,
        )
        assert sent_tg["channel"] == "telegram"
        assert sent_tg["status"] == "approved"

        draft_max = await greetings_svc.generate_greeting_draft(
            tenant_id=10,
            actor_id=101,
            contact_id=1,
            event_type="anniversary",
            year=2027,
            channel="max",
            reference_date=date(2026, 11, 8),
            session=session,
        )
        sent_max = await messaging_svc.send_greeting(
            tenant_id=10,
            actor_id=101,
            connection_id=7,
            greeting_id=draft_max["id"],
            channel="max",
            now=now,
            session=session,
        )
        assert sent_max["channel"] == "max"
        assert sent_max["status"] == "approved"


@pytest.mark.asyncio
async def test_p14_t06_batch_120_contacts_personalization_coverage_warning_and_unsubscribe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeMessagingSession()
    fake_outbox = FakeOutboxStoreAdapter(tenant_id=10, author_id=101)
    now = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(ActionAuditEvent.objects, "record_success", AsyncMock(return_value=SimpleNamespace(id=99)))

    # Company entity in CRM
    session.add(
        CrmEntity(
            id=500,
            tenant_id=10,
            connection_id=5,
            provider="bitrix24",
            entity_kind="company",
            external_id="comp-1",
            last_synced_at=now,
            title_encrypted="ООО Северсталь-Партнёр",
        )
    )

    # Create 120 contacts:
    # - Contacts 1..100: active 'marketing' consent + verified email identity
    # - Contacts 101..115: Bitrix24 CRM card + verified email identity, but NO ContactConsent row (D15 candidate only)
    # - Contacts 116..120: granted consent followed by a later withdrawal
    contact_ids: list[int] = []
    for i in range(1, 121):
        contact_ids.append(i)
        session.add(
            Contact(
                id=i,
                tenant_id=10,
                display_name_encrypted=f"Клиент #{i}",
                preferred_channel="email",
            )
        )
        session.add(
            ContactIdentity(
                id=1000 + i,
                tenant_id=10,
                contact_id=i,
                kind="email",
                value_encrypted=f"client{i}@example.com",
                blind_index=f"hash_{i}",
                connection_id=5,
                verified=True,
            )
        )
        session.add(
            CrmEntity(
                id=2000 + i,
                tenant_id=10,
                connection_id=5,
                provider="bitrix24",
                entity_kind="contact",
                external_id=f"bx-{i}",
                contact_id=i,
                company_external_id="comp-1",
                last_synced_at=now,
                title_encrypted=f"Клиент #{i}",
            )
        )
        if i <= 100:
            session.add(
                ContactConsent(
                    tenant_id=10,
                    contact_id=i,
                    channel="email",
                    purpose="marketing",
                    basis_encrypted="opt_in_form",
                    source_evidence_encrypted=f"web_form_{i}",
                    granted_at=now - timedelta(days=5),
                )
            )
        elif i >= 116:
            session.add(
                ContactConsent(
                    tenant_id=10,
                    contact_id=i,
                    channel="email",
                    purpose="marketing",
                    basis_encrypted="opt_in_form",
                    source_evidence_encrypted=f"web_form_{i}",
                    granted_at=now - timedelta(days=10),
                    withdrawn_at=now - timedelta(days=1),
                )
            )

    svc = CrmMessagingService(outbox=fake_outbox)
    with tenant_scope(10):
        batch = await svc.send_batch_messages(
            tenant_id=10,
            actor_id=101,
            contact_ids=contact_ids,
            channel="email",
            connection_id=5,
            subject_template="Специальное предложение для {name} ({company})",
            body_template="Здравствуйте, {name}!\nДля компании {company} доступны новые условия.",
            purpose="marketing",
            idempotency_key="batch-2026-11-08-001",
            now=now,
            session=session,
        )

        assert batch["requested_count"] == 120
        assert batch["scheduled_count"] == 100
        assert batch["skipped_no_consent_count"] == 20
        assert len(batch["skipped_no_consent_contact_ids"]) == 20
        assert batch["coverage_warning"] is not None
        assert "20 из 120" in batch["coverage_warning"]
        assert len(fake_outbox.draft_calls) == 100
        assert len(fake_outbox.schedule_calls) == 100

        # Check personalization on first scheduled draft
        first_draft_in = fake_outbox.draft_calls[0][0]
        assert first_draft_in.subject == "Специальное предложение для Клиент #1 (ООО Северсталь-Партнёр)"
        assert "Здравствуйте, Клиент #1!" in first_draft_in.body
        assert "Для компании ООО Северсталь-Партнёр доступны новые условия." in first_draft_in.body
        assert "/api/v1/integrations/bitrix/messaging/unsubscribe?token=" in first_draft_in.body

        # Idempotent replay with same idempotency_key does not schedule additional messages
        replay = await svc.send_batch_messages(
            tenant_id=10,
            actor_id=101,
            contact_ids=contact_ids,
            channel="email",
            connection_id=5,
            subject_template="Специальное предложение для {name} ({company})",
            body_template="Здравствуйте, {name}!\nДля компании {company} доступны новые условия.",
            purpose="marketing",
            idempotency_key="batch-2026-11-08-001",
            now=now,
            session=session,
        )
        assert replay["batch_id"] == batch["batch_id"]
        assert len(fake_outbox.draft_calls) == 100

    # D16: Contact #1 clicks the one-click unsubscribe link (without any login / tenant context)
    first_scheduled_item = next(item for item in batch["items"] if item["contact_id"] == 1)
    unsub_url = first_scheduled_item["unsubscribe_url"]
    token = unsub_url.split("token=", 1)[1]

    unsub_result = await svc.process_one_click_unsubscribe(
        token,
        now=now + timedelta(seconds=10),
        session=session,
    )
    assert unsub_result["unsubscribed"] is True
    assert unsub_result["contact_id"] == 1
    assert unsub_result["channel"] == "email"
    assert unsub_result["purpose"] == "marketing"
    assert unsub_result["cancelled_outbox_targets"] == 1

    # Verify Contact #1's scheduled outbox target was immediately cancelled and consent is now False
    with tenant_scope(10):
        assert (
            await has_active_consent(
                session,
                tenant_id=10,
                contact_id=1,
                channel="email",
                purpose="marketing",
                now=now + timedelta(seconds=15),
            )
            is False
        )
        status_after = await svc.get_batch_status(
            tenant_id=10,
            actor_id=101,
            batch_id=batch["batch_id"],
            session=session,
        )
        assert status_after["counts"]["cancelled"] == 1
        assert status_after["counts"]["approved"] == 99


@pytest.mark.asyncio
async def test_p14_t06_d17_manual_consent_and_d16_public_http_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeMessagingSession()
    now = datetime(2026, 11, 8, 15, 0, tzinfo=UTC)
    session.add(
        Contact(
            id=7,
            tenant_id=10,
            display_name_encrypted="Алексей Морозов",
            preferred_channel="telegram",
        )
    )

    audit_calls: list[dict[str, Any]] = []

    async def fake_record_success(**kwargs: Any):
        audit_calls.append(kwargs)
        return SimpleNamespace(id=777)

    monkeypatch.setattr(ActionAuditEvent.objects, "record_success", fake_record_success)

    # Stub ContactManager.record_consent to write into FakeMessagingSession while keeping its real validation
    svc = CrmMessagingService()
    manager_user = SimpleNamespace(
        id=42,
        is_active=True,
        username="manager",
        has_admin_access=lambda: False,
        role=SimpleNamespace(permissions=[]),
    )
    grants = frozenset(
        {
            ("source", ActionType.VIEW),
            ("contact", ActionType.VIEW),
            ("contact", ActionType.UPDATE),
        }
    )

    with tenant_scope(10), permission_scope(manager_user, member_grants=grants):
        consent_res = await svc.record_manual_consent(
            tenant_id=10,
            actor_id=manager_user.id,
            contact_id=7,
            channel="telegram",
            purpose="marketing",
            action="grant",
            basis="Подписанное письменное согласие клиента №14",
            source_evidence="scan://consents/2026-11-08/client-7.pdf",
            occurred_at=now,
            session=session,
        )
        assert consent_res["contact_id"] == 7
        assert consent_res["channel"] == "telegram"
        assert consent_res["purpose"] == "marketing"
        assert consent_res["action"] == "grant"
        assert consent_res["recorded_by_user_id"] == 42
        assert consent_res["audit_event_id"] == 777
        assert audit_calls[-1]["actor_kind"] == "user"
        assert audit_calls[-1]["actor_id"] == 42
        assert audit_calls[-1]["action"] == "crm.contact_update"
        assert audit_calls[-1]["resource_id"] == 7

        # Missing basis or source_evidence is rejected with 422
        with pytest.raises(Bitrix24Error) as exc_blank:
            await svc.record_manual_consent(
                tenant_id=10,
                actor_id=manager_user.id,
                contact_id=7,
                channel="telegram",
                purpose="marketing",
                action="grant",
                basis="   ",
                source_evidence="scan://consents/2026-11-08/client-7.pdf",
                occurred_at=now,
                session=session,
            )
        assert exc_blank.value.status_code == 422

    # Without contact.UPDATE permission, manual consent recording is rejected with 403
    viewer_grants = frozenset({("contact", ActionType.VIEW)})
    with tenant_scope(10), permission_scope(manager_user, member_grants=viewer_grants):
        with pytest.raises(Bitrix24Error) as exc_perm:
            await svc.record_manual_consent(
                tenant_id=10,
                actor_id=manager_user.id,
                contact_id=7,
                channel="telegram",
                purpose="marketing",
                action="grant",
                basis="basis",
                source_evidence="evidence",
                occurred_at=now,
                session=session,
            )
        assert exc_perm.value.status_code == 403

    # Verify public HTTP unsubscribe endpoint works through ApiScopeMiddleware without any Bearer token
    app = FastAPI()
    app.include_router(bitrix_endpoints.router, prefix="/api/v1/integrations/bitrix", dependencies=[])
    app.include_router(bitrix_endpoints.public_router, prefix="/api/v1/integrations/bitrix")
    wrapped_app = ApiScopeMiddleware(app)

    token = create_unsubscribe_token(
        tenant_id=10,
        contact_id=7,
        channel="telegram",
        purpose="marketing",
        issued_at=int(now.timestamp()),
    )

    async def fake_unsubscribe(tok: str) -> dict[str, Any]:
        return await svc.process_one_click_unsubscribe(tok, now=now + timedelta(minutes=1), session=session)

    monkeypatch.setattr(bitrix_endpoints.crm_messaging_service, "process_one_click_unsubscribe", fake_unsubscribe)

    async with AsyncClient(transport=ASGITransport(app=wrapped_app), base_url="http://testserver") as client:
        # GET without Authorization header succeeds (200)
        resp_get = await client.get(f"/api/v1/integrations/bitrix/messaging/unsubscribe?token={token}")
        assert resp_get.status_code == 200
        body_get = resp_get.json()
        assert body_get["unsubscribed"] is True
        assert body_get["contact_id"] == 7
        assert body_get["channel"] == "telegram"

        # POST without Authorization header succeeds (200)
        resp_post = await client.post(
            "/api/v1/integrations/bitrix/messaging/unsubscribe",
            json={"token": token},
        )
        assert resp_post.status_code == 200
        assert resp_post.json()["unsubscribed"] is True

        # Protected endpoint (/api/v1/integrations/bitrix/messaging/batch) without Authorization header returns 401
        resp_protected = await client.post("/api/v1/integrations/bitrix/messaging/batch", json={})
        assert resp_protected.status_code == 401


@pytest.mark.asyncio
async def test_p14_t05_t06_greetings_delegate_and_authenticated_rest_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeMessagingSession()
    fake_outbox = FakeOutboxStoreAdapter(tenant_id=10, author_id=101)
    now = datetime(2026, 11, 8, 16, 0, tzinfo=UTC)
    monkeypatch.setattr(ActionAuditEvent.objects, "record_success", AsyncMock(return_value=SimpleNamespace(id=501)))

    session.add(
        Contact(
            id=9,
            tenant_id=10,
            display_name_encrypted="Сергей Ковалёв",
            birthday_encrypted="1985-11-12",
            birthday_has_year=True,
            preferred_channel="telegram",
        )
    )
    session.add(
        ContactIdentity(
            id=91,
            tenant_id=10,
            contact_id=9,
            kind="telegram_id",
            value_encrypted="11223344",
            blind_index="h_tg_9",
            connection_id=6,
            verified=True,
        )
    )
    session.add(
        ContactConsent(
            tenant_id=10,
            contact_id=9,
            channel="telegram",
            purpose="greeting",
            basis_encrypted="opt_in",
            source_evidence_encrypted="tg_start",
            granted_at=now - timedelta(days=2),
        )
    )

    greetings_svc = CrmGreetingsService()
    with tenant_scope(10):
        draft = await greetings_svc.generate_greeting_draft(
            tenant_id=10,
            actor_id=101,
            contact_id=9,
            event_type="birthday",
            year=2026,
            channel="telegram",
            reference_date=date(2026, 11, 8),
            session=session,
        )
        # Exercise CrmGreetingsService.send_greeting by (contact_id, event_type, year) without greeting_id
        sent = await greetings_svc.send_greeting(
            tenant_id=10,
            actor_id=101,
            connection_id=6,
            contact_id=9,
            event_type="birthday",
            year=2026,
            channel="telegram",
            outbox=fake_outbox,
            now=now,
            session=session,
        )
        assert sent["id"] == draft["id"]
        assert sent["status"] == "approved"

        # Exercise CrmGreetingsService.get_greeting_delivery_status when target is uncertain_delivery / failed
        tgt = session.outbox_targets[sent["outbox_target_id"]]
        tgt.state = "uncertain_delivery"
        st_uncertain = await greetings_svc.get_greeting_delivery_status(
            tenant_id=10,
            actor_id=101,
            greeting_id=draft["id"],
            session=session,
        )
        assert st_uncertain["status"] == "unknown"

        tgt.state = "approved"
        session.greetings[draft["id"]].status = "approved"

    # Unsubscribe from 'greeting' purpose marks the approved GreetingHistory row as 'skipped'
    unsub_token = sent["unsubscribe_url"].split("token=", 1)[1]
    messaging_svc = CrmMessagingService(outbox=fake_outbox)
    unsub_out = await messaging_svc.process_one_click_unsubscribe(
        unsub_token,
        now=now + timedelta(minutes=2),
        session=session,
    )
    assert unsub_out["purpose"] == "greeting"
    assert session.greetings[draft["id"]].status == "skipped"

    # Exercise authenticated REST endpoints in app/api/v1/endpoints/bitrix.py
    app = FastAPI()
    app.include_router(bitrix_endpoints.router, prefix="/api/v1/integrations/bitrix")
    actor = SimpleNamespace(
        id=101,
        is_active=True,
        username="owner",
        has_admin_access=lambda: False,
        role=SimpleNamespace(permissions=[]),
    )
    app.dependency_overrides[get_authenticated_user] = lambda: actor
    monkeypatch.setattr("app.api.deps._load_with_rights", AsyncMock(return_value=actor))

    @app.middleware("http")
    async def _bind_scope(request, call_next):
        request.state.api_user = actor
        request.state.tenant_id = 10
        grants = frozenset(
            {
                ("source", ActionType.VIEW),
                ("source", ActionType.CREATE),
                ("contact", ActionType.VIEW),
                ("contact", ActionType.UPDATE),
                ("outboxmessage", ActionType.VIEW),
                ("outboxmessage", ActionType.CONFIGURE),
            }
        )
        with tenant_scope(10), permission_scope(actor, member_grants=grants):
            return await call_next(request)

    monkeypatch.setattr(
        bitrix_endpoints.crm_greetings_service,
        "send_greeting",
        AsyncMock(return_value=sent),
    )
    monkeypatch.setattr(
        bitrix_endpoints.crm_greetings_service,
        "get_greeting_delivery_status",
        AsyncMock(
            return_value={
                "id": draft["id"],
                "tenant_id": 10,
                "contact_id": 9,
                "event_type": "birthday",
                "event_date": date(2026, 11, 12),
                "year": 2026,
                "channel": "telegram",
                "status": "sent",
                "command_id": sent["command_id"],
                "sent_at": now,
                "outbox_message_id": sent["outbox_message_id"],
                "outbox_target_id": sent["outbox_target_id"],
                "outbox_state": "sent",
                "provider_message_id": "tg-msg-1",
                "error_code": None,
            }
        ),
    )
    fake_batch_out = {
        "batch_id": 88,
        "tenant_id": 10,
        "channel": "telegram",
        "purpose": "marketing",
        "state": "approved",
        "requested_count": 2,
        "scheduled_count": 1,
        "skipped_no_consent_count": 1,
        "skipped_no_consent_contact_ids": [10],
        "skipped_no_route_count": 0,
        "skipped_no_route_contact_ids": [],
        "coverage_warning": "Пропущено контактов без подтверждённого согласия (opt-in D15, цель 'marketing'): 1 из 2.",
        "counts": {"approved": 1, "skipped": 1},
        "items": [
            {
                "contact_id": 9,
                "status": "approved",
                "reason": None,
                "outbox_message_id": 1,
                "outbox_target_id": 1,
                "command_id": 1,
                "unsubscribe_url": sent["unsubscribe_url"],
            }
        ],
    }
    monkeypatch.setattr(
        bitrix_endpoints.crm_messaging_service,
        "send_batch_messages",
        AsyncMock(return_value=fake_batch_out),
    )
    monkeypatch.setattr(
        bitrix_endpoints.crm_messaging_service,
        "get_batch_status",
        AsyncMock(return_value=fake_batch_out),
    )
    monkeypatch.setattr(
        bitrix_endpoints.crm_messaging_service,
        "record_manual_consent",
        AsyncMock(
            return_value={
                "id": 301,
                "tenant_id": 10,
                "contact_id": 9,
                "channel": "telegram",
                "purpose": "marketing",
                "action": "grant",
                "granted_at": now,
                "withdrawn_at": None,
                "recorded_by_user_id": 101,
                "audit_event_id": 501,
            }
        ),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        r_send = await client.post(
            "/api/v1/integrations/bitrix/greetings/send",
            json={"greeting_id": draft["id"], "connection_id": 6, "channel": "telegram"},
        )
        assert r_send.status_code == 200
        assert r_send.json()["status"] == "approved"

        r_gstatus = await client.get(f"/api/v1/integrations/bitrix/greetings/{draft['id']}/status")
        assert r_gstatus.status_code == 200
        assert r_gstatus.json()["status"] == "sent"

        r_batch = await client.post(
            "/api/v1/integrations/bitrix/messaging/batch",
            json={
                "contact_ids": [9, 10],
                "channel": "telegram",
                "connection_id": 6,
                "body_template": "Привет, {name}!",
                "purpose": "marketing",
                "idempotency_key": "batch-rest-001",
            },
        )
        assert r_batch.status_code == 202
        assert r_batch.json()["batch_id"] == 88
        assert r_batch.json()["skipped_no_consent_count"] == 1

        r_bstatus = await client.get("/api/v1/integrations/bitrix/messaging/status/88")
        assert r_bstatus.status_code == 200
        assert r_bstatus.json()["scheduled_count"] == 1

        r_consent = await client.post(
            "/api/v1/integrations/bitrix/contacts/9/consents",
            json={
                "channel": "telegram",
                "purpose": "marketing",
                "action": "grant",
                "basis": "Письменное согласие",
                "source_evidence": "doc://consent-9",
            },
        )
        assert r_consent.status_code == 201
        assert r_consent.json()["recorded_by_user_id"] == 101
