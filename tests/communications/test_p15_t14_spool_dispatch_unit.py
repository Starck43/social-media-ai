"""Spool read/hash-recheck/cleanup at the outbox dispatch seam (P15-T14 / D9a).

These exercise the fail-closed rules an operator depends on: a re-rendered file
is refused on its hash, a purged file is a recorded refusal (never a re-render),
and delivered bytes are forgotten afterwards. No database and no provider.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services.attachments.spool import AttachmentSpool, SpoolRejected
from app.services.communications.outbox_store import (
    OutboxConflict,
    _discard_attachment_payload,
    _load_attachment_payload,
    _spool_item_id,
)

PDF = b"%PDF-1.7\nsynthetic approved bytes\n"


@pytest.fixture
def spool(tmp_path):
    store = AttachmentSpool(tmp_path / "spool", max_file_bytes=1024, max_total_bytes=4096, ttl_seconds=600)
    with patch("app.services.communications.outbox_store.attachment_spool", store):
        yield store


def _message(message_id=7):
    return SimpleNamespace(id=message_id, tenant_id=1)


def _refs(spool, message_id=7):
    stored = spool.put(1, _spool_item_id(message_id), 0, PDF, mime="application/pdf")
    return [SimpleNamespace(position=0, sha256=stored.sha256, size=stored.size, mime=stored.mime)]


def test_approved_bytes_are_returned_and_verified(spool):
    message = _message()
    refs = _refs(spool)

    payload = _load_attachment_payload(1, message, refs)

    assert len(payload) == 1
    assert payload[0].content == PDF
    assert payload[0].reference.sha256 == refs[0].sha256


def test_purged_file_is_a_recorded_refusal_not_a_rerender(spool):
    message = _message()
    refs = _refs(spool)
    spool.discard(1, _spool_item_id(message.id))

    with pytest.raises(OutboxConflict, match="attachment_unavailable:attachment_missing"):
        _load_attachment_payload(1, message, refs)

    # Nothing was written back: a refusal never silently re-renders.
    assert not (spool.root_dir / "t_1" / _spool_item_id(message.id) / "p0").exists()


def test_re_rendered_file_is_refused_on_the_hash(spool):
    message = _message()
    refs = _refs(spool)
    # Simulate a re-render that replaced the file behind the spool's back: the
    # slot cannot be overwritten through put() (it refuses an occupied position),
    # so tamper at the file level and prove the hash check still catches it.
    payload = spool.root_dir / "t_1" / _spool_item_id(message.id) / "p0"
    payload.chmod(0o600)
    payload.write_bytes(b"%PDF-1.7\ndifferent bytes\n")

    with pytest.raises(OutboxConflict, match="attachment_unavailable"):
        _load_attachment_payload(1, message, refs)


def test_a_partial_set_is_never_delivered(spool):
    """Two approved attachments, one purged -> the whole item is refused."""
    message = _message()
    first = spool.put(1, _spool_item_id(message.id), 0, PDF, mime="application/pdf")
    second = spool.put(1, _spool_item_id(message.id), 1, b"%PDF-1.7\ntwo\n", mime="application/pdf")
    refs = [
        SimpleNamespace(position=0, sha256=first.sha256, size=first.size, mime=first.mime),
        SimpleNamespace(position=1, sha256=second.sha256, size=second.size, mime=second.mime),
    ]
    (spool.root_dir / "t_1" / _spool_item_id(message.id) / "p1").unlink()

    with pytest.raises(OutboxConflict, match="attachment_unavailable"):
        _load_attachment_payload(1, message, refs)


def test_foreign_tenant_cannot_read_the_file(spool):
    message = _message()
    refs = _refs(spool)

    with pytest.raises(OutboxConflict, match="attachment_unavailable:attachment_missing"):
        _load_attachment_payload(2, message, refs)


def test_an_item_without_attachments_reads_nothing(spool):
    assert _load_attachment_payload(1, _message(), []) == ()


def test_discard_removes_the_scope_and_is_idempotent(spool):
    message = _message()
    _refs(spool)

    _discard_attachment_payload(1, message)
    _discard_attachment_payload(1, message)

    scope = spool.root_dir / "t_1" / _spool_item_id(message.id)
    assert not scope.exists() or not any(scope.iterdir())


def test_discard_never_raises_while_unwinding_a_send():
    """Cleanup must not mask the delivery outcome."""
    with patch("app.services.communications.outbox_store.attachment_spool") as broken:
        broken.discard.side_effect = SpoolRejected("invalid_item")
        _discard_attachment_payload(1, _message())

    with patch("app.services.communications.outbox_store.attachment_spool") as broken:
        broken.discard.side_effect = OSError("disk gone")
        _discard_attachment_payload(1, _message())
