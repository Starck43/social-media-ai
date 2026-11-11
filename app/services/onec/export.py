"""Table formatting plus bounded XLSX/PDF export for authorized 1C query results (P17-T03).

Formatting and rendering use only the rows the read service already returned: no
network access, no templates fetched at render time, and every rendered value is
escaped for its target format.
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from decimal import Decimal
from io import BytesIO
from typing import Any, Literal, Mapping

from app.services.onec.query import MonetaryValue, OneCQueryResult

MAX_EXPORT_ROWS = 1000
MAX_CELL_CHARS = 300
MAX_TITLE_CHARS = 120
_ILLEGAL_SHEET_CHARS = re.compile(r"[\\/*?:\[\]]")
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


class OneCExportError(ValueError):
    """Fixed error code; never echoes rejected values."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class OneCResultTable:
    """Deterministic table view of a query result plus a summary and optional chart data."""

    title: str
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    summary: str = field(repr=False)
    chart: tuple[tuple[str, str], ...] = ()
    truncated: bool = False


@dataclass(frozen=True)
class ExportedOneCFile:
    """Rendered export bytes with their media type, size and checksum."""

    content: bytes = field(repr=False)
    media_type: str
    extension: str
    filename: str
    row_count: int
    sha256: str


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, MonetaryValue):
        return value.formatted
    if isinstance(value, Decimal):
        return f"{value}"
    if isinstance(value, bool):
        return "да" if value else "нет"
    text = str(value)
    return text if len(text) <= MAX_CELL_CHARS else text[:MAX_CELL_CHARS] + "…"


def _title_for(result: OneCQueryResult) -> str:
    return f"1С: {result.entity_set}"[:MAX_TITLE_CHARS]


def _summarize(result: OneCQueryResult) -> str:
    parts = [f"строк: {result.row_count}"]
    if result.next_page_token:
        parts.append(f"следующая страница с {result.skip + result.row_count}")
    totals: list[str] = []
    for name in result.selected_fields:
        values = [row.get(name) for row in result.rows]
        if values and all(isinstance(value, MonetaryValue) for value in values):
            currency = values[0].currency  # type: ignore[union-attr]
            if all(value.currency == currency for value in values):  # type: ignore[union-attr]
                amount = sum((value.amount for value in values), Decimal("0"))  # type: ignore[union-attr]
                totals.append(f"{name}: {MonetaryValue(amount=amount, currency=currency).formatted}")
    if totals:
        parts.append("итого " + "; ".join(totals[:3]))
    return "; ".join(parts)


def _chart_series(result: OneCQueryResult) -> tuple[tuple[str, str], ...]:
    numeric_field = None
    for name in result.selected_fields:
        values = [row.get(name) for row in result.rows]
        if values and all(isinstance(value, (MonetaryValue, Decimal)) for value in values if value is not None):
            numeric_field = name
            break
    if numeric_field is None:
        return ()
    label_field = next((name for name in result.selected_fields if name != numeric_field), None)
    series: list[tuple[str, str]] = []
    for index, row in enumerate(result.rows):
        value = row.get(numeric_field)
        if isinstance(value, MonetaryValue):
            amount = f"{value.amount}"
        elif isinstance(value, Decimal):
            amount = f"{value}"
        else:
            continue
        label = _cell_text(row.get(label_field)) if label_field else str(index + 1)
        series.append((label[:60] or str(index + 1), amount))
    return tuple(series[:50])


def format_result(result: OneCQueryResult, *, title: str | None = None) -> OneCResultTable:
    """Build the table shown in chat/API and used for export."""
    if not isinstance(result, OneCQueryResult):
        raise OneCExportError("invalid_result")
    columns = tuple(result.selected_fields)
    if not columns:
        raise OneCExportError("invalid_result")
    rows = tuple(tuple(_cell_text(row.get(name)) for name in columns) for row in result.rows[:MAX_EXPORT_ROWS])
    resolved_title = (title or _title_for(result)).strip()[:MAX_TITLE_CHARS]
    if not resolved_title:
        raise OneCExportError("invalid_title")
    return OneCResultTable(
        title=resolved_title,
        columns=columns,
        rows=rows,
        summary=_summarize(result),
        chart=_chart_series(result),
        truncated=len(result.rows) > MAX_EXPORT_ROWS,
    )


