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

CANONICAL_SNIPPETS: list[dict[str, Any]] = [
    {
        "id": "kn-1",
        "title": "Рамочный договор поставки полимерного сырья № ДП-2026/114",
        "source_name": "Парный локальный шлюз · /srv/contracts/2026/dp_114_rev3.pdf",
        "source": "Парный локальный шлюз · /srv/contracts/2026/dp_114_rev3.pdf",
        "acl_scope": "contracts:read",
        "data_boundary": "local_only",
        "boundary": "local_only",
        "citation_anchor": "Стр. 7 · п. 4.2 «Условия пересмотра валютной оговорки»",
        "anchor": "Стр. 7 · п. 4.2 «Условия пересмотра валютной оговорки»",
        "revision_hash": "sha256:9c4a18f2",
        "hash": "sha256:9c4a18f2",
        "synced_at": "2026-11-01T10:15:00Z",
        "excerpt": "При отклонении курса ЦБ РФ более чем на 5% от базового значения (92.50 RUB/USD) стороны обязаны согласовать обновлённую спецификацию в письменной форме до отгрузки партии.",
        "extraction_limit_note": "PDF-парсер + OCR визы на стр. 12 (достоверность 0.91)",
        "limit": "PDF-парсер + OCR визы на стр. 12 (достоверность 0.91)",
    },
    {
        "id": "kn-2",
        "title": "КП ООО «ТехПолимер-Восток» (вложение из корпоративной почты)",
        "source_name": "Почта закупок · INBOX/Поставщики · UID 9817",
        "source": "Почта закупок · INBOX/Поставщики · UID 9817",
        "acl_scope": "procurement:read",
        "data_boundary": "company",
        "boundary": "company",
        "citation_anchor": "Файл: KP_TechPolymer_Nov2026.xlsx · Лист «Цены» · Ячейки C14:G18",
        "anchor": "Файл: KP_TechPolymer_Nov2026.xlsx · Лист «Цены» · Ячейки C14:G18",
        "revision_hash": "sha256:41e8b30a",
        "hash": "sha256:41e8b30a",
        "synced_at": "2026-11-01T11:42:00Z",
        "excerpt": "Гранула ПНД ПЭ2НТ11-9: 148 500,00 руб./т без НДС. По позиции «Катализатор КС-4» ячейка E17 не заполнена (MISSING_PRICE).",
        "extraction_limit_note": "Детерминированное чтение XLSX без округления",
        "limit": "Детерминированное чтение XLSX без округления",
    },
    {
        "id": "kn-3",
        "title": "Регламент согласования договоров холдинга (ред. 2026)",
        "source_name": "Яндекс.Диск · /Общие документы/Регламенты/Reg_2026.docx",
        "source": "Яндекс.Диск · /Общие документы/Регламенты/Reg_2026.docx",
        "acl_scope": "public",
        "data_boundary": "cloud",
        "boundary": "cloud",
        "citation_anchor": "Разд. 3 · п. 3.4 «Сроки визирования»",
        "anchor": "Разд. 3 · п. 3.4 «Сроки визирования»",
        "revision_hash": "sha256:7e12ad91",
        "hash": "sha256:7e12ad91",
        "synced_at": "2026-11-01T12:00:00Z",
        "excerpt": "Срок первичного рассмотрения спецификаций юристом составляет не более 2 рабочих дней с момента загрузки в систему.",
        "extraction_limit_note": "Прямой парсер DOCX без ограничений",
        "limit": "Прямой парсер DOCX без ограничений",
    },
]


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
        boundary = "cloud"

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
    - Superusers or wildcard '*' scopes access all ACL scopes.
    - Snippets exceeding allowed_boundary are strictly excluded (e.g. 'local_only' cannot leak
      to 'cloud' queries).
    """
    max_level = BOUNDARY_LEVELS.get(str(allowed_boundary).lower(), BOUNDARY_LEVELS["cloud"])
    scope_set = set(user_scopes or [])
    has_wildcard = "*" in scope_set or "admin" in scope_set

    result: list[dict[str, Any]] = []
    for s in snippets:
        snippet_boundary = str(s.get("data_boundary") or s.get("boundary") or "cloud").lower()
        snippet_level = BOUNDARY_LEVELS.get(snippet_boundary, BOUNDARY_LEVELS["cloud"])
        if snippet_level > max_level:
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
    base_pool = list(snippets if snippets is not None else CANONICAL_SNIPPETS)
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
    """Assemble verified citation blocks for LLM prompt context."""
    blocks: list[str] = []
    for idx, s in enumerate(snippets, start=1):
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
