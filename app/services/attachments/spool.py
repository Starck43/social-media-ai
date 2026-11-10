"""Transient attachment spool: rendered bytes live from render until dispatch.

Long-term file storage belongs to the owner's own cloud or local store (S3
connector per D9) and never to PostgreSQL, which would grow without bound across
thousands of tenants. This spool therefore holds an approved document's rendered
bytes only long enough to deliver them, then forgets them.

Layout mirrors `app.services.chat_attachments.ChatAttachmentStore` (directories
`0o700`, files `0o600`, no caller-controlled path components, no public URL), but
the scope is `(tenant_id, item_id, position)` so one outbox item's purge cannot
touch another's files, and the TTL and size caps come from settings.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

_HASH = re.compile(r"^[0-9a-f]{64}\Z")
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}\Z")
_META_SEPARATOR = "\n"


class SpoolRejected(ValueError):
    """The bytes or the scope cannot be spooled; nothing was written."""


@dataclass(frozen=True)
class SpooledAttachment:
    """What the outbox stores: an identity, its hash, size and MIME type."""

    sha256: str
    size: int
    mime: str


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class AttachmentSpool:
    """Filesystem spool with TTL expiry and deterministic cleanup.

    A spool is local to one host. Delivery must therefore happen on the process
    that wrote the file, or the spool must live on a shared volume; this project
    runs a single `python -m app.runtime`, so the default is adequate.
    """

    def __init__(
        self,
        root_dir: str | Path | None = None,
        *,
        max_file_bytes: int | None = None,
        max_total_bytes: int | None = None,
        ttl_seconds: int | None = None,
    ):
        if root_dir is None or max_file_bytes is None or max_total_bytes is None or ttl_seconds is None:
            from app.core.config import settings

            root_dir = settings.ATTACHMENT_STORAGE_DIR or (Path(tempfile.gettempdir()) / "social-media-ai-attachments")
            max_file_bytes = settings.ATTACHMENT_MAX_FILE_BYTES if max_file_bytes is None else max_file_bytes
            max_total_bytes = settings.ATTACHMENT_MAX_TOTAL_BYTES if max_total_bytes is None else max_total_bytes
            ttl_seconds = settings.ATTACHMENT_SPOOL_TTL_SECONDS if ttl_seconds is None else ttl_seconds
        self.root_dir = Path(root_dir)
        self.max_file_bytes = int(max_file_bytes)
        self.max_total_bytes = int(max_total_bytes)
        self.ttl_seconds = int(ttl_seconds)

    # --- scope handling -------------------------------------------------

    @staticmethod
    def _id(value: str, name: str) -> str:
        if type(value) is not str or not _SAFE_ID.match(value):
            raise SpoolRejected(f"invalid_{name}")
        return value

    def _scope_dir(self, tenant_id: int, item_id: str, *, create: bool = False) -> Path:
        if type(tenant_id) is not int or tenant_id <= 0:
            raise SpoolRejected("invalid_tenant")
        scope = self.root_dir / f"t_{tenant_id}" / self._id(item_id, "item")
        if create:
            self.root_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(self.root_dir, 0o700)
            scope.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(scope.parent, 0o700)
            scope.mkdir(parents=True, exist_ok=True)
            os.chmod(scope, 0o700)
        return scope

    def _paths(self, tenant_id: int, item_id: str, position: int) -> tuple[Path, Path]:
        if type(position) is not int or position < 0:
            raise SpoolRejected("invalid_position")
        scope = self._scope_dir(tenant_id, item_id, create=True)
        stem = f"p{position}"
        return scope / stem, scope / f"{stem}.meta"

    # --- public API -----------------------------------------------------

    def put(self, tenant_id: int, item_id: str, position: int, data: bytes, *, mime: str) -> SpooledAttachment:
        """Spool one attachment; bounds are enforced before anything is written."""
        if type(data) is not bytes or not data:
            raise SpoolRejected("empty_attachment")
        if len(data) > self.max_file_bytes:
            raise SpoolRejected("attachment_too_large")
        if type(mime) is not str or not mime.strip() or "\x00" in mime or len(mime) > 128:
            raise SpoolRejected("invalid_mime")
        payload, meta = self._paths(tenant_id, item_id, position)
        if self._total_bytes(self._scope_dir(tenant_id, item_id)) + len(data) > self.max_total_bytes:
            raise SpoolRejected("attachment_set_too_large")
        digest = _digest(data)
        _write_private(payload, data)
        _write_private(meta, _META_SEPARATOR.join((digest, str(len(data)), mime, str(int(time.time())))).encode())
        return SpooledAttachment(sha256=digest, size=len(data), mime=mime)

    def read(self, tenant_id: int, item_id: str, position: int, *, expected_sha256: str) -> bytes:
        """Recheck the bytes against the approval on every dispatch attempt."""
        if type(expected_sha256) is not str or not _HASH.match(expected_sha256):
            raise SpoolRejected("invalid_hash")
        payload, meta = self._paths(tenant_id, item_id, position)
        try:
            data = payload.read_bytes()
            stored = meta.read_text(encoding="ascii").split(_META_SEPARATOR)
        except OSError:
            raise SpoolRejected("attachment_missing") from None
        if len(data) > self.max_file_bytes:
            raise SpoolRejected("attachment_too_large")
        if not stored or stored[0] != _digest(data):
            raise SpoolRejected("attachment_corrupt")
        if _digest(data) != expected_sha256:
            raise SpoolRejected("attachment_render_mismatch")
        return data

    def discard(self, tenant_id: int, item_id: str) -> None:
        """Deterministic cleanup after delivery, failure or cancellation."""
        scope = self._scope_dir(tenant_id, item_id)
        if not scope.is_dir():
            return
        for child in scope.iterdir():
            if child.is_file():
                child.unlink(missing_ok=True)
        _remove_empty_children(scope)

    def purge_expired(self, *, now: float | None = None) -> int:
        """Remove files older than the TTL; returns the number of files removed.

        Every caller that admits a new attachment also sweeps, exactly like the
        chat store, so an abandoned render never outlives its TTL.
        """
        if not self.root_dir.is_dir():
            return 0
        cutoff = (time.time() if now is None else now) - self.ttl_seconds
        removed = 0
        for tenant_dir in self.root_dir.iterdir():
            if not tenant_dir.is_dir():
                continue
            for scope in tenant_dir.iterdir():
                if not scope.is_dir():
                    continue
                for child in scope.iterdir():
                    try:
                        if child.is_file() and child.stat().st_mtime < cutoff:
                            child.unlink(missing_ok=True)
                            removed += 1
                    except OSError:
                        continue
                _remove_empty_children(scope)
            _remove_empty_children(tenant_dir)
        return removed

    @staticmethod
    def _total_bytes(scope: Path) -> int:
        total = 0
        try:
            for child in scope.iterdir():
                if child.is_file() and child.name.startswith("p") and child.name.partition(".")[0][1:].isdigit():
                    total += child.stat().st_size
        except OSError:
            return 0
        return total


def _remove_empty_children(directory: Path) -> None:
    """Delete ``directory`` only when it holds nothing.

    Emptiness, not mtime, decides: writing a child updates the directory's own
    mtime, so a TTL comparison would keep an emptied scope forever. A live scope
    always holds its payload and meta files.
    """
    try:
        directory.rmdir()
    except OSError:
        pass


def _write_private(path: Path, data: bytes) -> None:
    """Write atomically with owner-only permissions; never expose a partial file."""
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


attachment_spool = AttachmentSpool()
