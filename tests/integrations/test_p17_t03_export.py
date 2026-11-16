"""Bounded XLSX/PDF export and table formatting for 1C results; no network."""

from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO

import pytest

from app.services.onec.export import (
    MAX_EXPORT_ROWS,
    OneCExportError,
    export_result,
    format_result,
    render_pdf,
    render_xlsx,
    rows_as_mappings,
)
from app.services.onec.query import MonetaryValue, OneCQueryHistoryMetadata, OneCQueryResult
from tests.shared.pdf_rendering import require_pdf_renderer

FIELDS = ("Description", "Цена")


def make_result(rows, *, fields=FIELDS, next_token=None, entity_set="Catalog_Товары"):
    history = OneCQueryHistoryMetadata(
        query_id="q" * 32,
        tenant_id=1,
        actor_id=1,
        connection_id=1,
        entity_set=entity_set,
        entity_kind="catalog",
        register_function=None,
        plan_fingerprint="a" * 64,
        row_count=len(rows),
        top=100,
        skip=0,
        duration_ms=5,
        from_cache=False,
        executed_at=datetime.now(timezone.utc),
    )
    return OneCQueryResult(
        entity_set=entity_set,
        entity_kind="catalog",
        register_function=None,
        selected_fields=fields,
        rows=tuple(rows),
        row_count=len(rows),
        top=100,
        skip=0,
        next_page_token=next_token,
        plan_fingerprint="a" * 64,
        history_metadata=history,
    )


def goods_rows(count=3):
    return [
        {"Description": f"Товар {index}", "Цена": MonetaryValue(amount=Decimal("10.00"), currency="RUB")}
        for index in range(count)
    ]


def test_format_result_builds_columns_summary_and_totals():
    table = format_result(make_result(goods_rows(3)))
    assert table.columns == FIELDS
    assert table.rows[0][0] == "Товар 0"
    assert table.rows[0][1] == "10,00 ₽"
    assert table.summary.startswith("строк: 3")
    assert "итого Цена" in table.summary
    assert table.chart[0][1] == "10.00"
    assert table.truncated is False


def test_format_result_reports_next_page_and_handles_nulls():
    rows = [{"Description": None, "Цена": None}]
    table = format_result(make_result(rows, next_token="token"))
    assert table.rows[0] == ("", "")
    assert "следующая страница с 1" in table.summary


def test_format_result_bounds_rows():
    table = format_result(make_result(goods_rows(MAX_EXPORT_ROWS + 1), fields=FIELDS))
    assert len(table.rows) == MAX_EXPORT_ROWS
    assert table.truncated is True


def test_format_result_rejects_invalid_input():
    with pytest.raises(OneCExportError):
        format_result("not-a-result")  # type: ignore[arg-type]


def test_xlsx_contains_header_values_and_neutralized_formulas():
    rows = [
        {"Description": "=1+1", "Цена": MonetaryValue(amount=Decimal("10.00"), currency="RUB")},
        {"Description": "обычный", "Цена": MonetaryValue(amount=Decimal("2.50"), currency="RUB")},
    ]
    content = render_xlsx(format_result(make_result(rows)))
    from openpyxl import load_workbook

    sheet = load_workbook(BytesIO(content)).active
    assert [cell.value for cell in sheet[1]] == ["Description", "Цена"]
    assert sheet["A2"].value == "'=1+1"
    assert sheet["A3"].value == "обычный"
    assert sheet.freeze_panes == "A2"


def test_pdf_is_rendered_without_external_resources():
    require_pdf_renderer()
    content = render_pdf(format_result(make_result(goods_rows(2))))
    assert content.startswith(b"%PDF")
    assert len(content) > 1000


def test_export_result_reports_media_type_checksum_and_rows():
    file = export_result(make_result(goods_rows(2)), "xlsx")
    assert file.media_type.startswith("application/vnd.openxmlformats")
    assert file.extension == "xlsx"
    assert file.filename.endswith(".xlsx")
    assert file.row_count == 2
    assert len(file.sha256) == 64

    require_pdf_renderer()
    pdf = export_result(make_result(goods_rows(1)), "pdf")
    assert pdf.media_type == "application/pdf"
    assert pdf.content.startswith(b"%PDF")


def test_pdf_renderer_failure_fails_closed(monkeypatch):
    import sys
    import types

    class Exploding:
        def __init__(self, *args, **kwargs):
            raise OSError("pango missing")

    monkeypatch.setitem(sys.modules, "weasyprint", types.SimpleNamespace(HTML=Exploding))
    with pytest.raises(OneCExportError) as excinfo:
        render_pdf(format_result(make_result(goods_rows(1))))
    assert excinfo.value.code == "pdf_unavailable"


def test_export_result_rejects_unknown_format():
    with pytest.raises(OneCExportError) as excinfo:
        export_result(make_result(goods_rows(1)), "docx")  # type: ignore[arg-type]
    assert excinfo.value.code == "invalid_format"


def test_pdf_export_neutralizes_escaping():
    require_pdf_renderer()
    rows = [{"Description": "<script>alert(1)</script>", "Цена": MonetaryValue(amount=Decimal("1.00"))}]
    content = render_pdf(format_result(make_result(rows)))
    assert b"<script>alert" not in content


def test_rows_as_mappings_projects_table():
    table = format_result(make_result(goods_rows(1)))
    assert rows_as_mappings(table) == ({"Description": "Товар 0", "Цена": "10,00 ₽"},)
