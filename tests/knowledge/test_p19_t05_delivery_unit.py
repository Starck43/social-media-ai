"""Export/send service ordering and refusals; DB behavior lives in test_p19_t05_send_db.py.

Pins the phase contract: render once -> draft binds the reference -> the SAME
bytes are spooled -> schedule. Every refusal is a stable code and leaves no
scheduled send behind.
"""

import hashlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.attachments.spool import AttachmentSpool, SpoolRejected
from app.services.communications.attachment_delivery import AttachmentStitchRefused
from app.services.communications.attachments import AttachmentRejected
from app.services.communications.outbox_contracts import TargetIn
from app.services.knowledge import export_delivery as delivery
from app.services.knowledge.documents import DocumentConflict
from app.services.knowledge.export import SpooledExport
from app.services.knowledge.export_contracts import SendDocumentEmailIn

BODY = "Договор №12 согласован.\n\nПодписанная сторона: ООО «Ромашка»."
HASH = "sha256:" + hashlib.sha256(BODY.encode("utf-8")).hexdigest()
DRAFT_HASH = "sha256:" + "b" * 64
IDENTITY = (1, 7, "owner", ("public",))


def _approval(**extra):
    fields = {"id": 5, "tenant_id": 1, "document_id": 3, "revision_id": 9, "revision_hash": HASH, "status": "approved"}
    return SimpleNamespace(**{**fields, **extra})


def _revision(**extra):
    fields = {
        "id": 9,
        "document_id": 3,
        "tenant_id": 1,
        "content_hash": HASH,
        "body_text": BODY,
        "status": "approved",
    }
    return SimpleNamespace(**{**fields, **extra})


def _document(**extra):
    return SimpleNamespace(id=3, tenant_id=1, title="Договор №12", **extra)


@pytest.fixture
def services(monkeypatch):
    """Document-side authorization/locks as doubles; the seam stays real."""
    monkeypatch.setattr(delivery.document_service, "_actor", AsyncMock(return_value=IDENTITY))
    monkeypatch.setattr(delivery.document_service, "_doc", AsyncMock(return_value=_document()))
    monkeypatch.setattr(delivery.document_service, "_revision", AsyncMock(return_value=_revision()))


@pytest.fixture
def session():
    """Phase-A lookups then the seam's three locked lookups, in call order."""
    return SimpleNamespace(scalar=AsyncMock(side_effect=[_approval(), _approval(), _revision(), _document()]))


@pytest.fixture
def outbox(monkeypatch):
    draft = AsyncMock(return_value={"id": 77, "content_hash": DRAFT_HASH, "state": "validated"})
    schedule = AsyncMock(return_value={"id": 77, "content_hash": DRAFT_HASH, "state": "approved"})
    monkeypatch.setattr(delivery.outbox_store, "draft", draft)
    monkeypatch.setattr(delivery.outbox_store, "schedule", schedule)
    return SimpleNamespace(draft=draft, schedule=schedule)


@pytest.fixture
def spool(tmp_path, monkeypatch):
    store = AttachmentSpool(
        tmp_path / "spool", max_file_bytes=1024 * 1024, max_total_bytes=4 * 1024 * 1024, ttl_seconds=600
    )
    monkeypatch.setattr("app.services.attachments.attachment_spool", store)
    return store


def _request(**extra):
    fields = {
        "format": "docx",
        "contact_id": 4,
        "subject": "Счёт на оплату",
        "body": "Договор во вложении",
        "target": TargetIn(channel="email", connection_id=1, identity_id=2),
        "scheduled_at": datetime.now(timezone.utc) + timedelta(minutes=2),
        "ttl_seconds": 3600,
    }
    return SendDocumentEmailIn(**{**fields, **extra})


async def test_non_email_channel_is_refused_before_any_draft(services, outbox):
    request = _request(target=TargetIn(channel="telegram", connection_id=1, identity_id=2))

    with pytest.raises(AttachmentRejected) as caught:
        await delivery.send_document_email(3, request, "key-1", session=SimpleNamespace())

    assert caught.value.code == "channel_cannot_attach"
    outbox.draft.assert_not_called()
    outbox.schedule.assert_not_called()


