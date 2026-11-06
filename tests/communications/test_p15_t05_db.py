"""LOCAL PostgreSQL handoff only; not executed by the remote agent.

Requires owner-applied 0141 and serialized test_schema ownership. All providers
are replaced by AsyncMock. Root conftest stays enabled. Fixtures remove and
verify synthetic outbox/ledger records before personal_world removes its users.
"""

import asyncio
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete, func, select, text

from app.channels.senders.contracts import EMAIL, SendResult
from app.core.access_catalog import workspace_grants
from app.core.config import settings
from app.core.database import async_engine, async_session_maker
from app.core.permissions import permission_scope
from app.core.tenant_context import tenant_scope
from app.models import (
    ActionAuditEvent,
    Contact,
    ContactConsent,
    ContactIdentity,
    TenantUser,
    WorkspaceConnection,
    WorkspaceConnectionSecret,
)
from app.models.actions.business_command import BusinessCommand, BusinessCommandTarget
from app.models.communications.outbox import OutboxMessage, OutboxTarget
from app.services.commands import ledger
from app.services.communications.outbox_contracts import DraftIn, MessageOut, ScheduleIn
from app.services.communications.outbox_store import (
    CLAIM_GRACE,
    OutboxConflict,
    OutboxDispatcher,
    OutboxUnavailable,
    due_after_quiet_hours,
    outbox_store,
)
from app.utils.crypto import encrypt_secret
from tests.shared.fixtures.personal_workspace import personal_world  # noqa: F401

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]
UTC = timezone.utc


def scope(world, workspace=0):
    stack = ExitStack()
    stack.enter_context(tenant_scope(world.tenants[workspace].id))
    stack.enter_context(
        permission_scope(world.users[0 if workspace == 0 else 2], member_grants=workspace_grants("owner"))
    )
    return stack


