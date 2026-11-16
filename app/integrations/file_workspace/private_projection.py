"""Inactive same-read projection; not authorization, authentic provenance or storage."""

from __future__ import annotations

import re

from app.integrations.file_workspace.content_read import ReadPlainText
from app.integrations.file_workspace.insight_candidates import (
    MAX_CANDIDATE_REFERENCES,
    MAX_INSIGHT_CANDIDATES,
    InsightCandidate,
    InsightCandidateBatch,
)
from app.integrations.file_workspace.line_evidence import verify_line_evidence
from app.integrations.file_workspace.private_payload import (
    SCHEMA_VERSION,
    SERIALIZER_REVISION,
    PrivatePayloadUnavailable,
    serialize_private_payload,
)

_METADATA_KEYS = frozenset(
    (
        "path_key",
        "version_semantics",
        "provider_object_id",
        "owner_epoch",
        "connection_epoch",
        "object_epoch",
        "consent_revision",
        "policy_revision",
        "lifecycle",
    )
)


def _project(source, batch, metadata) -> bytes:
    if (
        type(batch) is not InsightCandidateBatch
        or type(batch.generation_id) is not str
        or len(batch.generation_id) != 32
        or re.fullmatch(r"[0-9a-f]{32}", batch.generation_id) is None
        or type(batch.review_status) is not str
        or batch.review_status != "unverified"
        or type(batch.candidates) is not tuple
        or len(batch.candidates) > MAX_INSIGHT_CANDIDATES
        or not verify_line_evidence(batch.evidence, source)
        or type(metadata) is not dict
        or len(metadata) != len(_METADATA_KEYS)
        or any(type(key) is not str for key in metadata)
        or metadata.keys() != _METADATA_KEYS
    ):
        raise ValueError
    candidates = []
    for candidate in batch.candidates:
        if (
            type(candidate) is not InsightCandidate
            or type(candidate.evidence_indices) is not tuple
            or not 1 <= len(candidate.evidence_indices) <= MAX_CANDIDATE_REFERENCES
        ):
            raise ValueError
        # The closed-schema serializer validates text and every reference before
        # encoding. Never coerce arbitrary iterables or copy an entire context.
        candidates.append({"text": candidate.text, "evidence_indices": list(candidate.evidence_indices)})
    context = source.context
    request = context.request
    extraction = source.extraction
    return serialize_private_payload(
        {
            "schema_version": SCHEMA_VERSION,
            "serializer_revision": SERIALIZER_REVISION,
            "scope": {
                "tenant_id": request.tenant_id,
                "owner_id": request.actor_id,
                "connection_id": request.connection_id,
                "object_id": request.object_id,
                "root_key": context.root_key,
                "path_key": metadata["path_key"],
            },
            "source": {
                "provider_version": source.provider_version,
                "version_semantics": metadata["version_semantics"],
                "provider_object_id": metadata["provider_object_id"],
                "raw_sha256": extraction.raw_sha256,
                "text_sha256": extraction.text_sha256,
                "parser_version": extraction.parser_version,
            },
            "binding": {
                "read_attempt_id": request.nonce,
                "generation_attempt_id": batch.generation_id,
                "owner_epoch": metadata["owner_epoch"],
                "connection_epoch": metadata["connection_epoch"],
                "object_epoch": metadata["object_epoch"],
                "consent_revision": metadata["consent_revision"],
                "policy_revision": metadata["policy_revision"],
            },
            "lifecycle": metadata["lifecycle"],
            "line_count": extraction.line_count,
            "excerpts": [{"start": item.start, "end": item.end, "text": item.text} for item in batch.evidence.excerpts],
            "candidates": candidates,
            "review_status": "unverified",
        }
    )


def serialize_same_read_payload(source: ReadPlainText, batch: InsightCandidateBatch, *, metadata: dict) -> bytes:
    """Explicit allowlist projection after same-read quote verification.

    Inputs must be exclusively owned application values, not externally supplied
    grants or concurrently mutated. Metadata carries unproved path/version labels,
    epochs, policy/consent revisions and historical times. Personal owner is the
    read actor by convention, NOT a proof of ownership. No fresh rights, clock,
    exact-byte version assertion, model provenance, semantic verification or I/O.
    Missing provider version fails closed; hashes/observations never replace it.
    """
    try:
        return _project(source, batch, metadata)
    except Exception:
        pass
    raise PrivatePayloadUnavailable()
