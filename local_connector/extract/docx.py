"""Bounded DOCX extraction with paragraph and table-row anchors (python-docx).

Core properties provide author/created/modified. The ZIP container is guarded
before the parser runs so a compressed bomb cannot force unbounded memory.
Extraction never raises: every failure is a note code.
"""

from __future__ import annotations

import io

from .containers import inspect_zip
from .limits import DocumentLimits
from .models import ExtractionChunk, ExtractionResult, bounded_name


def _epoch(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if not hasattr(value, "timestamp"):
        return None
    try:
        return int(value.timestamp())
    except (OverflowError, OSError, ValueError):
        return None


def _cell_text(cell: object) -> str:
    text = getattr(cell, "text", "")
    return text if isinstance(text, str) else ""


def extract_docx(data: bytes, limits: DocumentLimits) -> ExtractionResult:
    """Extract bounded DOCX text/metadata; every failure is a note code."""

    status, _ = inspect_zip(data, limits.max_uncompressed_bytes)
    if status == "invalid":
        return ExtractionResult(chunks=(), notes=("docx.unsupported_format",))
    if status == "too_large":
        return ExtractionResult(chunks=(), notes=("docx.uncompressed_limit",))

    try:
        import docx
    except ImportError:
        return ExtractionResult(chunks=(), notes=("docx.unavailable",))
    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:
        return ExtractionResult(chunks=(), notes=("docx.unsupported_format", type(exc).__name__.lower()))

    author: str | None
    created: int | None
    modified: int | None
    try:
        properties = document.core_properties
        author = bounded_name(properties.author)
        created = _epoch(properties.created)
        modified = _epoch(properties.modified)
    except Exception:
        author, created, modified = None, None, None

    notes: list[str] = []
    chunks: list[ExtractionChunk] = []
    characters = 0

    def add(text: str, anchor: str) -> bool:
        nonlocal characters
        text = text.strip()
        if not text:
            return True
        if characters + len(text) > limits.max_document_characters:
            notes.append("limit.characters")
            return False
        characters += len(text)
        chunks.append(ExtractionChunk(text=text, page_anchor=anchor))
        return True

    for index, paragraph in enumerate(document.paragraphs, start=1):
        if index > limits.max_docx_paragraphs:
            notes.append("docx.paragraphs_truncated")
            break
        text = paragraph.text if isinstance(paragraph.text, str) else ""
        if not add(text, f"p:{index}"):
            break

    row_budget = limits.max_docx_table_rows
    for table_index, table in enumerate(document.tables, start=1):
        if characters > limits.max_document_characters:
            break
        for row_index, row in enumerate(table.rows, start=1):
            if row_budget <= 0:
                notes.append("docx.table_truncated")
                break
            row_budget -= 1
            cells = [text.strip() for text in (_cell_text(cell) for cell in row.cells)]
            row_text = " ".join(text for text in cells if text)
            if not add(row_text, f"t:{table_index}:r:{row_index}"):
                break
        if characters > limits.max_document_characters:
            break

    return ExtractionResult(
        chunks=tuple(chunks),
        author=author,
        created=created,
        modified=modified,
        notes=tuple(dict.fromkeys(notes))[:16],
    )
