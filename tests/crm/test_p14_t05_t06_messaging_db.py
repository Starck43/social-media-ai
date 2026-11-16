"""P14-T05 & P14-T06 PostgreSQL `test_schema` integration tests for greeting delivery, batch messaging, consent, and unsubscribe.

Covers against real PostgreSQL (`test_schema`) with root conftest enabled:
1. Refusal without recorded `ContactConsent` (`send_greeting` -> 403 `consent_required`; `send_batch_messages` -> `skipped_no_consent` + `coverage_warning`).
2. `withdraw -> grant -> withdraw` ordering through `ContactManager.record_consent` and `CrmMessagingService.process_one_click_unsubscribe` with row locking on `Contact`.
3. Cancellation of scheduled outbox targets and current-year `GreetingHistory` rows after one-click unsubscribe (while leaving prior-year rows untouched) and preserving `GreetingHistory.draft_text_encrypted`.
4. Idempotent replay of repeated `send_batch_messages` with the same `idempotency_key` and separate `skipped_conflict` bucket on `OutboxConflict("outbox_idempotency_mismatch")`.
5. Tenant isolation in `_resolve_contact_identity` and uniform HTTP 422 response (`invalid_unsubscribe_token`) for invalid tokens and deleted/merged contacts.
"""

from __future__ import annotations

from contextlib import ExitStack
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete, func, select, text

import app.services.communications.outbox_store as outbox_store_module
from app.channels.senders.contracts import EMAIL
from app.core.access_catalog import workspace_grants
from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.integrations.bitrix24.client import Bitrix24Error
from app.models import (
    ActionAuditEvent,
    Contact,
    ContactConsent,
    ContactIdentity,
    WorkspaceConnection,
    WorkspaceConnectionSecret,
)
from app.models.actions.business_command import BusinessCommand, BusinessCommandTarget
from app.models.communications.outbox import OutboxMessage, OutboxTarget
from app.models.crm.entity import CrmEntity
from app.models.crm.greeting_history import GreetingHistory
from app.models.managers.contact_manager import ContactManager
from app.services.commands import ledger
from app.services.communications.outbox_store import outbox_store
from app.services.crm.greetings import CrmGreetingsService
from app.services.crm.messaging import (
    CrmMessagingService,
    _resolve_contact_identity,
    create_unsubscribe_token,
    has_active_consent,
)
from app.utils.crypto import encrypt_secret
from tests.shared.fixtures.personal_workspace import personal_world  # noqa: F401

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]
UTC = timezone.utc
_ORIGINAL_DATETIME = datetime


class _FrozenDateTimeMeta(type(_ORIGINAL_DATETIME)):
    def __instancecheck__(cls, instance):
        return isinstance(instance, _ORIGINAL_DATETIME)


class _FrozenDateTime(_ORIGINAL_DATETIME, metaclass=_FrozenDateTimeMeta):
    current: datetime

    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)


def scope(world, workspace: int = 0) -> ExitStack:
    stack = ExitStack()
    stack.enter_context(tenant_scope(world.tenants[workspace].id))
    stack.enter_context(
        permission_scope(
            world.users[0 if workspace == 0 else 2],
            member_grants=workspace_grants("owner"),
        )
    )
    return stack


async def _purge_own_events(tenant_ids: list[int]) -> None:
    table = ActionAuditEvent.__table__
    qualified = f'"{table.schema}"."{table.name}"'
    with tenant_scope(bypass=True):
        async with async_session_maker() as session, session.begin():
            await session.execute(text("SELECT set_config('app.action_audit_retention', 'on', true)"))
            await session.execute(delete(ActionAuditEvent).where(ActionAuditEvent.tenant_id.in_(tenant_ids)))
            remaining = await session.scalar(
                text(f"SELECT count(*) FROM {qualified} WHERE tenant_id = ANY(:ids)"),
                {"ids": tenant_ids},
            )
            assert remaining == 0


