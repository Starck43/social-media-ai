"""Bounded XLSX extraction with sheet anchors (openpyxl, read-only mode).

``read_only`` streams rows and ``data_only`` reads cached values, so formulas
are never evaluated by the extractor. The ZIP container is guarded before the
parser runs, and sheet/row/cell counts stay bounded.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from datetime import date, datetime

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


def _cell_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    if isinstance(value, (int, str)):
        return str(value)
    if isinstance(value, (datetime, date)):
        try:
            return value.isoformat()
        except (ValueError, OverflowError, OSError):
            return ""
    return ""


def _sheet_rows(values: Iterator[tuple[object, ...]], max_cells: int) -> Iterator[str]:
    for row in values:
        parts = [_cell_value(item) for item in list(row)[:max_cells]]
        text = " ".join(part.strip() for part in parts if part.strip())
        if text:
            yield text


def extract_xlsx(data: bytes, limits: DocumentLimits) -> ExtractionResult:
    """Extract bounded XLSX text/metadata; every failure is a note code."""

    status, _ = inspect_zip(data, limits.max_uncompressed_bytes)
    if status == "invalid":
        return ExtractionResult(chunks=(), notes=("xlsx.unsupported_format",))
    if status == "too_large":
        return ExtractionResult(chunks=(), notes=("xlsx.uncompressed_limit",))

    import openpyxl

    try:
        workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        return ExtractionResult(chunks=(), notes=("xlsx.unsupported_format", type(exc).__name__.lower()))

    author: str | None
    created: int | None
    modified: int | None
    notes: list[str] = []
    chunks: list[ExtractionChunk] = []
    characters = 0
    try:
        try:
            properties = workbook.properties
            author = bounded_name(properties.creator)
            created = _epoch(properties.created)
            modified = _epoch(properties.modified)
        except Exception:
            author, created, modified = None, None, None

        def add(text: str, anchor: str) -> bool:
            nonlocal characters
            if characters + len(text) > limits.max_document_characters:
                notes.append("limit.characters")
                return False
            characters += len(text)
            chunks.append(ExtractionChunk(text=text, sheet_anchor=anchor))
            return True

        worksheets = list(workbook.worksheets)
        if len(worksheets) > limits.max_xlsx_sheets:
            notes.append("xlsx.sheets_truncated")
        for ordinal, sheet in enumerate(worksheets[: limits.max_xlsx_sheets], start=1):
            if characters > limits.max_document_characters:
                break
            anchor = (bounded_name(sheet.title) or f"sheet_{ordinal}")[:100]
            rows = 0
            for row in _sheet_rows(sheet.iter_rows(values_only=True), limits.max_xlsx_cells_per_row):
                rows += 1
                if rows > limits.max_xlsx_rows:
                    notes.append("xlsx.rows_truncated")
                    break
                if not add(row, anchor):
                    break
    finally:
        try:
            workbook.close()
        except Exception:
            pass

    return ExtractionResult(
        chunks=tuple(chunks),
        author=author,
        created=created,
        modified=modified,
        notes=tuple(dict.fromkeys(notes))[:16],
    )
