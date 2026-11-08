"""P18-T04: bounded PDF extraction with page anchors, metadata and OCR depth."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from helpers import Harness, make_jpeg, make_pdf, stub_tesseract  # noqa: E402

from local_connector.extract import pdf as pdf_module  # noqa: E402
from local_connector.extract.limits import DocumentLimits  # noqa: E402
from local_connector.extract.ocr import OcrOutcome, OcrRunner  # noqa: E402
from local_connector.extract.pdf import extract_pdf  # noqa: E402


def ocr_for(tmp_path):
    binary = stub_tesseract(tmp_path)
    limits = DocumentLimits(ocr_binary=str(binary))
    runner = OcrRunner(limits.with_ocr_budget(), workdir=tmp_path / "ocr-work", max_image_bytes=1 << 20)
    return runner


def test_text_pages_and_anchors(tmp_path):
    data = make_pdf(pages=("First page text", "Second page text"))
    result = extract_pdf(data, DocumentLimits(), None)
    assert [item.text for item in result.chunks] == ["First page text", "Second page text"]
    assert [item.page_anchor for item in result.chunks] == ["1", "2"]
    assert result.notes == ()
    assert result.author is None


def test_metadata_fields(tmp_path):
    from datetime import datetime

    data = make_pdf(
        pages=("meta",), author="P18 T04", creation="D:20260102030405+00'00'", modification="D:20260203040506+00'00'"
    )
    result = extract_pdf(data, DocumentLimits(), None)
    assert result.author == "P18 T04"
    assert result.created == int(datetime(2026, 1, 2, 3, 4, 5).timestamp())
    assert result.modified == int(datetime(2026, 2, 3, 4, 5, 6).timestamp())


def test_bad_pdf_becomes_note(tmp_path):
    result = extract_pdf(b"not a pdf at all", DocumentLimits(), None)
    assert result.chunks == ()
    assert result.notes[0].startswith("pdf.unsupported_format")


def test_pdf_backend_absent_is_note(monkeypatch):
    monkeypatch.setitem(sys.modules, "pypdf", None)
    result = extract_pdf(b"%PDF-1.4 fake", DocumentLimits(), None)
    assert result.chunks == ()
    assert result.notes == ("pdf.unavailable",)


def test_page_limit_truncates(tmp_path):
    data = make_pdf(pages=("one", "two", "three", "four", "five"))
    limits = DocumentLimits(max_pdf_pages=2)
    result = extract_pdf(data, limits, None)
    assert [item.page_anchor for item in result.chunks] == ["1", "2"]
    assert "pdf.pages_truncated" in result.notes


def test_encrypted_pdf_note():
    class _FakeReader:
        is_encrypted = True
        metadata = None

    def fake_factory(stream):
        return _FakeReader()

    result = extract_pdf(b"%PDF-1.4 fake", DocumentLimits(), None, reader_factory=fake_factory)
    assert result.notes == ("pdf.encrypted",)


def test_pages_unavailable(monkeypatch):
    class _BrokenPages:
        is_encrypted = False
        metadata = None

        @property
        def pages(self):
            raise RuntimeError("broken")

    result = extract_pdf(b"%PDF-1.4 fake", DocumentLimits(), None, reader_factory=lambda stream: _BrokenPages())
    assert result.notes == ("pdf.pages_unavailable",)


def test_page_failure_isolated(monkeypatch):
    class _Page:
        def __init__(self, text, broken=False):
            self._text = text
            self._broken = broken
            self.images = ()

        def extract_text(self):
            if self._broken:
                raise RuntimeError("boom")
            return self._text

    class _Reader:
        is_encrypted = False
        metadata = None

        def __init__(self):
            self._pages = [_Page("good page"), _Page("bad", broken=True)]

        @property
        def pages(self):
            pages = list(self._pages)
            return pages

    result = extract_pdf(b"%PDF-1.4 fake", DocumentLimits(), None, reader_factory=lambda stream: _Reader())
    assert [item.text for item in result.chunks] == ["good page"]
    assert "pdf.page_2_failure" in result.notes


def test_embedded_image_ocr(tmp_path):
    data = make_pdf(pages=("scan page",), jpeg=make_jpeg(), author="scanner")
    result = extract_pdf(data, DocumentLimits(), ocr_for(tmp_path))
    anchor_texts = {(item.page_anchor, item.text) for item in result.chunks}
    assert ("1", "OCR-TEXT-MARKER") in anchor_texts
    assert ("1", "scan page") in anchor_texts


def test_ocr_unavailable_noted(tmp_path):
    data = make_pdf(pages=("page",), jpeg=make_jpeg())

    class _NoRunner:
        def ocr_image(self, image):
            return OcrOutcome(None, "ocr_unavailable")

    result = extract_pdf(data, DocumentLimits(), _NoRunner())
    assert "ocr_unavailable" in result.notes
    assert any(item.text == "page" for item in result.chunks)


def test_image_budget_enforced(tmp_path):
    limits = DocumentLimits(max_embedded_image_bytes=8)
    data = make_pdf(pages=("page",), jpeg=make_jpeg())
    result = extract_pdf(data, limits, ocr_for(tmp_path))
    assert "ocr_image_too_large" in result.notes
    assert any(item.text == "page" for item in result.chunks)


def test_pdf_clock_budget(tmp_path):
    ticks = {"now": 0.0}

    def clock():
        ticks["now"] += 100.0
        return ticks["now"]

    data = make_pdf(pages=("one", "two"))
    result = extract_pdf(data, DocumentLimits(), None, clock=clock)
    assert "limit.time" in result.notes


def test_character_limit(tmp_path):
    big = "word " * 30000
    limits = DocumentLimits(max_document_characters=1000)
    data = make_pdf(pages=(big,))
    result = extract_pdf(data, limits, None)
    assert "limit.characters" in result.notes
    assert sum(len(item.text) for item in result.chunks) <= 1000


def test_jpeg_passthrough_and_png_fallback(tmp_path):
    jpeg = make_jpeg()
    assert jpeg[:3] == b"\xff\xd8\xff"
    from helpers import make_png

    png = make_png()
    assert pdf_module._image_to_png(png) is not None
    assert pdf_module._image_to_png(b"garbage not an image") is None