def _safe_cell(value: str) -> str:
    """Neutralize spreadsheet formula prefixes without changing the visible value."""
    if value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def _sheet_name(title: str) -> str:
    cleaned = _ILLEGAL_SHEET_CHARS.sub(" ", title).strip() or "1C"
    return cleaned[:31]


def render_xlsx(table: OneCResultTable) -> bytes:
    """Render a bounded table as XLSX; every cell is plain text, never a formula."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    if not isinstance(table, OneCResultTable):
        raise OneCExportError("invalid_table")
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = _sheet_name(table.title)
    sheet.append([_safe_cell(column) for column in table.columns])
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in table.rows:
        sheet.append([_safe_cell(value) for value in row])
    sheet.freeze_panes = "A2"
    for index, column in enumerate(table.columns, start=1):
        width = max([len(column), *(len(row[index - 1]) for row in table.rows)] or [len(column)])
        sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = min(max(10, width + 2), 60)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _no_fetch(url, *args, **kwargs):
    raise OneCExportError("external_resource_forbidden")


def render_pdf(table: OneCResultTable) -> bytes:
    """Render a bounded table as PDF from escaped HTML; no external resources are fetched.

    A host without the native pango stack fails closed with `pdf_unavailable`
    instead of surfacing an unhandled renderer error.
    """
    try:
        from weasyprint import HTML
    except (ImportError, OSError):
        raise OneCExportError("pdf_unavailable") from None

    if not isinstance(table, OneCResultTable):
        raise OneCExportError("invalid_table")
    header = "".join(f"<th>{html.escape(column)}</th>" for column in table.columns)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(value)}</td>" for value in row) + "</tr>" for row in table.rows)
    source = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(table.title)}</title>"
        "<style>@page{size:A4 landscape;margin:12mm}"
        "body{font-family:'Noto Sans',sans-serif;font-size:9pt}"
        "h1{font-size:14pt;overflow-wrap:anywhere}"
        "p.summary{color:#444}"
        "table{border-collapse:collapse;width:100%}"
        "th,td{border:1px solid #999;padding:3px 5px;text-align:left;overflow-wrap:anywhere}"
        "th{background:#eee}</style></head><body>"
        f"<h1>{html.escape(table.title)}</h1>"
        f"<p class='summary'>{html.escape(table.summary)}</p>"
        f"<table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>"
        "</body></html>"
    )
    try:
        return HTML(string=source, url_fetcher=_no_fetch).write_pdf()
    except (ImportError, OSError):
        raise OneCExportError("pdf_unavailable") from None


EXPORT_MEDIA_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}


def export_result(
    result: OneCQueryResult,
    format: Literal["xlsx", "pdf"],
    *,
    title: str | None = None,
) -> ExportedOneCFile:
    """Render one export of already-authorized rows; callers record the audit entry."""
    if format not in EXPORT_MEDIA_TYPES:
        raise OneCExportError("invalid_format")
    table = format_result(result, title=title)
    content = render_xlsx(table) if format == "xlsx" else render_pdf(table)
    if not content:
        raise OneCExportError("empty_export")
    filename = re.sub(r"[^A-Za-zА-Яа-яЁё0-9_.-]+", "_", table.title)[:60] or "onec"
    return ExportedOneCFile(
        content=content,
        media_type=EXPORT_MEDIA_TYPES[format],
        extension=format,
        filename=f"{filename}.{format}",
        row_count=len(table.rows),
        sha256=hashlib.sha256(content).hexdigest(),
    )


def rows_as_mappings(table: OneCResultTable) -> tuple[Mapping[str, str], ...]:
    """Project a formatted table into row dicts for JSON responses."""
    return tuple(dict(zip(table.columns, row)) for row in table.rows)


__all__ = [
    "EXPORT_MEDIA_TYPES",
    "ExportedOneCFile",
    "MAX_EXPORT_ROWS",
    "OneCExportError",
    "OneCResultTable",
    "export_result",
    "format_result",
    "render_pdf",
    "render_xlsx",
    "rows_as_mappings",
]