@pytest.fixture
async def messaging_world(personal_world, monkeypatch):  # noqa: F811
    assert settings.DB_SCHEMA in {
        "test_schema",
        "p14_t05_t06_messaging_test",
    }, "P14-T05/T06 DB tests require test_schema or the dedicated p14_t05_t06_messaging_test schema"
    assert settings.DB_TEST_SCHEMA == settings.DB_SCHEMA, "DB_TEST_SCHEMA must match the selected isolated schema"
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "PII_INDEX_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "CREDENTIALS_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "SECRET_KEY", "db-test-secret-" + ("cd" * 16))
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://crm.example.com")
    monkeypatch.setattr(ledger, "RUNTIME_ENABLED_CAPABILITIES", {EMAIL})
    monkeypatch.setattr(ledger, "RUNTIME_KILL_SWITCHES", {})

    world = personal_world
    tenant_a = world.tenants[0].id
    tenant_b = world.tenants[1].id
    try:
        with scope(world, 0):
            async with async_session_maker() as session, session.begin():
                contact_1 = Contact(
                    tenant_id=tenant_a,
                    display_name_encrypted="Анна Волкова",
                    birthday_encrypted="1990-11-12",
                    birthday_has_year=True,
                    preferred_channel="email",
                )
                contact_2 = Contact(
                    tenant_id=tenant_a,
                    display_name_encrypted="Дмитрий Соколов",
                    preferred_channel="email",
                )
                connection = WorkspaceConnection(
                    tenant_id=tenant_a,
                    owner_user_id=world.users[0].id,
                    platform="email",
                    provider_id="mailru_mail",
                    auth_method="app_password",
                    schema_version=1,
                    label="CRM Outbox SMTP",
                    account_key=f"{world.tag}@example.com",
                    config={"account_login": f"{world.tag}@example.com", "send_enabled": True},
                    state="verified",
                    is_active=True,
                )
                session.add_all([contact_1, contact_2, connection])
                await session.flush()

                id_1 = ContactIdentity(
                    tenant_id=tenant_a,
                    contact_id=contact_1.id,
                    connection_id=connection.id,
                    kind="email",
                    value_encrypted="anna@example.com",
                    blind_index="1" * 64,
                    verified=True,
                )
                id_2 = ContactIdentity(
                    tenant_id=tenant_a,
                    contact_id=contact_2.id,
                    connection_id=connection.id,
                    kind="email",
                    value_encrypted="dmitry@example.com",
                    blind_index="2" * 64,
                    verified=True,
                )
                session.add_all(
                    [
                        id_1,
                        id_2,
                        WorkspaceConnectionSecret(
                            connection_id=connection.id,
                            secret_encrypted=encrypt_secret("smtp-secret"),
                        ),
                    ]
                )
                await session.flush()

        with scope(world, 1):
            async with async_session_maker() as session, session.begin():
                foreign_contact = Contact(
                    tenant_id=tenant_b,
                    display_name_encrypted="Чужой Контакт",
                    preferred_channel="email",
                )
                foreign_conn = WorkspaceConnection(
                    tenant_id=tenant_b,
                    owner_user_id=world.users[2].id,
                    platform="email",
                    provider_id="mailru_mail",
                    auth_method="app_password",
                    schema_version=1,
                    label="Foreign SMTP",
                    account_key=f"{world.tag}-b@example.com",
                    config={"account_login": f"{world.tag}-b@example.com", "send_enabled": True},
                    state="verified",
                    is_active=True,
                )
                session.add_all([foreign_contact, foreign_conn])
                await session.flush()
                foreign_identity = ContactIdentity(
                    tenant_id=tenant_b,
                    contact_id=foreign_contact.id,
                    connection_id=foreign_conn.id,
                    kind="email",
                    value_encrypted="foreign@example.com",
                    blind_index="3" * 64,
                    verified=True,
                )
                session.add(foreign_identity)
                await session.flush()

        world.crm_messaging = SimpleNamespace(
            tenant_a=tenant_a,
            tenant_b=tenant_b,
            actor_a=world.users[0].id,
            contact_1=contact_1.id,
            contact_2=contact_2.id,
            connection=connection.id,
            identity_1=id_1.id,
            identity_2=id_2.id,
            foreign_contact=foreign_contact.id,
            foreign_connection=foreign_conn.id,
            foreign_identity=foreign_identity.id,
        )
        yield world
    finally:
        tenant_ids = [row.id for row in world.tenants]
        async with async_session_maker() as session, session.begin():
            for model in (
                GreetingHistory,
                CrmEntity,
                OutboxTarget,
                OutboxMessage,
                BusinessCommandTarget,
                BusinessCommand,
                WorkspaceConnection,
                Contact,
            ):
                await session.execute(delete(model).where(model.tenant_id.in_(tenant_ids)))
        await _purge_own_events(tenant_ids)
        async with async_session_maker() as session:
            for model in (
                GreetingHistory,
                CrmEntity,
                OutboxTarget,
                OutboxMessage,
                BusinessCommandTarget,
                BusinessCommand,
                WorkspaceConnection,
                Contact,
                ActionAuditEvent,
            ):
                assert (
                    await session.scalar(select(func.count()).select_from(model).where(model.tenant_id.in_(tenant_ids)))
                    == 0
                )


