"""P18-T04: bounded DOCX extraction with paragraph/table anchors and guards."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers import Harness, make_docx, make_zip_bomb  # noqa: E402

from local_connector.extract.docx import extract_docx  # noqa: E402
from local_connector.extract.limits import DocumentLimits  # noqa: E402


def test_paragraphs_and_table_anchors():
    data = make_docx(
        paragraphs=("alpha paragraph", "beta paragraph"),
        table=(("c1", "c2"), ("c3", "c4")),
        author="DOCX author",
    )
    result = extract_docx(data, DocumentLimits())
    anchors = {item.page_anchor: item.text for item in result.chunks}
    assert "alpha paragraph" in anchors["p:2"]
    assert "beta paragraph" in anchors["p:3"]
    assert "c1 c2" in anchors["t:1:r:1"]
    assert result.notes == ()
    assert result.author == "DOCX author"
    assert result.created is not None


def test_bad_docx_becomes_note():
    result = extract_docx(b"definitely not a zip", DocumentLimits())
    assert result.chunks == ()
    assert result.notes[0].startswith("docx.unsupported_format")


def test_zip_bomb_rejected_before_parse():
    limits = DocumentLimits(max_uncompressed_bytes=1 << 20)
    data = make_zip_bomb("word/document.xml", inflated=1 << 23)
    result = extract_docx(data, limits)
    assert result.chunks == ()
    assert result.notes == ("docx.uncompressed_limit",)
    assert len(data) < (1 << 20)


def test_paragraph_budget_truncation():
    data = make_docx(paragraphs=tuple(f"row {n}" for n in range(10)))
    limits = DocumentLimits(max_docx_paragraphs=3)
    result = extract_docx(data, limits)
    anchors = [item.page_anchor for item in result.chunks]
    assert anchors == ["p:1", "p:2", "p:3"]
    assert "docx.paragraphs_truncated" in result.notes


def test_empty_paragraphs_skipped():
    data = make_docx(paragraphs=("", "visible only", ""), heading=False)
    result = extract_docx(data, DocumentLimits())
    assert [item.text for item in result.chunks] == ["visible only"]
    assert result.notes == ()


def test_table_row_budget():
    data = make_docx(paragraphs=("lead",), table=tuple((f"a{n}", f"b{n}") for n in range(10)))
    limits = DocumentLimits(max_docx_table_rows=2)
    result = extract_docx(data, limits)
    table_anchors = [a.page_anchor for a in result.chunks if a.page_anchor.startswith("t:")]
    assert len(table_anchors) == 2
    assert "docx.table_truncated" in result.notes


def test_engine_documents_docx(tmp_path):
    harness = Harness(tmp_path)
    harness.write("report.docx", make_docx(paragraphs=("unique docx phrase",)))
    assert harness.index("report.docx") is True
    record = harness.store.get_file("report.docx")
    assert record is not None
    assert record.notes == ()
    hits = harness.store.search("unique docx phrase")
    assert hits and hits[0]["relative_path"] == "report.docx"
    harness.close()
