"""Approved revision -> transient spool -> outbox reference (P19-T05 / P15-T14 / D9a).

The seam must be exact: what the renderer spools is what the outbox later reads,
rechecks and deletes. No PostgreSQL bytes, no path, no re-render on retry.
"""

import hashlib
import re
from unittest.mock import patch

import pytest

from app.services.attachments.spool import AttachmentSpool, SpoolRejected
from app.services.knowledge.export import (
    ExportError,
    ExportRevision,
    spool_revision,
)

BODY = "Уважаемый клиент!\n\nДоговор №12 подписан. \nКонфиденциально."


def _revision(**extra):
    return ExportRevision(
        revision_id=41,
        content_hash="sha256:" + hashlib.sha256(BODY.encode("utf-8")).hexdigest(),
        title="Договор №12",
        body_text=BODY,
        **extra,
    )


@pytest.fixture
def spool(tmp_path):
    store = AttachmentSpool(
        tmp_path / "spool", max_file_bytes=1024 * 1024, max_total_bytes=4 * 1024 * 1024, ttl_seconds=600
    )
    with patch("app.services.attachments.attachment_spool", store):
        yield store


def test_docx_is_spooled_and_reads_back_through_the_reference(spool):
    """The seam the outbox will use: read the same item id with the expected hash."""
    exported = spool_revision(_revision(), "docx", tenant_id=1, outbox_message_id=7)

    data = spool.read(1, "outbox_7", 0, expected_sha256=exported.sha256)

    assert hashlib.sha256(data).hexdigest() == exported.sha256
    assert exported.mime.endswith("wordprocessingml.document")


def test_pdf_is_spooled_with_a_pdf_media_type(spool):
    pytest.importorskip("weasyprint", reason="PDF rendering needs the optional renderer installed")
    exported = spool_revision(_revision(), "pdf", tenant_id=1, outbox_message_id=8)

    assert exported.mime == "application/pdf"
    assert exported.size > 0
    assert exported.revision_body_hash == _revision().content_hash


def test_the_reference_carries_no_path_or_bytes(spool):
    exported = spool_revision(_revision(), "docx", tenant_id=1, outbox_message_id=9)

    fields = set(vars(exported))
    assert {"sha256", "size", "mime"} <= fields
    assert not any(isinstance(getattr(exported, f), bytes) for f in fields)
    # A hash is hex only: no directory separator or absolute path can hide there.
    assert re.fullmatch(r"[0-9a-f]{64}", exported.sha256)


def test_an_edited_body_is_refused_before_rendering(spool):
    edited = ExportRevision(
        revision_id=41,
        content_hash="sha256:" + hashlib.sha256(b"other").hexdigest(),
        title="Договор №12",
        body_text=BODY,
    )

    with pytest.raises(ExportError, match="revision_hash_mismatch"):
        spool_revision(edited, "docx", tenant_id=1, outbox_message_id=10)

    # Nothing is written for a refused revision.
    assert not (spool.root_dir / "t_1" / "outbox_10").exists()


def test_a_purged_export_is_a_recorded_refusal_not_a_rerender(spool):
    exported = spool_revision(_revision(), "docx", tenant_id=1, outbox_message_id=11)
    spool.discard(1, "outbox_11")

    with pytest.raises(SpoolRejected, match="attachment_missing"):
        spool.read(1, "outbox_11", 0, expected_sha256=exported.sha256)

    assert not (spool.root_dir / "t_1" / "outbox_11" / "p0").exists()
