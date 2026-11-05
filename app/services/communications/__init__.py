"""Tenant-scoped unified communications package (`P15-T02`)."""

from .ingest import (
    CommunicationIngestError,
    IngestMessageInput,
    IngestResult,
    PersonalConnectorDisabledError,
    compute_content_hash,
    derive_thread_key,
    extract_reference_ids,
    hash_message_id,
    ingest,
    ingest_max_update,
    ingest_telegram_update,
    mark_message_unavailable,
    normalize_message_id,
    normalize_subject_for_thread,
    prune_expired_messages,
)

__all__ = [
    "CommunicationIngestError",
    "IngestMessageInput",
    "IngestResult",
    "PersonalConnectorDisabledError",
    "compute_content_hash",
    "derive_thread_key",
    "extract_reference_ids",
    "hash_message_id",
    "ingest",
    "ingest_max_update",
    "ingest_telegram_update",
    "mark_message_unavailable",
    "normalize_message_id",
    "normalize_subject_for_thread",
    "prune_expired_messages",
]