@pytest.fixture
async def outbox_world(personal_world, monkeypatch):  # noqa: F811
    assert settings.DB_SCHEMA == "test_schema", "P15-T05 DB tests require test_schema"
    monkeypatch.setattr(settings, "PII_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "CREDENTIALS_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(ledger, "RUNTIME_ENABLED_CAPABILITIES", {EMAIL})
    monkeypatch.setattr(ledger, "RUNTIME_KILL_SWITCHES", {})
    world = personal_world
    tenant = world.tenants[0].id
    try:
        with scope(world):
            async with async_session_maker() as session, session.begin():
                contact = Contact(tenant_id=tenant, display_name_encrypted="Synthetic outbox contact")
                connection = WorkspaceConnection(
                    tenant_id=tenant,
                    owner_user_id=world.users[0].id,
                    platform="email",
                    provider_id="mailru_mail",
                    auth_method="app_password",
                    schema_version=1,
                    label="Synthetic outbox",
                    account_key=f"{world.tag}@example.com",
                    config={"account_login": f"{world.tag}@example.com", "send_enabled": True},
                    state="verified",
                    is_active=True,
                )
                session.add_all([contact, connection])
                await session.flush()
                identity = ContactIdentity(
                    tenant_id=tenant,
                    contact_id=contact.id,
                    connection_id=connection.id,
                    kind="email",
                    value_encrypted="synthetic-recipient@example.com",
                    blind_index="1" * 64,
                    verified=True,
                )
                second = ContactIdentity(
                    tenant_id=tenant,
                    contact_id=contact.id,
                    connection_id=connection.id,
                    kind="email",
                    value_encrypted="synthetic-second@example.com",
                    blind_index="2" * 64,
                    verified=True,
                )
                session.add_all(
                    [
                        identity,
                        second,
                        WorkspaceConnectionSecret(
                            connection_id=connection.id, secret_encrypted=encrypt_secret("synthetic-secret")
                        ),
                        ContactConsent(
                            tenant_id=tenant,
                            contact_id=contact.id,
                            channel="email",
                            purpose="transactional",
                            basis_encrypted="synthetic basis",
                            source_evidence_encrypted="synthetic evidence",
                            granted_at=datetime.now(UTC) - timedelta(days=1),
                        ),
                    ]
                )
                await session.flush()
                world.outbox = SimpleNamespace(
                    contact=contact.id, connection=connection.id, identity=identity.id, second=second.id
                )
        yield world
    finally:
        # Deliberately ordered: outbox FK RESTRICT must not obstruct removal of
        # its synthetic command/contact/connection, nor the parent fixture.
        tenant_ids = [row.id for row in world.tenants]
        async with async_session_maker() as session, session.begin():
            for model in (
                OutboxTarget,
                OutboxMessage,
                BusinessCommandTarget,
                BusinessCommand,
                WorkspaceConnection,
                Contact,
                ActionAuditEvent,
            ):
                await session.execute(delete(model).where(model.tenant_id.in_(tenant_ids)))
        async with async_session_maker() as session:
            for model in (
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


async def scheduled(world, *, multi=False, key="synthetic-draft"):
    ref = world.outbox
    targets = [{"channel": "email", "connection_id": ref.connection, "identity_id": ref.identity}]
    if multi:
        targets.append({"channel": "email", "connection_id": ref.connection, "identity_id": ref.second})
    draft = await outbox_store.draft(
        DraftIn(contact_id=ref.contact, body="Synthetic private outbox text", targets=targets), key
    )
    # Includes empty-subject serialization regression (P16-T01 failure class).
    MessageOut.model_validate(draft)
    at = due_after_quiet_hours(datetime.now(UTC) + timedelta(minutes=2), "UTC")
    approved = await outbox_store.schedule(
        ScheduleIn(message_id=draft["id"], expected_content_hash=draft["content_hash"], scheduled_at=at)
    )
    return approved, at


async def test_persisted_empty_subject_ciphertext_projection_and_tenant_isolation(outbox_world):
    world = outbox_world
    with scope(world):
        message, _ = await scheduled(world)
        assert (await outbox_store.get(message["id"]))["subject"] == ""
        schema = async_engine.dialect.identifier_preparer.quote_schema(settings.DB_SCHEMA)
        async with async_session_maker() as session:
            stored = (
                await session.execute(
                    text(f"SELECT content_encrypted,subject_encrypted FROM {schema}.outbox_messages WHERE id=:id"),
                    {"id": message["id"]},
                )
            ).one()
            assert stored[0].startswith("pii:v1:") and stored[1] is None
            assert "Synthetic private" not in repr(stored)
    with scope(world, 1):
        with pytest.raises(OutboxUnavailable):
            await outbox_store.get(message["id"])
        with pytest.raises(OutboxUnavailable):
            await outbox_store.cancel(message["id"])


async def test_two_postgresql_workers_and_confirmed_restart_never_double_send(outbox_world):
    with scope(outbox_world):
        message, at = await scheduled(outbox_world)
        sender = AsyncMock(return_value=SendResult("sent", "smtp_accepted", message_id="<synthetic@example.com>"))
        left, right = OutboxDispatcher(sender=sender), OutboxDispatcher(sender=sender)
        await asyncio.gather(left.run(now=at), right.run(now=at))
        assert sender.await_count == 1
        result = await outbox_store.get(message["id"])
        assert result["targets"][0]["attempts"] == 1 and result["state"] == "sent"
        await OutboxDispatcher(sender=sender).run(now=at + timedelta(minutes=1))
        assert sender.await_count == 1


async def test_crash_after_durable_claim_restarts_as_uncertain_not_approved(outbox_world):
    with scope(outbox_world):
        message, at = await scheduled(outbox_world)
        sender = AsyncMock()
        first = OutboxDispatcher(sender=sender)
        ticket = await first.claim(at)
        restarted = OutboxDispatcher(sender=sender)
        assert await restarted.claim(at) is None
        assert await restarted.recover(at + CLAIM_GRACE) == 1
        assert not await restarted.deliver(ticket, at + CLAIM_GRACE)
        assert (await outbox_store.get(message["id"]))["state"] == "uncertain_delivery"
        assert sender.await_count == 0


async def test_ack_then_outcome_commit_failure_original_ticket_cannot_replay(outbox_world, monkeypatch):
    import app.services.communications.outbox_store as module

    with scope(outbox_world):
        message, at = await scheduled(outbox_world)
        sender = AsyncMock(return_value=SendResult("sent", "smtp_accepted"))
        dispatcher = OutboxDispatcher(sender=sender)
        ticket = await dispatcher.claim(at)
        original = module.persist_outcome

        async def fail_before_commit(*args, **kwargs):
            await original(*args, **kwargs)
            raise RuntimeError("synthetic outcome transaction failure")

        monkeypatch.setattr(module, "persist_outcome", fail_before_commit)
        with pytest.raises(RuntimeError):
            await dispatcher.deliver(ticket, at)
        assert sender.await_count == 1
        monkeypatch.setattr(module, "persist_outcome", original)
        assert not await OutboxDispatcher(sender=sender).deliver(ticket, at)
        await OutboxDispatcher(sender=sender).recover(at + CLAIM_GRACE)
        assert (await outbox_store.get(message["id"]))["state"] == "uncertain_delivery"
        assert sender.await_count == 1


async def test_multitarget_partial_restart_only_dispatches_remaining(outbox_world):
    with scope(outbox_world):
        message, at = await scheduled(outbox_world, multi=True)
        sender = AsyncMock(return_value=SendResult("sent", "smtp_accepted"))
        first = OutboxDispatcher(sender=sender)
        await first.deliver(await first.claim(at), at)
        await OutboxDispatcher(sender=sender).run(now=at)
        assert sender.await_count == 2
        result = await outbox_store.get(message["id"])
        assert [t["state"] for t in result["targets"]] == ["sent", "sent"]


async def test_cancel_vs_claim_serialized_by_postgresql_message_lock(outbox_world):
    with scope(outbox_world):
        message, at = await scheduled(outbox_world)
        dispatcher = OutboxDispatcher(sender=AsyncMock())
        results = await asyncio.gather(outbox_store.cancel(message["id"]), dispatcher.claim(at), return_exceptions=True)
        result = await outbox_store.get(message["id"])
        if result["state"] == "cancelled":
            assert results[1] is None
        else:
            assert isinstance(results[0], OutboxConflict) and result["state"] == "claimed"


async def test_manager_cannot_approve_even_if_interactive_permission_cache_is_owner(outbox_world):
    world = outbox_world
    with scope(world):
        async with async_session_maker() as session, session.begin():
            member = await session.get(TenantUser, world.memberships[0].id)
            member.role_id = world.roles["manager"].id
        draft = await outbox_store.draft(
            DraftIn(
                contact_id=world.outbox.contact,
                body="Synthetic",
                targets=[
                    {"channel": "email", "connection_id": world.outbox.connection, "identity_id": world.outbox.identity}
                ],
            ),
            "manager-draft",
        )
        with pytest.raises(PermissionError):
            await outbox_store.schedule(
                ScheduleIn(
                    message_id=draft["id"],
                    expected_content_hash=draft["content_hash"],
                    scheduled_at=datetime.now(UTC) + timedelta(minutes=2),
                )
            )


async def test_concurrent_duplicate_draft_has_single_unique_intent(outbox_world):
    world = outbox_world
    with scope(world):
        request = DraftIn(
            contact_id=world.outbox.contact,
            body="Synthetic",
            targets=[
                {"channel": "email", "connection_id": world.outbox.connection, "identity_id": world.outbox.identity}
            ],
        )
        left, right = await asyncio.gather(
            outbox_store.draft(request, "same-key"), outbox_store.draft(request, "same-key")
        )
        assert left["id"] == right["id"]
        with pytest.raises(OutboxConflict):
            await outbox_store.draft(request.model_copy(update={"body": "Changed"}), "same-key")


async def test_consent_withdrawal_blocks_preclaimed_send(outbox_world):
    world = outbox_world
    with scope(world):
        message, at = await scheduled(world)
        sender = AsyncMock()
        dispatcher = OutboxDispatcher(sender=sender)
        ticket = await dispatcher.claim(at)
        async with async_session_maker() as session, session.begin():
            contact = await session.get(Contact, world.outbox.contact, with_for_update=True)
            consent = await session.scalar(select(ContactConsent).where(ContactConsent.contact_id == contact.id))
            consent.withdrawn_at = at
        await dispatcher.deliver(ticket, at)
        assert sender.await_count == 0 and (await outbox_store.get(message["id"]))["state"] == "failed"