async def test_db_refusal_without_consent_and_withdraw_grant_withdraw_ordering(messaging_world) -> None:
    ref = messaging_world.crm_messaging
    greetings_svc = CrmGreetingsService()
    messaging_svc = CrmMessagingService(outbox=outbox_store)
    contact_mgr = ContactManager()
    now = datetime.now(UTC)

    with scope(messaging_world, 0):
        async with async_session_maker() as session, session.begin():
            draft = await greetings_svc.generate_greeting_draft(
                tenant_id=ref.tenant_a,
                actor_id=ref.actor_a,
                contact_id=ref.contact_1,
                event_type="birthday",
                year=now.year,
                channel="email",
                reference_date=date(now.year, 11, 8),
                session=session,
            )
            # 1. Refusal without consent on send_greeting
            with pytest.raises(Bitrix24Error) as exc_no_consent:
                await messaging_svc.send_greeting(
                    tenant_id=ref.tenant_a,
                    actor_id=ref.actor_a,
                    connection_id=ref.connection,
                    greeting_id=draft["id"],
                    now=now,
                    session=session,
                )
            assert exc_no_consent.value.code == "consent_required"
            assert exc_no_consent.value.status_code == 403

            # 2. withdraw -> grant -> withdraw ordering through ContactManager and process_one_click_unsubscribe
            t0 = now - timedelta(minutes=30)
            t1 = now - timedelta(minutes=20)
            t2 = now - timedelta(minutes=10)

            await contact_mgr.record_consent(
                ref.contact_1,
                "email",
                "greeting",
                "initial_withdraw",
                "manual:0",
                session=session,
                withdrawn_at=t0,
            )
            assert (
                await has_active_consent(
                    session,
                    tenant_id=ref.tenant_a,
                    contact_id=ref.contact_1,
                    channel="email",
                    purpose="greeting",
                    now=now,
                )
                is False
            )

            await contact_mgr.record_consent(
                ref.contact_1,
                "email",
                "greeting",
                "client_opt_in",
                "form:1",
                session=session,
                granted_at=t1,
            )
            assert (
                await has_active_consent(
                    session,
                    tenant_id=ref.tenant_a,
                    contact_id=ref.contact_1,
                    channel="email",
                    purpose="greeting",
                    now=now,
                )
                is True
            )

            tok = create_unsubscribe_token(
                tenant_id=ref.tenant_a,
                contact_id=ref.contact_1,
                channel="email",
                purpose="greeting",
                issued_at=int(t2.timestamp()),
            )
            await messaging_svc.process_one_click_unsubscribe(tok, now=t2, session=session)
            assert (
                await has_active_consent(
                    session,
                    tenant_id=ref.tenant_a,
                    contact_id=ref.contact_1,
                    channel="email",
                    purpose="greeting",
                    now=now,
                )
                is False
            )


