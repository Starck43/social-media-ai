"""Real schedule/claim/deliver transitions with a transient approved attachment."""

import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.channels.senders.contracts import SendResult
from app.core.database import async_session_maker
from app.models.communications.outbox import OutboxAttachmentRef
from app.services.attachments.spool import AttachmentSpool
from app.services.communications import outbox_store as store_module
from app.services.communications.attachments import AttachmentRef
from app.services.communications.outbox_contracts import DraftIn, ScheduleIn
from app.services.communications.outbox_store import (
    CLAIM_GRACE,
    OutboxConflict,
    OutboxDispatcher,
    due_after_quiet_hours,
    outbox_store,
)
from tests.communications.test_p15_t05_db import outbox_world, scope  # noqa: F401
from tests.shared.fixtures.personal_workspace import personal_world  # noqa: F401

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]
PDF = b"%PDF-1.7\nsynthetic approved attachment\n"


@pytest.fixture
def spool(tmp_path, monkeypatch):
    spool = AttachmentSpool(tmp_path / "spool", max_file_bytes=1024, max_total_bytes=4096, ttl_seconds=600)
    monkeypatch.setattr(store_module, "attachment_spool", spool)
    return spool


async def attached(world, spool, *, multi=False, approve=True):
    ref = world.outbox
    targets = [{"channel": "email", "connection_id": ref.connection, "identity_id": ref.identity}]
    if multi:
        targets.append({"channel": "email", "connection_id": ref.connection, "identity_id": ref.second})
    draft = await outbox_store.draft(
        DraftIn(
            contact_id=ref.contact,
            body="Synthetic attachment message",
            targets=targets,
            attachments=[AttachmentRef(sha256=hashlib.sha256(PDF).hexdigest(), size=len(PDF), mime="application/pdf")],
        ),
        "synthetic-attachment-draft",
    )
    spool.put(world.tenants[0].id, f"outbox_{draft['id']}", 0, PDF, mime="application/pdf")
    at = due_after_quiet_hours(datetime.now(timezone.utc) + timedelta(minutes=2), "UTC")
    request = ScheduleIn(message_id=draft["id"], expected_content_hash=draft["content_hash"], scheduled_at=at)
    if approve:
        approved = await outbox_store.schedule(request)
        assert approved["state"] == "approved"
        assert (await outbox_store.schedule(request))["content_hash"] == draft["content_hash"]
    return draft, at, request


@pytest.mark.parametrize("first_outcome", ["sent", "rejected", "unknown"])
async def test_attached_schedule_claim_and_multitarget_delivery(outbox_world, spool, first_outcome):
    world = outbox_world
    with scope(world):
        draft, at, _ = await attached(world, spool, multi=True)
        sender = AsyncMock(
            side_effect=[
                SendResult(first_outcome, "synthetic_outcome", message_id="<first@example.com>"),
                SendResult("sent", "smtp_accepted", message_id="<second@example.com>"),
            ]
        )
        dispatcher = OutboxDispatcher(sender=sender)
        first = await dispatcher.claim(at)
        assert isinstance(first, tuple)
        assert await dispatcher.deliver(first, at)
        assert (
            spool.read(world.tenants[0].id, f"outbox_{draft['id']}", 0, expected_sha256=hashlib.sha256(PDF).hexdigest())
            == PDF
        )
        second = await dispatcher.claim(at)
        assert isinstance(second, tuple) and second[1] != first[1]
        assert await dispatcher.deliver(second, at)
        assert sender.await_count == 2
        for call in sender.await_args_list:
            assert call.args[1].attachments[0].content == PDF
        assert not (spool.root_dir / f"t_{world.tenants[0].id}" / f"outbox_{draft['id']}").exists()
        assert await dispatcher.claim(at) is None
        assert not await dispatcher.deliver(first, at)
        assert sender.await_count == 2


@pytest.mark.parametrize("stage", ["schedule", "claim"])
async def test_changed_attachment_manifest_invalidates_approval(outbox_world, spool, stage):
    world = outbox_world
    with scope(world):
        draft, at, request = await attached(world, spool, approve=stage == "claim")
        async with async_session_maker() as session, session.begin():
            ref = await session.scalar(
                select(OutboxAttachmentRef).where(
                    OutboxAttachmentRef.tenant_id == world.tenants[0].id,
                    OutboxAttachmentRef.outbox_message_id == draft["id"],
                )
            )
            ref.sha256 = "0" * 64
        if stage == "schedule":
            with pytest.raises(OutboxConflict, match="outbox_intent_mismatch"):
                await outbox_store.schedule(request)
        else:
            sender = AsyncMock()
            assert await OutboxDispatcher(sender=sender).claim(at) == {"denied": True}
            sender.assert_not_awaited()
            assert not (spool.root_dir / f"t_{world.tenants[0].id}" / f"outbox_{draft['id']}").exists()


async def test_missing_spool_has_a_distinct_persisted_error_code(outbox_world, spool):
    world = outbox_world
    with scope(world):
        draft, at, _ = await attached(world, spool)
        sender = AsyncMock()
        dispatcher = OutboxDispatcher(sender=sender)
        ticket = await dispatcher.claim(at)
        assert isinstance(ticket, tuple)
        spool.discard(world.tenants[0].id, f"outbox_{draft['id']}")
        assert await dispatcher.deliver(ticket, at)
        result = await outbox_store.get(draft["id"])
        assert result["targets"][0]["error_code"] == "attachment_unavailable"
        sender.assert_not_awaited()


@pytest.mark.parametrize("terminal", ["cancel", "recover"])
async def test_terminal_cancel_or_lost_worker_discards_the_message_spool(outbox_world, spool, terminal):
    world = outbox_world
    with scope(world):
        draft, at, _ = await attached(world, spool)
        sender = AsyncMock()
        dispatcher = OutboxDispatcher(sender=sender)
        if terminal == "cancel":
            await outbox_store.cancel(draft["id"])
        else:
            assert isinstance(await dispatcher.claim(at), tuple)
            assert await dispatcher.recover(at + CLAIM_GRACE) == 1
        assert not (spool.root_dir / f"t_{world.tenants[0].id}" / f"outbox_{draft['id']}").exists()
        sender.assert_not_awaited()
