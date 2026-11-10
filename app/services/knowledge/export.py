"""Render an already-authorized revision; no access, persistence, or delivery."""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from io import BytesIO
from typing import Literal


class ExportError(ValueError):
    """Invalid input or mismatched revision hash."""


@dataclass(frozen=True)
class ExportRevision:
    revision_id: int
    content_hash: str
    title: str = field(repr=False)
    body_text: str = field(repr=False)


@dataclass(frozen=True)
class ExportedFile:
    content: bytes = field(repr=False)
    media_type: str
    extension: str
    revision_id: int
    content_hash: str


def _valid_xml_text(text: str) -> bool:
    return all(
        c in "\t\n\r" or "\x20" <= c <= "\ud7ff" or "\ue000" <= c <= "\ufffd" or "\U00010000" <= c <= "\U0010ffff"
        for c in text
    )


def _validate(revision: ExportRevision) -> None:
    if (
        type(revision.revision_id) is not int
        or revision.revision_id <= 0
        or not isinstance(revision.content_hash, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", revision.content_hash) is None
        or not isinstance(revision.title, str)
        or not isinstance(revision.body_text, str)
        or not _valid_xml_text(revision.title)
        or not _valid_xml_text(revision.body_text)
    ):
        raise ExportError("invalid_revision")
    actual = "sha256:" + hashlib.sha256(revision.body_text.encode("utf-8")).hexdigest()
    if actual != revision.content_hash:
        raise ExportError("revision_hash_mismatch")


def _no_fetch(url, *args, **kwargs):
    raise ExportError("external_resource_forbidden")


def render_revision(revision: ExportRevision, format: Literal["docx", "pdf"]) -> ExportedFile:
    """Render literal text; never interpret user content as markup or fetch URLs.

    This function is not an access check or an approval. Any future delivery
    must also bind the exact output bytes to the outbox approval.
    """
    _validate(revision)
    metadata = f"revision_id={revision.revision_id}; content_hash={revision.content_hash}"
    if format == "docx":
        from docx import Document

        document = Document()
        document.core_properties.title = revision.title[:255]
        document.core_properties.subject = metadata
        document.add_heading(revision.title, level=1)
        document.add_paragraph(revision.body_text)
        buffer = BytesIO()
        document.save(buffer)
        content = buffer.getvalue()
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif format == "pdf":
        from weasyprint import HTML

        source = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>{html.escape(revision.title)}</title>"
            f"<meta name='keywords' content='{html.escape(metadata, quote=True)}'>"
            "<style>body{font-family:'Noto Sans',sans-serif}"
            "h1{overflow-wrap:anywhere}pre{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere}</style>"
            f"</head><body><h1>{html.escape(revision.title)}</h1><pre>{html.escape(revision.body_text)}</pre></body></html>"
        )
        content = HTML(string=source, url_fetcher=_no_fetch).write_pdf()
        media_type = "application/pdf"
    else:
        raise ExportError("invalid_format")
    return ExportedFile(content, media_type, format, revision.revision_id, revision.content_hash)
