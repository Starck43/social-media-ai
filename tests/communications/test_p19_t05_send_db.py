"""Real DB cycle for P19-T05 delivery: approved document -> outbox -> spool -> dispatch.

Reuses the P15-T05 outbox world (tenant, contact, verified email connection) and
the P15-T14 spool discipline. The renderer is real (docx); no provider I/O —
the sender is a double. Synthetic document rows are removed in fixture teardown.
"""

import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, func, select

from app.channels.senders.contracts import SendResult
from app.core.database import async_session_maker
from app.models import DocumentRevision, KnowledgeDocument
from app.models.communications.outbox import OutboxAttachmentRef, OutboxMessage
from app.models.knowledge.approval import DocumentApproval
from app.services.attachments.spool import AttachmentSpool
from app.services.communications import outbox_store as store_module
from app.services.communications.attachment_delivery import AttachmentStitchRefused
from app.services.communications.outbox_contracts import TargetIn
from app.services.communications.outbox_store import OutboxDispatcher, due_after_quiet_hours
from app.services.knowledge.documents import DocumentConflict, DocumentUnavailable
from app.services.knowledge.export_contracts import SendDocumentEmailIn
from app.services.knowledge.export_delivery import export_document, send_document_email
from tests.communications.test_p15_t05_db import outbox_world, scope  # noqa: F401
from tests.shared.fixtures.personal_workspace import personal_world  # noqa: F401

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]

BODY = "Договор №12 согласован.\n\nПодписанная сторона: ООО «Ромашка»."
CONTENT_HASH = "sha256:" + hashlib.sha256(BODY.encode("utf-8")).hexdigest()


@pytest.fixture
def spool(tmp_path, monkeypatch):
    """One store for both seams: the export spool write and the dispatch read."""
    store = AttachmentSpool(
        tmp_path / "spool", max_file_bytes=1024 * 1024, max_total_bytes=4 * 1024 * 1024, ttl_seconds=600
    )
    monkeypatch.setattr(store_module, "attachment_spool", store)
    monkeypatch.setattr("app.services.attachments.attachment_spool", store)
    return store


@pytest.fixture
async def make_document(outbox_world):
    """Create approved (or tampered/unapproved) synthetic documents; remove them after."""
    world = outbox_world

    async def _make(*, approved=True, tamper=False):
        now = datetime.now(timezone.utc)
        async with async_session_maker() as session, session.begin():
            doc = KnowledgeDocument(
                tenant_id=world.tenants[0].id,
                title="Договор №12",
                source_ref="synthetic-p19-t05",
                data_boundary="cloud",
                acl_scope="collection.source.view",
                counterparty="ООО «Ромашка»",
                contact_id=world.outbox.contact,
                owner_user_id=world.users[0].id,
            )
            session.add(doc)
            await session.flush()
            revision = DocumentRevision(
                tenant_id=world.tenants[0].id,
                document_id=doc.id,
                version=1,
                content_hash=CONTENT_HASH,
                body_text=BODY,
                status="approved" if approved else "draft",
                author="synthetic",
                author_user_id=world.users[0].id,
                approved_by=f"user:{world.users[0].id}" if approved else None,
            )
            session.add(revision)
            await session.flush()
            doc.current_revision_id = revision.id
            if approved:
                session.add(
                    DocumentApproval(
                        tenant_id=world.tenants[0].id,
                        document_id=doc.id,
                        revision_id=revision.id,
                        revision_hash=("sha256:" + "c" * 64) if tamper else CONTENT_HASH,
                        requester_user_id=world.users[0].id,
                        approver_user_id=world.users[0].id,
                        status="approved",
                        request_idempotency_key=f"synthetic-p19t05-{doc.id}",
                        request_hash="sha256:" + "d" * 64,
                        decision_hash="sha256:" + "e" * 64,
                        requested_at=now - timedelta(hours=1),
                        responded_at=now - timedelta(minutes=30),
                        expires_at=now + timedelta(hours=1),
                    )
                )
            await session.flush()
            return doc.id, revision.id

    yield _make
    tenant_ids = [row.id for row in world.tenants]
    async with async_session_maker() as session, session.begin():
        await session.execute(delete(DocumentApproval).where(DocumentApproval.tenant_id.in_(tenant_ids)))
        await session.execute(delete(DocumentRevision).where(DocumentRevision.tenant_id.in_(tenant_ids)))
        await session.execute(delete(KnowledgeDocument).where(KnowledgeDocument.tenant_id.in_(tenant_ids)))


