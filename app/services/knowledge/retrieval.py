"""Permission- and ACL-aware retrieval service for knowledge snippets (P12-T03).

Filters indexed snippets from mounted cloud drive folders (SourceType.DRIVE_FOLDER),
email attachments, and local gateways by acl_scope and data_boundary (local_only | company | cloud)
before returning results or assembling LLM prompt context.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

BOUNDARY_LEVELS: dict[str, int] = {
    "local_only": 1,
    "company": 2,
    "cloud": 3,
}


def build_citation_anchor(
    source_type: str,
    path_or_id: str,
    location_subpart: str | None = None,
) -> str:
    """Build a deterministic citation anchor string."""
    clean_path = str(path_or_id or "").strip()
    if not location_subpart:
        return clean_path
    clean_subpart = str(location_subpart).strip()
    return f"{clean_path} · {clean_subpart}"


def build_knowledge_snippet(
    snippet_id: str,
    title: str,
    source_name: str,
    acl_scope: str,
    data_boundary: str,
    citation_anchor: str,
    revision_hash: str,
    excerpt: str,
    extraction_limit_note: str,
    synced_at: str | None = None,
) -> dict[str, Any]:
    """Construct a knowledge snippet conforming to P12-T03 template and API contracts."""
    boundary = str(data_boundary).lower().strip()
    if boundary not in BOUNDARY_LEVELS:
        raise ValueError("Unknown evidence boundary")

    iso_synced = synced_at or datetime.now(timezone.utc).isoformat()
    return {
        "id": str(snippet_id),
        "title": str(title),
        "source_name": str(source_name),
        "source": str(source_name),
        "acl_scope": str(acl_scope),
        "data_boundary": boundary,
        "boundary": boundary,
        "citation_anchor": str(citation_anchor),
        "anchor": str(citation_anchor),
        "revision_hash": str(revision_hash),
        "hash": str(revision_hash),
        "synced_at": iso_synced,
        "excerpt": str(excerpt),
        "extraction_limit_note": str(extraction_limit_note),
        "limit": str(extraction_limit_note),
    }


def filter_snippets_by_acl(
    snippets: Iterable[dict[str, Any]],
    user_scopes: Iterable[str] | None,
    allowed_boundary: str = "cloud",
) -> list[dict[str, Any]]:
    """Filter snippets by ACL permission scopes and data boundary before context assembly.

    Rules:
    - If user_scopes is None, only 'public' snippets are returned.
    - If user has specific scopes, snippets with matching scope or 'public' are allowed.
    - Trusted callers may explicitly supply wildcard '*' scopes access all ACL scopes.
    - Snippets exceeding allowed_boundary are strictly excluded (inclusive ceiling).
    - This ceiling does not authorize sending local-only content to a cloud model;
      callers must enforce the separate execution/transport boundary.
    """
    max_level = BOUNDARY_LEVELS.get(str(allowed_boundary).lower())
    if max_level is None:
        return []
    scope_set = set(user_scopes or [])
    has_wildcard = "*" in scope_set or "admin" in scope_set

    result: list[dict[str, Any]] = []
    for s in snippets:
        snippet_boundary = str(s.get("data_boundary") or s.get("boundary") or "").lower()
        snippet_level = BOUNDARY_LEVELS.get(snippet_boundary)
        if snippet_level is None or snippet_level > max_level:
            continue

        snippet_acl = str(s.get("acl_scope") or "").strip()
        if snippet_acl != "public" and not has_wildcard and snippet_acl not in scope_set:
            continue

        result.append(s)

    return result


def search_knowledge_snippets(
    query: str,
    snippets: Iterable[dict[str, Any]] | None = None,
    user_scopes: Iterable[str] | None = None,
    allowed_boundary: str = "cloud",
) -> list[dict[str, Any]]:
    """Search snippets by term with mandatory ACL and boundary gate applied first."""
    base_pool = list(snippets or ())
    permitted = filter_snippets_by_acl(base_pool, user_scopes, allowed_boundary=allowed_boundary)

    q = (query or "").strip().lower()
    if not q:
        return permitted

    matched: list[dict[str, Any]] = []
    for item in permitted:
        searchable_text = " ".join(
            [
                item.get("title", ""),
                item.get("source_name", item.get("source", "")),
                item.get("citation_anchor", item.get("anchor", "")),
                item.get("excerpt", ""),
                item.get("revision_hash", item.get("hash", "")),
            ]
        ).lower()
        if q in searchable_text:
            matched.append(item)

    return matched


def assemble_llm_citations_context(
    snippets: Iterable[dict[str, Any]],
    allowed_boundary: str = "cloud",
) -> str:
    """Assemble citation blocks under the ceiling, not a cloud-transport authorization.

    Callers must separately enforce ACLs and the model execution/transport boundary.
    """
    blocks: list[str] = []
    max_level = BOUNDARY_LEVELS.get(str(allowed_boundary).lower())
    if max_level is None:
        return ""
    safe = []
    for s in snippets:
        boundary = str(s.get("data_boundary") or s.get("boundary") or "").lower()
        level = BOUNDARY_LEVELS.get(boundary)
        if level is not None and level <= max_level:
            safe.append(s)
    for idx, s in enumerate(safe, start=1):
        title = s.get("title", "Документ")
        source = s.get("source_name", s.get("source", "Unknown"))
        anchor = s.get("citation_anchor", s.get("anchor", ""))
        rev_hash = s.get("revision_hash", s.get("hash", ""))
        boundary = s.get("data_boundary", s.get("boundary", "cloud"))
        excerpt = s.get("excerpt", "").strip()
        note = s.get("extraction_limit_note", s.get("limit", ""))

        block = f"[{idx}] {title} — {source} ({anchor} · {rev_hash} · boundary:{boundary})\n«{excerpt}»"
        if note:
            block += f"\n[OCR/Extraction note: {note}]"
        blocks.append(block)

    return "\n\n".join(blocks)
