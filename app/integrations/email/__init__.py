"""Email IMAP read-only incremental synchronization package (`P15-T01`)."""

from .imap_sync import (
    DEFAULT_IMAP_FOLDERS,
    ImapEnvelope,
    ImapSyncError,
    ImapSyncService,
    configure_imap_folders,
    get_imap_folders,
    imap_sync_service,
    parse_raw_header_block,
)

__all__ = [
    "DEFAULT_IMAP_FOLDERS",
    "ImapEnvelope",
    "ImapSyncError",
    "ImapSyncService",
    "configure_imap_folders",
    "get_imap_folders",
    "imap_sync_service",
    "parse_raw_header_block",
]