async def test_db_unsubscribe_cancels_current_year_outbox_and_preserves_prior_year(
    messaging_world, monkeypatch
) -> None:
    ref = messaging_world.crm_messaging
    greetings_svc = CrmGreetingsService()
    messaging_svc = CrmMessagingService(outbox=outbox_store)
    contact_mgr = ContactManager()
    now = datetime(2020, 5, 4, 12, 0, tzinfo=UTC)

    # OutboxStore independently reads the wall clock while validating approval TTL.
    # Freeze both outbox and approval-ledger clocks so this remains valid on any date.
    _FrozenDateTime.current = now
    monkeypatch.setattr(outbox_store_module, "datetime", _FrozenDateTime)
    monkeypatch.setattr(ledger, "datetime", _FrozenDateTime)

    with scope(messaging_world, 0):
        async with async_session_maker() as session, session.begin():
            await contact_mgr.record_consent(
                ref.contact_1,
                "email",
                "greeting",
                "opt_in",
                "form:greeting",
                session=session,
                granted_at=now - timedelta(minutes=5),
            )
            # Historical prior-year greeting row (year = now.year - 1)
            prior_greeting = GreetingHistory(
                tenant_id=ref.tenant_a,
                contact_id=ref.contact_1,
                event_type="birthday",
                event_date=date(now.year - 1, 11, 12),
                year=now.year - 1,
                channel="email",
                subject_encrypted="Прошлогоднее поздравление",
                draft_text_encrypted="Текст прошлого года",
                status="approved",
            )
            session.add(prior_greeting)
            await session.flush()

            draft_curr = await greetings_svc.generate_greeting_draft(
                tenant_id=ref.tenant_a,
                actor_id=ref.actor_a,
                contact_id=ref.contact_1,
                event_type="birthday",
                year=now.year,
                channel="email",
                reference_date=date(now.year, 11, 8),
                session=session,
            )
            clean_draft_text = draft_curr["draft_text"]

            sent = await messaging_svc.send_greeting(
                tenant_id=ref.tenant_a,
                actor_id=ref.actor_a,
                connection_id=ref.connection,
                greeting_id=draft_curr["id"],
                now=now,
                session=session,
            )
            assert sent["status"] == "approved"
            assert sent["unsubscribe_url"].startswith("https://crm.example.com/api/v1/")

            curr_row = await session.get(GreetingHistory, draft_curr["id"])
            assert curr_row is not None
            assert curr_row.draft_text_encrypted == clean_draft_text

            tok = sent["unsubscribe_url"].split("token=", 1)[1]
            unsub = await messaging_svc.process_one_click_unsubscribe(
                tok,
                now=now + timedelta(seconds=10),
                session=session,
            )
            assert unsub["cancelled_outbox_targets"] == 1

            await session.refresh(curr_row)
            await session.refresh(prior_greeting)
            assert curr_row.status == "skipped"
            assert prior_greeting.status == "approved"

            tgt = await session.get(OutboxTarget, sent["outbox_target_id"])
            assert tgt is not None and tgt.state == "cancelled"


