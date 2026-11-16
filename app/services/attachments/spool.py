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

import fcntl
import hashlib
import os
import re
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from app.services.attachments.cipher import ExportArtifactUnavailable, ExportCipher

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
        encryption_key: str | None = None,
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
        self._encryption_key = encryption_key

    def _cipher(self) -> ExportCipher:
        key = self._encryption_key
        if key is None:
            from app.core.config import settings

            key = settings.CREDENTIALS_KEY
        try:
            return ExportCipher(key, max_bytes=self.max_file_bytes)
        except ExportArtifactUnavailable:
            raise SpoolRejected("spool_key_unavailable") from None

    @contextmanager
    def _locked(self):
        """Serialize spool admission and cleanup across workers sharing a root."""
        self.root_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root_dir, 0o700)
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.root_dir / ".spool.lock", flags, 0o600)
        try:
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

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

    def _paths(self, tenant_id: int, item_id: str, position: int, *, create: bool = False) -> tuple[Path, Path]:
        if type(position) is not int or position < 0:
            raise SpoolRejected("invalid_position")
        scope = self._scope_dir(tenant_id, item_id, create=create)
        stem = f"p{position}"
        return scope / stem, scope / f"{stem}.meta"

    # --- public API -----------------------------------------------------

    def put(self, tenant_id: int, item_id: str, position: int, data: bytes, *, mime: str) -> SpooledAttachment:
        """Atomically check the set budget and write a bounded encrypted payload."""
        with self._locked():
            return self._put_locked(tenant_id, item_id, position, data, mime=mime)

    def _put_locked(self, tenant_id: int, item_id: str, position: int, data: bytes, *, mime: str) -> SpooledAttachment:
        if type(data) is not bytes or not data:
            raise SpoolRejected("empty_attachment")
        if len(data) > self.max_file_bytes:
            raise SpoolRejected("attachment_too_large")
        if type(mime) is not str or not mime.strip() or any(c in mime for c in "\x00\r\n") or len(mime) > 128:
            raise SpoolRejected("invalid_mime")
        # Seal before opening a file: a missing or invalid key never writes plaintext.
        encrypted = self._cipher().seal(data)
        self._purge_expired_locked()
        payload, meta = self._paths(tenant_id, item_id, position, create=True)
        if payload.exists() or meta.exists():
            raise SpoolRejected("attachment_position_occupied")
        if self._total_bytes(self._scope_dir(tenant_id, item_id)) + len(data) > self.max_total_bytes:
            raise SpoolRejected("attachment_set_too_large")
        digest = encrypted.payload_sha256.removeprefix("sha256:")
        try:
            _write_private(payload, encrypted.token.encode("ascii"))
            _write_private(meta, _META_SEPARATOR.join((digest, str(len(data)), mime, str(int(time.time())))).encode())
        except OSError:
            payload.unlink(missing_ok=True)
            meta.unlink(missing_ok=True)
            raise SpoolRejected("attachment_write_failed") from None
        return SpooledAttachment(sha256=digest, size=len(data), mime=mime)

    def read(self, tenant_id: int, item_id: str, position: int, *, expected_sha256: str) -> bytes:
        """Decrypt and recheck the exact plaintext; never re-render on failure."""
        with self._locked():
            return self._read_locked(tenant_id, item_id, position, expected_sha256=expected_sha256)

    def _read_locked(self, tenant_id: int, item_id: str, position: int, *, expected_sha256: str) -> bytes:
        if type(expected_sha256) is not str or not _HASH.match(expected_sha256):
            raise SpoolRejected("invalid_hash")
        payload, meta = self._paths(tenant_id, item_id, position)
        try:
            stored = meta.read_text(encoding="ascii").split(_META_SEPARATOR)
            if len(stored) != 4 or not _HASH.fullmatch(stored[0]):
                raise ValueError
            size, created = int(stored[1]), int(stored[3])
            if size <= 0 or size > self.max_file_bytes or created > time.time():
                raise ValueError
            if time.time() - created >= self.ttl_seconds:
                raise SpoolRejected("attachment_expired")
            # Bound disk input before reading or allocating the token.
            max_token = 4 * ((57 + (self.max_file_bytes // 16 + 1) * 16 + 2) // 3)
            if payload.stat().st_size > max_token:
                raise ValueError
            token = payload.read_text(encoding="ascii")
        except FileNotFoundError:
            raise SpoolRejected("attachment_missing") from None
        except SpoolRejected:
            raise
        except (OSError, UnicodeError, ValueError):
            raise SpoolRejected("attachment_corrupt") from None
        try:
            data = self._cipher().open(
                token, expected_hash="sha256:" + stored[0], expected_size=size, ttl_seconds=self.ttl_seconds
            )
        except ExportArtifactUnavailable:
            raise SpoolRejected("attachment_corrupt") from None
        if _digest(data) != expected_sha256:
            raise SpoolRejected("attachment_render_mismatch")
        return data

    def discard(self, tenant_id: int, item_id: str) -> None:
        """Deterministic cleanup after delivery, failure or cancellation."""
        with self._locked():
            self._discard_locked(tenant_id, item_id)

    def _discard_locked(self, tenant_id: int, item_id: str) -> None:
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
        with self._locked():
            return self._purge_expired_locked(now=now)

    def _purge_expired_locked(self, *, now: float | None = None) -> int:
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
            for meta in scope.glob("p*.meta"):
                stem = meta.name[:-5]
                if not stem[1:].isdigit():
                    continue
                fields = meta.read_text(encoding="ascii").split(_META_SEPARATOR)
                if len(fields) != 4 or not _HASH.fullmatch(fields[0]) or int(fields[1]) <= 0:
                    raise ValueError
                total += int(fields[1])
        except (OSError, UnicodeError, ValueError):
            raise SpoolRejected("attachment_corrupt") from None
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
    fd, name = tempfile.mkstemp(prefix=".attachment-", dir=path.parent)
    tmp = Path(name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


attachment_spool = AttachmentSpool()
