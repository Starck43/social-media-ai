"""P18-T04: bounded XLSX extraction with sheet anchors and guards."""

import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers import Harness, make_xlsx, make_zip_bomb  # noqa: E402

from local_connector.extract.limits import DocumentLimits  # noqa: E402
from local_connector.extract.xlsx import extract_xlsx  # noqa: E402


def test_rows_and_sheet_anchors():
    data = make_xlsx({"Sheet1": (("col a", "col b"), (1, "value one")), "Second": (("only",),)})
    result = extract_xlsx(data, DocumentLimits())
    by_sheet = {}
    for item in result.chunks:
        by_sheet.setdefault(item.sheet_anchor, []).append(item.text)
    assert by_sheet["Sheet1"][0].split() == ["col", "a", "col", "b"]
    assert any(item == "1 value one" for item in by_sheet["Sheet1"])
    assert by_sheet["Second"] == ["only"]
    assert result.notes == ()
    assert result.author == "p18-t04-test"


def test_cell_value_types():
    data = make_xlsx({"S": ((True, 2.0, 1.5, date(2026, 1, 2), datetime(2026, 1, 2, 3, 4, 5), None, "x"),)})
    result = extract_xlsx(data, DocumentLimits())
    assert result.chunks[0].text == "TRUE 2 1.5 2026-01-02T00:00:00 2026-01-02T03:04:05 x"


def test_xlsx_backend_absent_is_note(tmp_path, monkeypatch):
    import io
    import zipfile as zf

    buffer = io.BytesIO()
    with zf.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
    monkeypatch.setitem(sys.modules, "openpyxl", None)
    result = extract_xlsx(buffer.getvalue(), DocumentLimits())
    assert result.chunks == ()
    assert result.notes == ("xlsx.unavailable",)


def test_bad_xlsx_becomes_note():
    result = extract_xlsx(b"no workbook here", DocumentLimits())
    assert result.chunks == ()
    assert result.notes[0].startswith("xlsx.unsupported_format")


def test_zip_bomb_rejected_before_parse():
    limits = DocumentLimits(max_uncompressed_bytes=1 << 20)
    data = make_zip_bomb("xl/worksheets/sheet1.xml", inflated=1 << 22)
    result = extract_xlsx(data, limits)
    assert result.chunks == ()
    assert result.notes == ("xlsx.uncompressed_limit",)
    assert len(data) < (1 << 20)


def test_sheet_budget_truncation():
    data = make_xlsx({"S%d" % n: (("row",),) for n in range(6)})
    limits = DocumentLimits(max_xlsx_sheets=3)
    result = extract_xlsx(data, limits)
    assert {item.sheet_anchor for item in result.chunks} == {"S0", "S1", "S2"}
    assert "xlsx.sheets_truncated" in result.notes


def test_row_budget_truncation():
    data = make_xlsx({"S": tuple((f"r{i}",) for i in range(20))})
    limits = DocumentLimits(max_xlsx_rows=5)
    result = extract_xlsx(data, limits)
    assert len(result.chunks) == 5
    assert "xlsx.rows_truncated" in result.notes


def test_empty_rows_skipped():
    data = make_xlsx(
        {
            "S": (
                (None, None),
                ("kept",),
                (),
                (0,),
            )
        }
    )
    result = extract_xlsx(data, DocumentLimits())
    assert [item.text for item in result.chunks] == ["kept", "0"]
    assert result.notes == ()


def test_engine_documents_xlsx(tmp_path):
    harness = Harness(tmp_path)
    harness.write("data.xlsx", make_xlsx({"S": (("unique xlsx token",),)}))
    assert harness.index("data.xlsx") is True
    record = harness.store.get_file("data.xlsx")
    assert record is not None
    assert record.notes == ()
    assert harness.store.search("unique xlsx token")[0]["sheet_anchor"] == "S"
    harness.close()
