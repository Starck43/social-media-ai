"""Versioned Document Service with content hash integrity and approval invalidation (P12-T03).

Core rules:
- Document revisions are uniquely tracked by SHA256 content_hash.
- Any edit to body_text creates a new revision and resets status = 'draft_invalidated',
  approved_by = None ('edits invalidate approval').
- Product approval click is recorded with approver name/timestamp and does NOT claim qualified EDS.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

STATUS_DRAFT = "draft"
STATUS_APPROVED = "approved"
STATUS_DRAFT_INVALIDATED = "draft_invalidated"
STATUS_REJECTED = "rejected"
STATUS_IN_REVIEW = "in_review"
STATUS_REVISION_REQUESTED = "revision_requested"

STATUS_LABELS: dict[str, str] = {
    STATUS_DRAFT: "Черновик",
    STATUS_APPROVED: "Согласовано",
    STATUS_DRAFT_INVALIDATED: "Требуется повторное согласование после правки текста",
    STATUS_REJECTED: "Отклонено",
    STATUS_IN_REVIEW: "На согласовании",
    STATUS_REVISION_REQUESTED: "Требуется доработка",
}


# This is a pure state-policy helper, not an approval or persistence operation.
# A caller must enforce tenant/actor access and atomically compare the stored
# state and revision; the hash-bound approval workflow belongs to P19-T04.
_REVIEW_TRANSITIONS = {
    STATUS_DRAFT: frozenset({STATUS_IN_REVIEW}),
    STATUS_IN_REVIEW: frozenset({STATUS_APPROVED, STATUS_REJECTED, STATUS_REVISION_REQUESTED}),
    STATUS_APPROVED: frozenset(),
    STATUS_REJECTED: frozenset(),
    STATUS_REVISION_REQUESTED: frozenset(),
}


def canonical_revision_status(status: str) -> tuple[str, bool]:
    """Map legacy invalidation without losing its meaning; reject unknown values."""
    if status == STATUS_DRAFT_INVALIDATED:
        return STATUS_DRAFT, True
    if status not in _REVIEW_TRANSITIONS:
        raise ValueError("Unknown document revision status")
    return status, False


def validate_review_transition(current: str, target: str) -> None:
    """Validate only the review graph; this does not authorize or persist a change."""
    if current not in _REVIEW_TRANSITIONS or target not in _REVIEW_TRANSITIONS[current]:
        raise ValueError("Invalid document review transition")


def compute_content_hash(body_text: str) -> str:
    """Compute the full deterministic SHA-256 digest for a revision snapshot."""
    normalized = (body_text or "").encode("utf-8")
    digest = hashlib.sha256(normalized).hexdigest()
    return f"sha256:{digest}"


def create_versioned_document(
    title: str,
    counterparty: str,
    body_text: str,
    doc_id: str | None = None,
    unsupported_terms_note: str = "",
    author: str = "",
) -> dict[str, Any]:
    """Create a new versioned document in initial draft state."""
    clean_text = str(body_text or "").strip()
    c_hash = compute_content_hash(clean_text)
    now_iso = datetime.now(timezone.utc).isoformat()
    doc_id = doc_id or f"doc-{abs(hash(title + now_iso)) % 10000}"

    return {
        "id": doc_id,
        "title": str(title),
        "counterparty": str(counterparty),
        "version": "v1.0",
        "content_hash": c_hash,
        "hash": c_hash,
        "updated_at": now_iso,
        "status": STATUS_LABELS[STATUS_DRAFT],
        "status_code": STATUS_DRAFT,
        "approved_by": None,
        "unsupported_terms_note": str(unsupported_terms_note),
        "warning": str(unsupported_terms_note),
        "body_text": clean_text,
        "revisions": [
            {
                "version": "v1.0",
                "content_hash": c_hash,
                "author": author,
                "created_at": now_iso,
            }
        ],
    }


def update_document_body(
    doc: dict[str, Any],
    new_body_text: str,
    author: str = "",
) -> dict[str, Any]:
    """Update document body_text, creating a new revision.

    CRITICAL RULE (P12-T03):
    Any edit to body_text creates a new revision and resets
    status = 'draft_invalidated', approved_by = None (edits invalidate approval).
    """
    clean_new_text = str(new_body_text or "").strip()
    new_hash = compute_content_hash(clean_new_text)

    # Compute next minor version label
    current_version = str(doc.get("version", "v1.0")).lstrip("v")
    base_ver = current_version.split("-")[0]
    parts = base_ver.split(".")
    try:
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
        new_version = f"v{major}.{minor + 1}-edit"
    except (ValueError, IndexError):
        new_version = f"{doc.get('version', 'v1.0')}-edit"

    now_iso = datetime.now(timezone.utc).isoformat()

    updated = dict(doc)
    updated["version"] = new_version
    updated["body_text"] = clean_new_text
    updated["content_hash"] = new_hash
    updated["hash"] = new_hash
    updated["updated_at"] = now_iso

    # Invalidate approval immediately
    updated["status"] = STATUS_LABELS[STATUS_DRAFT_INVALIDATED]
    updated["status_code"] = STATUS_DRAFT_INVALIDATED
    updated["approved_by"] = None

    revisions = list(doc.get("revisions", []))
    revisions.append(
        {
            "version": new_version,
            "content_hash": new_hash,
            "author": author,
            "created_at": now_iso,
        }
    )
    updated["revisions"] = revisions

    return updated


def approve_document(
    doc: dict[str, Any],
    approver: str,
) -> dict[str, Any]:
    """Approve a document revision."""
    approver_name = str(approver or "").strip() or "operator"
    now_iso = datetime.now(timezone.utc).isoformat()

    updated = dict(doc)
    updated["status"] = f"Согласовано ({approver_name})"
    updated["status_code"] = STATUS_APPROVED
    updated["approved_by"] = approver_name
    updated["updated_at"] = now_iso
    return updated


def reject_document(
    doc: dict[str, Any],
    reason: str = "",
) -> dict[str, Any]:
    """Reject a document revision."""
    reason_clean = str(reason or "").strip()
    status_label = f"Отклонено: {reason_clean}" if reason_clean else STATUS_LABELS[STATUS_REJECTED]

    updated = dict(doc)
    updated["status"] = status_label
    updated["status_code"] = STATUS_REJECTED
    updated["approved_by"] = None
    updated["updated_at"] = datetime.now(timezone.utc).isoformat()
    return updated
