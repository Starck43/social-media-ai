"""Approved revision -> spool stitch: the approval is rechecked before rendering.

These pin the refusals an operator relies on. Every one is a recorded code, never
a silent re-render or an approval-by-trust.
"""

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.attachments.spool import AttachmentSpool
from app.services.communications.attachment_delivery import (
    AttachmentStitchRefused,
    spool_approved_revision,
)

BODY = "Договор №12 согласован.\n\nПодписанная сторона: ООО «Ромашка»."
HASH = "sha256:" + hashlib.sha256(BODY.encode("utf-8")).hexdigest()


@pytest.fixture
def spool(tmp_path):
    store = AttachmentSpool(
        tmp_path / "spool", max_file_bytes=1024 * 1024, max_total_bytes=4 * 1024 * 1024, ttl_seconds=600
    )
    with patch("app.services.attachments.attachment_spool", store):
        yield store


def _session(approval, revision, document=None):
    """Pin the three scalar() lookups in call order."""
    return SimpleNamespace(scalar=AsyncMock(side_effect=[approval, revision, document]))


def _approval(**extra):
    fields = {
        "id": 5,
        "tenant_id": 1,
        "document_id": 3,
        "revision_id": 9,
        "revision_hash": HASH,
        "status": "approved",
    }
    fields.update(extra)
    return SimpleNamespace(**fields)


def _revision(**extra):
    fields = {"id": 9, "document_id": 3, "tenant_id": 1, "content_hash": HASH, "body_text": BODY, "status": "approved"}
    fields.update(extra)
    return SimpleNamespace(**fields)


def _document(**extra):
    return SimpleNamespace(id=3, tenant_id=1, title="Договор №12", **extra)


async def _stitch(session, **extra):
    kwargs = dict(tenant_id=1, document_id=3, revision_id=9, approval_id=5, outbox_message_id=7, format="docx")
    kwargs.update(extra)
    return await spool_approved_revision(session, **kwargs)


@pytest.mark.asyncio
async def test_an_approved_revision_is_spooled_and_reads_back(spool):
    stitched = await _stitch(_session(_approval(), _revision(), _document()))

    data = spool.read(1, "outbox_7", 0, expected_sha256=stitched.reference.sha256)

    assert hashlib.sha256(data).hexdigest() == stitched.reference.sha256
    assert stitched.revision_id == 9
    assert stitched.revision_hash == HASH
    assert stitched.reference.mime.endswith("wordprocessingml.document")


@pytest.mark.asyncio
async def test_an_edited_revision_is_refused_on_the_body_hash(spool):
    edited = _revision(content_hash="sha256:" + "a" * 64)

    with pytest.raises(AttachmentStitchRefused, match="revision_changed"):
        await _stitch(_session(_approval(), edited, _document()))

    assert not (spool.root_dir / "t_1" / "outbox_7").exists()


@pytest.mark.asyncio
async def test_a_body_that_disagrees_with_its_stored_hash_is_refused(spool):
    """The DB check constraint is rechecked here, not trusted blindly."""
    lying = _revision(content_hash=HASH, body_text=BODY + " edited")

    with pytest.raises(AttachmentStitchRefused, match="revision_hash_mismatch"):
        await _stitch(_session(_approval(), lying, _document()))

    assert not (spool.root_dir / "t_1" / "outbox_7").exists()


@pytest.mark.asyncio
async def test_a_pending_approval_is_refused(spool):
    with pytest.raises(AttachmentStitchRefused, match="approval_not_granted"):
        await _stitch(_session(_approval(status="pending"), _revision(), _document()))

    assert not (spool.root_dir / "t_1" / "outbox_7").exists()


@pytest.mark.asyncio
async def test_a_missing_approval_is_refused(spool):
    with pytest.raises(AttachmentStitchRefused, match="approval_unavailable"):
        await _stitch(_session(None, _revision(), _document()))


@pytest.mark.asyncio
async def test_a_revision_no_longer_approved_is_refused(spool):
    with pytest.raises(AttachmentStitchRefused, match="revision_changed"):
        await _stitch(_session(_approval(), _revision(status="revision_requested"), _document()))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"tenant_id": 0},
        {"document_id": 0},
        {"revision_id": -1},
        {"approval_id": 0},
        {"outbox_message_id": 0},
    ],
)
async def test_bad_scope_is_refused_before_any_lookup(spool, kwargs):
    session = SimpleNamespace(scalar=AsyncMock())

    with pytest.raises(AttachmentStitchRefused):
        await _stitch(session, **kwargs)

    session.scalar.assert_not_called()