async def test_missing_approval_is_refused_before_any_draft(services, outbox):
    session = SimpleNamespace(scalar=AsyncMock(return_value=None))

    with pytest.raises(DocumentConflict, match="approval_not_granted"):
        await delivery.send_document_email(3, _request(), "key-1", session=session)

    outbox.draft.assert_not_called()


async def test_send_renders_once_and_spools_the_drafted_bytes(services, session, outbox, spool):
    scheduled = await delivery.send_document_email(3, _request(), "key-1", session=session)

    assert scheduled["state"] == "approved"
    draft_in = outbox.draft.call_args[0][0]
    (ref,) = draft_in.attachments
    # The draft binds exactly one email target and the rendered reference.
    assert [t.channel for t in draft_in.targets] == ["email"]
    # Phase C spooled the SAME bytes under the outbox scope, verified by hash.
    data = spool.read(1, "outbox_77", 0, expected_sha256=ref.sha256)
    assert hashlib.sha256(data).hexdigest() == ref.sha256
    assert ref.mime.endswith("wordprocessingml.document")
    schedule_in = outbox.schedule.call_args[0][0]
    assert schedule_in.message_id == 77
    assert schedule_in.expected_content_hash == DRAFT_HASH


async def test_occupied_position_with_matching_bytes_completes_the_retry(services, session, outbox, monkeypatch):
    def occupied(*args, **kwargs):
        raise SpoolRejected("attachment_position_occupied")

    monkeypatch.setattr(delivery, "spool_rendered", occupied)
    reads = []
    monkeypatch.setattr(
        "app.services.attachments.attachment_spool",
        SimpleNamespace(read=lambda tenant, item, position, expected_sha256: reads.append(expected_sha256) or b"x"),
    )

    scheduled = await delivery.send_document_email(3, _request(), "key-1", session=session)

    assert scheduled["state"] == "approved"
    draft_in = outbox.draft.call_args[0][0]
    assert reads == [draft_in.attachments[0].sha256]


async def test_occupied_position_with_foreign_bytes_is_refused(services, session, outbox, monkeypatch):
    def occupied(*args, **kwargs):
        raise SpoolRejected("attachment_position_occupied")

    def reject(*args, **kwargs):
        raise SpoolRejected("attachment_hash_mismatch")

    monkeypatch.setattr(delivery, "spool_rendered", occupied)
    monkeypatch.setattr("app.services.attachments.attachment_spool", SimpleNamespace(read=reject))

    with pytest.raises(AttachmentStitchRefused, match="attachment_position_conflict"):
        await delivery.send_document_email(3, _request(), "key-1", session=session)

    outbox.schedule.assert_not_called()


async def test_spool_hash_disagreement_never_schedules(services, session, outbox, monkeypatch):
    monkeypatch.setattr(
        delivery,
        "spool_rendered",
        lambda content, mime, **kwargs: SpooledExport(
            sha256="0" * 64, size=len(content), mime=mime, revision_id=9, revision_body_hash=HASH
        ),
    )

    with pytest.raises(AttachmentStitchRefused, match="attachment_hash_mismatch"):
        await delivery.send_document_email(3, _request(), "key-1", session=session)

    outbox.schedule.assert_not_called()


async def test_export_renders_the_locked_current_revision(services):
    session = SimpleNamespace()

    exported = await delivery.export_document(3, "docx", session=session)

    assert exported.revision_id == 9
    assert exported.content_hash == HASH
    assert exported.media_type.endswith("wordprocessingml.document")
    assert len(exported.content) > 0


@pytest.mark.parametrize(
    "kwargs",
    [{"expected_revision_id": 4}, {"expected_content_hash": "sha256:" + "a" * 64}],
)
async def test_export_staleness_guard_refuses(services, kwargs):
    with pytest.raises(DocumentConflict, match="document_stale"):
        await delivery.export_document(3, "docx", session=SimpleNamespace(), **kwargs)
