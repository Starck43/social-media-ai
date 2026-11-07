"""Bounded text extraction and OCR for the local connector scaffold.

The dispatcher never raises for bad content: unsupported and malformed files
become note codes inside ``ExtractionResult`` so the indexer records the file
with its metadata instead of crashing the scan.
"""

from __future__ import annotations

from pathlib import Path

from . import images  # noqa: F401
from .docx import extract_docx
from .images import IMAGE_SUFFIXES, extract_image
from .limits import DocumentLimits
from .models import ExtractionChunk, ExtractionResult
from .ocr import OcrRunner
from .pdf import extract_pdf
from .text import extract_plain_text
from .xlsx import extract_xlsx

__all__ = [
    "DocumentLimits",
    "ExtractionChunk",
    "ExtractionResult",
    "OcrRunner",
    "extract_file",
]

_TEXT_SUFFIXES = {".txt", ".md", ".markdown"}


def extract_file(
    data: bytes,
    name: str,
    limits: DocumentLimits,
    ocr: OcrRunner | None = None,
    *,
    chunk_size: int = 4000,
) -> ExtractionResult:
    """Run the bounded extractor for one owned suffix; failures are notes.

    Raster images are OCR-processed here so the engine keeps a single call
    path for every supported format.
    """

    suffix = Path(name).suffix.lower()
    try:
        if suffix in _TEXT_SUFFIXES:
            return extract_plain_text(data, chunk_size=chunk_size)
        if suffix == ".pdf":
            return extract_pdf(data, limits, ocr)
        if suffix == ".docx":
            return extract_docx(data, limits)
        if suffix == ".xlsx":
            return extract_xlsx(data, limits)
        if suffix in IMAGE_SUFFIXES:
            return extract_image(data, name, limits, ocr)
    except Exception as exc:
        return ExtractionResult(chunks=(), notes=("extraction." + type(exc).__name__.lower(),))
    return ExtractionResult(chunks=(), notes=("unsupported_format",))