def send_request(world):
    ref = world.outbox
    return SendDocumentEmailIn(
        format="docx",
        contact_id=ref.contact,
        subject="Счёт на оплату",
        body="Договор во вложении",
        target=TargetIn(channel="email", connection_id=ref.connection, identity_id=ref.identity),
        scheduled_at=due_after_quiet_hours(datetime.now(timezone.utc) + timedelta(minutes=2), "UTC"),
        ttl_seconds=86400,
    )


async def test_send_schedules_spools_and_delivers_the_approved_bytes(outbox_world, spool, make_document):
    world = outbox_world
    doc_id, revision_id = await make_document()
    request = send_request(world)

    with scope(world):
        scheduled = await send_document_email(doc_id, request, "p19t05-send-1")

    assert scheduled["state"] == "approved"
    message_id = scheduled["id"]
    async with async_session_maker() as session:
        ref = (
            await session.scalars(
                select(OutboxAttachmentRef).where(OutboxAttachmentRef.outbox_message_id == message_id)
            )
        ).one()
    assert ref.mime.endswith("wordprocessingml.document")
    data = spool.read(world.tenants[0].id, f"outbox_{message_id}", ref.position, expected_sha256=ref.sha256)
    assert ref.size == len(data)

    sender = AsyncMock(return_value=SendResult("sent", "smtp_accepted", message_id="<p19t05@example.com>"))
    dispatcher = OutboxDispatcher(sender=sender)
    with scope(world):
        ticket = await dispatcher.claim(request.scheduled_at)
        assert isinstance(ticket, tuple)
        assert await dispatcher.deliver(ticket, request.scheduled_at)
    assert sender.await_count == 1
    # The delivered bytes are exactly the spooled render; the spool is discarded after.
    assert sender.call_args.args[1].attachments[0].content == data
    assert not (spool.root_dir / f"t_{world.tenants[0].id}" / f"outbox_{message_id}").exists()


async def test_unapproved_document_never_reaches_the_outbox(outbox_world, spool, make_document):
    world = outbox_world
    doc_id, _ = await make_document(approved=False)

    with scope(world):
        with pytest.raises(DocumentConflict, match="approval_not_granted"):
            await send_document_email(doc_id, send_request(world), "p19t05-no-approval")

    async with async_session_maker() as session:
        count = await session.scalar(
            select(func.count()).select_from(OutboxMessage).where(OutboxMessage.tenant_id == world.tenants[0].id)
        )
    assert count == 0


async def test_edited_revision_is_refused_on_the_body_hash(outbox_world, spool, make_document):
    world = outbox_world
    doc_id, _ = await make_document(tamper=True)

    with scope(world):
        with pytest.raises(AttachmentStitchRefused, match="revision_changed"):
            await send_document_email(doc_id, send_request(world), "p19t05-tampered")

    async with async_session_maker() as session:
        count = await session.scalar(
            select(func.count()).select_from(OutboxMessage).where(OutboxMessage.tenant_id == world.tenants[0].id)
        )
    assert count == 0


async def test_retry_with_the_same_key_replays_without_a_second_item(outbox_world, spool, make_document):
    world = outbox_world
    doc_id, _ = await make_document()

    with scope(world):
        first = await send_document_email(doc_id, send_request(world), "p19t05-retry")
        second = await send_document_email(doc_id, send_request(world), "p19t05-retry")

    assert first["id"] == second["id"]
    assert second["state"] == "approved"
    async with async_session_maker() as session:
        count = await session.scalar(
            select(func.count()).select_from(OutboxMessage).where(OutboxMessage.tenant_id == world.tenants[0].id)
        )
    assert count == 1


async def test_export_renders_and_guards_staleness_and_tenant_boundaries(outbox_world, spool, make_document):
    world = outbox_world
    doc_id, revision_id = await make_document()

    with scope(world):
        exported = await export_document(doc_id, "docx")
        assert exported.revision_id == revision_id
        assert exported.content_hash == CONTENT_HASH
        assert len(exported.content) > 0
        with pytest.raises(DocumentConflict, match="document_stale"):
            await export_document(doc_id, "docx", expected_revision_id=revision_id + 1000)

    with scope(world, workspace=1):
        with pytest.raises(DocumentUnavailable):
            await export_document(doc_id, "docx")
