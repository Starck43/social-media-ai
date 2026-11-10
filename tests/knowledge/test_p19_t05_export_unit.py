"""Rendering tests use synthetic text only; auth and delivery are untested."""

from dataclasses import replace
from hashlib import sha256
from io import BytesIO

import pytest
from docx import Document
from pypdf import PdfReader

from app.services.knowledge.export import ExportError, ExportRevision, render_revision


@pytest.fixture
def revision():
    body = 'Привет, мир!\n<img src="https://invalid.example/secret">'
    return ExportRevision(42, "sha256:" + sha256(body.encode()).hexdigest(), "Тест & <title>", body)


def test_docx_contains_literal_content_and_revision_metadata(revision):
    exported = render_revision(revision, "docx")
    doc = Document(BytesIO(exported.content))
    assert exported.extension == "docx" and exported.revision_id == 42
    assert exported.content_hash == revision.content_hash
    assert [p.text for p in doc.paragraphs] == [revision.title, revision.body_text]
    assert f"revision_id=42; content_hash={revision.content_hash}" == doc.core_properties.subject


def test_pdf_contains_unicode_literal_text_and_revision_metadata(revision):
    exported = render_revision(revision, "pdf")
    pdf = PdfReader(BytesIO(exported.content))
    text = "\n".join(page.extract_text() for page in pdf.pages)
    assert "Привет, мир!" in text
    assert "<img src=" in text
    assert pdf.metadata.title == revision.title
    assert f"revision_id=42; content_hash={revision.content_hash}" in pdf.metadata.get("/Keywords")
    assert exported.media_type == "application/pdf"


@pytest.mark.parametrize("format", ["docx", "pdf"])
def test_hash_mismatch_denies_export(revision, format):
    with pytest.raises(ExportError, match="revision_hash_mismatch"):
        render_revision(replace(revision, body_text="modified"), format)


@pytest.mark.parametrize(
    "changes", [{"revision_id": True}, {"content_hash": "bad"}, {"title": "bad\x00"}, {"body_text": "\ud800"}]
)
def test_invalid_snapshot_denies_export(revision, changes):
    with pytest.raises(ExportError, match="invalid_revision"):
        render_revision(replace(revision, **changes), "docx")


def test_invalid_format_is_rejected(revision):
    with pytest.raises(ExportError, match="invalid_format"):
        render_revision(revision, "html")


def test_sensitive_content_is_not_exposed_by_repr(revision):
    exported = render_revision(revision, "docx")
    assert revision.title not in repr(revision)
    assert revision.body_text not in repr(revision)
    assert repr(exported.content) not in repr(exported)


def test_docx_preserves_full_long_title_in_body(revision):
    long_title = "З" * 500
    result = render_revision(replace(revision, title=long_title), "docx")
    doc = Document(BytesIO(result.content))
    assert doc.paragraphs[0].text == long_title
    assert doc.core_properties.title == long_title[:255]
