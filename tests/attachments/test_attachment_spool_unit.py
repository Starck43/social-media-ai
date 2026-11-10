"""Unit checks for the transient attachment spool (no database, no provider)."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.attachments.policy import resolved_spool
from app.services.attachments.spool import AttachmentSpool, SpoolRejected

PDF = b"%PDF-1.7\nsynthetic document bytes\n"


@pytest.fixture
def spool(tmp_path: Path) -> AttachmentSpool:
    return AttachmentSpool(tmp_path / "spool", max_file_bytes=1024, max_total_bytes=2048, ttl_seconds=600)


def test_put_then_read_returns_exact_bytes_and_hash(spool):
    stored = spool.put(1, "cmd_1", 0, PDF, mime="application/pdf")

    assert stored.size == len(PDF)
    assert stored.mime == "application/pdf"
    assert spool.read(1, "cmd_1", 0, expected_sha256=stored.sha256) == PDF


def test_file_and_directory_are_private(spool):
    spool.put(1, "cmd_1", 0, PDF, mime="application/pdf")
    payload = spool.root_dir / "t_1" / "cmd_1" / "p0"

    assert stat.S_IMODE(os.stat(payload).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(payload.parent).st_mode) == 0o700


def test_render_mismatch_is_refused(spool):
    spool.put(1, "cmd_1", 0, PDF, mime="application/pdf")

    with pytest.raises(SpoolRejected, match="attachment_render_mismatch"):
        spool.read(1, "cmd_1", 0, expected_sha256="0" * 64)


def test_missing_file_is_refused_not_sent(spool):
    with pytest.raises(SpoolRejected, match="attachment_missing"):
        spool.read(1, "cmd_1", 0, expected_sha256="0" * 64)


def test_foreign_tenant_and_item_are_separate_scopes(spool):
    spool.put(1, "cmd_1", 0, PDF, mime="application/pdf")

    with pytest.raises(SpoolRejected, match="attachment_missing"):
        spool.read(2, "cmd_1", 0, expected_sha256="0" * 64)
    with pytest.raises(SpoolRejected, match="attachment_missing"):
        spool.read(1, "cmd_2", 0, expected_sha256="0" * 64)


@pytest.mark.parametrize("item_id", ["../escape", "a/b", "", "x" * 65, "with space"])
def test_item_id_cannot_escape_the_root(spool, item_id):
    with pytest.raises(SpoolRejected, match="invalid_item"):
        spool.put(1, item_id, 0, PDF, mime="application/pdf")


def test_file_over_cap_is_refused_before_writing(spool):
    with pytest.raises(SpoolRejected, match="attachment_too_large"):
        spool.put(1, "cmd_1", 0, b"x" * 1025, mime="application/pdf")

    assert not (spool.root_dir / "t_1" / "cmd_1" / "p0").exists()


def test_total_set_over_cap_is_refused(spool):
    spool.put(1, "cmd_1", 0, b"x" * 1024, mime="application/pdf")

    with pytest.raises(SpoolRejected, match="attachment_set_too_large"):
        spool.put(1, "cmd_1", 1, b"y" * 1024, mime="application/pdf")

    assert not (spool.root_dir / "t_1" / "cmd_1" / "p1").exists()


def test_empty_bytes_and_bad_mime_are_refused(spool):
    with pytest.raises(SpoolRejected, match="empty_attachment"):
        spool.put(1, "cmd_1", 0, b"", mime="application/pdf")
    with pytest.raises(SpoolRejected, match="invalid_mime"):
        spool.put(1, "cmd_1", 0, PDF, mime="\x00")


def test_corrupt_payload_is_refused(spool):
    stored = spool.put(1, "cmd_1", 0, PDF, mime="application/pdf")
    (spool.root_dir / "t_1" / "cmd_1" / "p0").write_bytes(b"tampered")

    with pytest.raises(SpoolRejected, match="attachment_corrupt"):
        spool.read(1, "cmd_1", 0, expected_sha256=stored.sha256)


def test_discard_removes_scope_and_is_idempotent(spool):
    spool.put(1, "cmd_1", 0, PDF, mime="application/pdf")

    spool.discard(1, "cmd_1")
    spool.discard(1, "cmd_1")

    assert not (spool.root_dir / "t_1" / "cmd_1").exists()
    assert not (spool.root_dir / "t_1" / "cmd_1" / "p0").exists()


def test_purge_expired_removes_only_old_files(spool):
    spool.put(1, "old", 0, PDF, mime="application/pdf")
    spool.put(2, "new", 0, PDF, mime="application/pdf")
    old_scope = spool.root_dir / "t_1" / "old"
    for name in ("p0", "p0.meta"):
        os.utime(old_scope / name, (0, 0))

    assert spool.purge_expired() == 2  # payload and meta of the one expired attachment
    assert not (spool.root_dir / "t_1" / "old" / "p0").exists()
    assert not (spool.root_dir / "t_1" / "old").exists()
    assert (spool.root_dir / "t_2" / "new" / "p0").exists()


def test_settings_defaults_and_cross_field_guard():
    defaults = Settings()

    assert defaults.ATTACHMENT_MAX_FILE_BYTES == 5 * 1024 * 1024
    assert defaults.ATTACHMENT_MAX_TOTAL_BYTES == 10 * 1024 * 1024
    assert defaults.ATTACHMENT_SPOOL_TTL_SECONDS == 900
    assert defaults.ATTACHMENT_STORAGE_DIR is None

    with pytest.raises(Exception, match="ATTACHMENT_MAX_TOTAL_BYTES"):
        Settings(ATTACHMENT_MAX_FILE_BYTES=2048, ATTACHMENT_MAX_TOTAL_BYTES=1024)
    with pytest.raises(Exception, match="absolute"):
        Settings(ATTACHMENT_STORAGE_DIR=Path("relative/spool"))


def test_resolved_spool_follows_settings(tmp_path):
    configured = Settings(
        ATTACHMENT_STORAGE_DIR=tmp_path / "custom",
        ATTACHMENT_MAX_FILE_BYTES=64,
        ATTACHMENT_MAX_TOTAL_BYTES=128,
        ATTACHMENT_SPOOL_TTL_SECONDS=60,
    )
    spool = resolved_spool(configured)

    assert spool.root_dir == tmp_path / "custom"
    assert (spool.max_file_bytes, spool.max_total_bytes, spool.ttl_seconds) == (64, 128, 60)


def test_default_root_is_outside_static_and_private(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    spool = AttachmentSpool(None, max_file_bytes=1024, max_total_bytes=2048, ttl_seconds=60)
    spool.put(1, "cmd_1", 0, PDF, mime="application/pdf")

    assert "static" not in str(spool.root_dir)
    assert stat.S_IMODE(os.stat(spool.root_dir).st_mode) == 0o700


def test_spool_matches_chat_store_privacy_model():
    """The chat store's staging model is the precedent; keep the guarantees aligned."""
    import app.services.chat_attachments as chat

    assert chat.STAGED_ATTACHMENT_TTL_SECONDS > 0
    source = Path("app/services/attachments/spool.py").read_text(encoding="utf-8")
    assert "0o700" in source and "0o600" in source
    default_root = AttachmentSpool(None, max_file_bytes=1, max_total_bytes=2, ttl_seconds=3).root_dir
    assert Path(tempfile.gettempdir()).resolve() == default_root.parent.resolve()
