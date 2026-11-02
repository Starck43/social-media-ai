"""Knowledge & Documents Workspace Services (P12-T03)."""

from __future__ import annotations

from .documents import (
    CANONICAL_DOCUMENTS,
    STATUS_APPROVED,
    STATUS_DRAFT,
    STATUS_DRAFT_INVALIDATED,
    STATUS_LABELS,
    STATUS_REJECTED,
    approve_document,
    compute_content_hash,
    create_versioned_document,
    reject_document,
    update_document_body,
)
from .retrieval import (
    BOUNDARY_LEVELS,
    CANONICAL_SNIPPETS,
    assemble_llm_citations_context,
    build_citation_anchor,
    build_knowledge_snippet,
    filter_snippets_by_acl,
    search_knowledge_snippets,
)

__all__ = [
    "BOUNDARY_LEVELS",
    "CANONICAL_SNIPPETS",
    "CANONICAL_DOCUMENTS",
    "STATUS_APPROVED",
    "STATUS_DRAFT",
    "STATUS_DRAFT_INVALIDATED",
    "STATUS_LABELS",
    "STATUS_REJECTED",
    "assemble_llm_citations_context",
    "build_citation_anchor",
    "build_knowledge_snippet",
    "filter_snippets_by_acl",
    "search_knowledge_snippets",
    "compute_content_hash",
    "create_versioned_document",
    "update_document_body",
    "approve_document",
    "reject_document",
]
