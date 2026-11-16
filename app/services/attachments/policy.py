"""Owner-approved transient attachment storage (2026-11-10).

The owner settled two questions raised in PR #451/#454:

1. The limit is configurable, with defaults — `ATTACHMENT_MAX_FILE_BYTES`,
   `ATTACHMENT_MAX_TOTAL_BYTES`, `ATTACHMENT_SPOOL_TTL_SECONDS`,
   `ATTACHMENT_STORAGE_DIR`.
2. Rendered DOCX/PDF bytes are **not** stored in PostgreSQL. Long-term storage is
   the tenant's own cloud or local store (S3 connector per D9); the platform only
   needs the bytes from render to dispatch, so they live in a TTL spool.

A filesystem spool is the most efficient fit here, not a database nor an object
store:

- PostgreSQL would grow without bound across thousands of tenants while the bytes
  are needed for minutes; the owner rejected that explicitly.
- An S3 bucket would add infrastructure and a dependency for data that is deleted
  within the TTL, while a later S3 *source* stays possible because the outbox
  stores a reference, not a location.
- A local directory with `0o700`/`0o600` perms, path components the caller cannot
  choose, atomic writes and TTL sweep costs nothing to run and is scoped exactly
  to `(tenant, outbox item, position)`.

Restart safety is preserved: the file and its digest survive a crash, so the next
dispatch attempt reads the file, rechecks `attachment_bytes_hash` and only then
sends. A partial set is impossible because the whole set is spooled before the
item is declared dispatchable.
"""

from pathlib import Path

from app.core.config import Settings
from app.services.attachments.spool import AttachmentSpool

SPOOL_FIELDS = (
    "ATTACHMENT_STORAGE_DIR",
    "ATTACHMENT_MAX_FILE_BYTES",
    "ATTACHMENT_MAX_TOTAL_BYTES",
    "ATTACHMENT_SPOOL_TTL_SECONDS",
)


def resolved_spool(settings: Settings) -> AttachmentSpool:
    """One spool per deployment, built from settings, never from caller input."""
    return AttachmentSpool(
        settings.ATTACHMENT_STORAGE_DIR,
        max_file_bytes=settings.ATTACHMENT_MAX_FILE_BYTES,
        max_total_bytes=settings.ATTACHMENT_MAX_TOTAL_BYTES,
        ttl_seconds=settings.ATTACHMENT_SPOOL_TTL_SECONDS,
    )


def configured_root(settings: Settings) -> Path:
    """Path the spool will use; exposed for ops checks, never for request handling."""
    return resolved_spool(settings).root_dir