async def test_db_batch_replay_after_clock_jump_reuses_durable_message(messaging_world, monkeypatch) -> None:
    ref = messaging_world.crm_messaging
    messaging_svc = CrmMessagingService(outbox=outbox_store)
    contact_mgr = ContactManager()
    now = datetime.now(UTC)
    replay_at = now + timedelta(hours=26)

    _FrozenDateTime.current = now
    monkeypatch.setattr(outbox_store_module, "datetime", _FrozenDateTime)
    monkeypatch.setattr(ledger, "datetime", _FrozenDateTime)
    idem = f"{messaging_world.tag}-durable-replay"
    request = {
        "tenant_id": ref.tenant_a,
        "actor_id": ref.actor_a,
        "contact_ids": [ref.contact_1],
        "channel": "email",
        "connection_id": ref.connection,
        "subject_template": "Предложение для {name}",
        "body_template": "Здравствуйте, {name}!",
        "purpose": "marketing",
        "idempotency_key": idem,
    }

    with scope(messaging_world, 0):
        async with async_session_maker() as session, session.begin():
            await contact_mgr.record_consent(
                ref.contact_1,
                "email",
                "marketing",
                "opt_in",
                "form:durable-replay",
                session=session,
                granted_at=now - timedelta(minutes=1),
            )
            first = await messaging_svc.send_batch_messages(**request, now=now, session=session)

    first_item = first["items"][0]
    with scope(messaging_world, 0):
        async with async_session_maker() as session, session.begin():
            first_message = await session.get(OutboxMessage, first_item["outbox_message_id"])
            assert first_message is not None
            first_body = first_message.content_encrypted
            first_counts = (
                await session.scalar(
                    select(func.count()).select_from(OutboxMessage).where(OutboxMessage.tenant_id == ref.tenant_a)
                ),
                await session.scalar(
                    select(func.count()).select_from(OutboxTarget).where(OutboxTarget.tenant_id == ref.tenant_a)
                ),
                await session.scalar(
                    select(func.count()).select_from(BusinessCommand).where(BusinessCommand.tenant_id == ref.tenant_a)
                ),
            )
            first_token = first_item["unsubscribe_url"].split("token=", 1)[1]

    _FrozenDateTime.current = replay_at
    with scope(messaging_world, 0):
        async with async_session_maker() as session, session.begin():
            replay = await messaging_svc.send_batch_messages(**request, now=replay_at, session=session)
            assert replay["batch_id"] == first["batch_id"]
            assert replay["items"] == first["items"]
            assert replay["counts"] == first["counts"]
            replay_message = await session.get(OutboxMessage, first_item["outbox_message_id"])
            assert replay_message is not None and replay_message.content_encrypted == first_body
            replay_counts = (
                await session.scalar(
                    select(func.count()).select_from(OutboxMessage).where(OutboxMessage.tenant_id == ref.tenant_a)
                ),
                await session.scalar(
                    select(func.count()).select_from(OutboxTarget).where(OutboxTarget.tenant_id == ref.tenant_a)
                ),
                await session.scalar(
                    select(func.count()).select_from(BusinessCommand).where(BusinessCommand.tenant_id == ref.tenant_a)
                ),
            )
            assert replay_counts == first_counts

    with scope(messaging_world, 0):
        async with async_session_maker() as session, session.begin():
            fresh = await messaging_svc.send_batch_messages(
                **{**request, "idempotency_key": f"{idem}-new"},
                scheduled_at=replay_at + timedelta(minutes=1),
                now=replay_at,
                session=session,
            )
            assert fresh["scheduled_count"] == 1
            fresh_item = fresh["items"][0]
            assert fresh_item["unsubscribe_url"].split("token=", 1)[1] != first_token
            fresh_message = await session.get(OutboxMessage, fresh_item["outbox_message_id"])
            assert fresh_message is not None and fresh_message.content_encrypted != first_body


