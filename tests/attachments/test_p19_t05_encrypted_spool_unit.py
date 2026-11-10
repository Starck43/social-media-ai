"""The spool persists ciphertext and returns only verified plaintext."""

import os
import time

import pytest
from cryptography.fernet import Fernet

from app.services.attachments.spool import AttachmentSpool, SpoolRejected


@pytest.fixture
def key():
    return Fernet.generate_key().decode("ascii")


@pytest.fixture
def spool(tmp_path, key):
    return AttachmentSpool(
        tmp_path / "spool", max_file_bytes=64, max_total_bytes=128, ttl_seconds=60, encryption_key=key
    )


def test_encrypted_at_rest_and_restart_reads_same_plaintext(spool, key):
    data = b"private synthetic document \x00\xff"
    ref = spool.put(7, "outbox_3", 0, data, mime="application/pdf")
    path = spool.root_dir / "t_7" / "outbox_3" / "p0"
    assert data not in path.read_bytes()
    restarted = AttachmentSpool(
        spool.root_dir, max_file_bytes=64, max_total_bytes=128, ttl_seconds=60, encryption_key=key
    )
    assert restarted.read(7, "outbox_3", 0, expected_sha256=ref.sha256) == data


def test_bad_key_and_tampering_fail_closed(spool):
    ref = spool.put(7, "outbox_3", 0, b"secret", mime="application/pdf")
    wrong = AttachmentSpool(
        spool.root_dir,
        max_file_bytes=64,
        max_total_bytes=128,
        ttl_seconds=60,
        encryption_key=Fernet.generate_key().decode(),
    )
    with pytest.raises(SpoolRejected, match="attachment_corrupt"):
        wrong.read(7, "outbox_3", 0, expected_sha256=ref.sha256)
    payload = spool.root_dir / "t_7" / "outbox_3" / "p0"
    payload.write_bytes(b"tampered")
    with pytest.raises(SpoolRejected, match="attachment_corrupt"):
        spool.read(7, "outbox_3", 0, expected_sha256=ref.sha256)


def test_mismatch_expiry_and_discard_never_rerender(spool):
    spool.put(7, "outbox_3", 0, b"secret", mime="application/pdf")
    with pytest.raises(SpoolRejected, match="attachment_render_mismatch"):
        spool.read(7, "outbox_3", 0, expected_sha256="0" * 64)
    meta = spool.root_dir / "t_7" / "outbox_3" / "p0.meta"
    fields = meta.read_text().split("\n")
    fields[3] = str(int(time.time()) - 60)
    meta.write_text("\n".join(fields))
    with pytest.raises(SpoolRejected, match="attachment_expired"):
        spool.read(7, "outbox_3", 0, expected_sha256=fields[0])
    spool.discard(7, "outbox_3")
    assert not meta.exists()


def test_unavailable_key_never_writes(tmp_path):
    spool = AttachmentSpool(tmp_path, max_file_bytes=64, max_total_bytes=128, ttl_seconds=60, encryption_key="bad")
    with pytest.raises(SpoolRejected, match="spool_key_unavailable"):
        spool.put(7, "outbox_3", 0, b"private", mime="application/pdf")
    assert not list(tmp_path.rglob("p0"))


def test_total_is_plaintext_sizes_not_ciphertext_overhead(spool):
    spool.put(7, "outbox_3", 0, b"a" * 64, mime="application/pdf")
    spool.put(7, "outbox_3", 1, b"b" * 64, mime="application/pdf")
    with pytest.raises(SpoolRejected, match="attachment_set_too_large"):
        spool.put(7, "outbox_3", 2, b"c", mime="application/pdf")


def test_concurrent_writers_cannot_exceed_set_budget(tmp_path, key):
    from concurrent.futures import ThreadPoolExecutor

    root = tmp_path / "shared"

    def put(position):
        worker = AttachmentSpool(root, max_file_bytes=64, max_total_bytes=64, ttl_seconds=60, encryption_key=key)
        try:
            worker.put(7, "outbox_3", position, bytes([65 + position]) * 64, mime="application/pdf")
            return "stored"
        except SpoolRejected as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(put, (0, 1)))
    assert sorted(outcomes) == ["attachment_set_too_large", "stored"]


def test_metadata_cannot_extend_authenticated_token_ttl(spool, key):
    from app.services.attachments.cipher import ExportCipher

    data = b"secret"
    ref = spool.put(7, "outbox_3", 0, data, mime="application/pdf")
    token = ExportCipher(key, max_bytes=64)._cipher.encrypt_at_time(data, int(time.time()) - 61)
    (spool.root_dir / "t_7" / "outbox_3" / "p0").write_bytes(token)
    # The fresh metadata is not enough to bypass the authenticated token timestamp.
    with pytest.raises(SpoolRejected, match="attachment_corrupt"):
        spool.read(7, "outbox_3", 0, expected_sha256=ref.sha256)