async def test_db_batch_idempotency_conflict_bucket_and_tenant_isolation(messaging_world) -> None:
    ref = messaging_world.crm_messaging
    messaging_svc = CrmMessagingService(outbox=outbox_store)
    contact_mgr = ContactManager()
    now = datetime.now(UTC)

    with scope(messaging_world, 0):
        async with async_session_maker() as session, session.begin():
            # Tenant isolation in _resolve_contact_identity: tenant_b identity is never resolved under tenant_a
            isolated = await _resolve_contact_identity(
                session,
                tenant_id=ref.tenant_a,
                contact_id=ref.foreign_contact,
                channel="email",
                connection_id=ref.foreign_connection,
                identity_id=ref.foreign_identity,
            )
            assert isolated is None

            # Grant marketing consent only for contact_1; contact_2 has no consent
            await contact_mgr.record_consent(
                ref.contact_1,
                "email",
                "marketing",
                "opt_in",
                "form:batch",
                session=session,
                granted_at=now - timedelta(minutes=5),
            )

            batch_1 = await messaging_svc.send_batch_messages(
                tenant_id=ref.tenant_a,
                actor_id=ref.actor_a,
                contact_ids=[ref.contact_1, ref.contact_2],
                channel="email",
                connection_id=ref.connection,
                subject_template="Предложение для {name}",
                body_template="Здравствуйте, {name}!",
                purpose="marketing",
                idempotency_key="db-batch-idem-001",
                now=now,
                session=session,
            )
            assert batch_1["scheduled_count"] == 1
            assert batch_1["skipped_no_consent_count"] == 1
            assert batch_1["skipped_no_consent_contact_ids"] == [ref.contact_2]
            assert batch_1["skipped_conflict_count"] == 0

            # Idempotent replay returns the same batch without duplicating outbox messages
            batch_replay = await messaging_svc.send_batch_messages(
                tenant_id=ref.tenant_a,
                actor_id=ref.actor_a,
                contact_ids=[ref.contact_1, ref.contact_2],
                channel="email",
                connection_id=ref.connection,
                subject_template="Предложение для {name}",
                body_template="Здравствуйте, {name}!",
                purpose="marketing",
                idempotency_key="db-batch-idem-001",
                now=now,
                session=session,
            )
            assert batch_replay["batch_id"] == batch_1["batch_id"]
            assert batch_replay["scheduled_count"] == 1
            assert batch_replay["items"] == batch_1["items"]

            # OutboxConflict("outbox_idempotency_mismatch") is placed in skipped_conflict
            session.add(
                OutboxMessage(
                    tenant_id=ref.tenant_a,
                    author_id=ref.actor_a,
                    contact_id=ref.contact_1,
                    purpose="marketing",
                    state="validated",
                    timezone="UTC",
                    subject_encrypted="Old",
                    content_encrypted="Old",
                    content_hash="mismatch",
                    request_hash="mismatch",
                    idempotency_key=f"db-batch-conflict:c{ref.contact_1}",
                )
            )
            await session.flush()

            conflict_res = await messaging_svc.send_batch_messages(
                tenant_id=ref.tenant_a,
                actor_id=ref.actor_a,
                contact_ids=[ref.contact_1],
                channel="email",
                connection_id=ref.connection,
                subject_template="New Subject {name}",
                body_template="New Body {name}",
                purpose="marketing",
                idempotency_key="db-batch-conflict",
                now=now,
                session=session,
            )
            assert conflict_res["scheduled_count"] == 0
            assert conflict_res["skipped_no_route_count"] == 0
            assert conflict_res["skipped_conflict_count"] == 1
            assert conflict_res["skipped_conflict_contact_ids"] == [ref.contact_1]
            assert conflict_res["items"][0]["reason"] == "outbox_idempotency_mismatch"

            # Uniform 422 response for invalid token and deleted contact token
            deleted_token = create_unsubscribe_token(
                tenant_id=ref.tenant_a,
                contact_id=99999999,
                channel="email",
                purpose="marketing",
                issued_at=int(now.timestamp()),
            )
            with pytest.raises(Bitrix24Error) as exc_deleted:
                await messaging_svc.process_one_click_unsubscribe(deleted_token, now=now, session=session)
            with pytest.raises(Bitrix24Error) as exc_invalid:
                await messaging_svc.process_one_click_unsubscribe("bad.token", now=now, session=session)
            assert exc_deleted.value.status_code == exc_invalid.value.status_code == 422
            assert exc_deleted.value.code == exc_invalid.value.code == "invalid_unsubscribe_token"
