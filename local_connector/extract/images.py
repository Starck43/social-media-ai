"""Bounded raster-image preparation for OCR.

Top-level raster files are normalized to PNG with Pillow before they reach
the OCR runner. Non-image bytes become a note; oversize rasters are rejected
before any decode.
"""

from __future__ import annotations

from pathlib import Path

from .limits import DocumentLimits
from .models import ExtractionChunk, ExtractionResult
from .ocr import OcrOutcome, OcrRunner
from .pdf import _image_to_png

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".gif", ".webp")

# Tesseract reads these natively; anything else is normalized through Pillow.
_NATIVE_RASTERS: dict[str, bytes] = {
    ".png": b"\x89PNG",
    ".jpg": b"\xff\xd8\xff",
    ".jpeg": b"\xff\xd8\xff",
    ".bmp": b"BM",
    ".tiff": b"II\x2a\x00",
    ".tif": b"II\x2a\x00",
}


def image_suffix(name: str) -> str | None:
    suffix = Path(name).suffix.lower()
    return suffix if suffix in IMAGE_SUFFIXES else None


def prepare_image(data: bytes, name: str, limits: DocumentLimits) -> tuple[bytes | None, str | None]:
    """Return (ocr_payload, note) for one image file; payload None on failure."""

    suffix = image_suffix(name)
    if suffix is None:
        return None, "image.unsupported_format"
    if len(data) > limits.max_embedded_image_bytes:
        return None, "image.too_large"
    marker = _NATIVE_RASTERS.get(suffix)
    if marker is not None and data.startswith(marker):
        return bytes(data), None
    png = _image_to_png(data)
    if png is None:
        return None, "image.unsupported_format"
    return png, None


def extract_image(data: bytes, name: str, limits: DocumentLimits, ocr: OcrRunner | None = None) -> ExtractionResult:
    """OCR one raster file to bounded text chunks; failures are notes."""

    payload, note = prepare_image(data, name, limits)
    if payload is None:
        return ExtractionResult(chunks=(), notes=(note,))
    if ocr is None:
        return ExtractionResult(chunks=(), notes=("ocr_unavailable",))
    outcome = ocr.ocr_image(payload)
    if not isinstance(outcome, OcrOutcome) or not outcome.ok:
        return ExtractionResult(chunks=(), notes=(outcome.note if isinstance(outcome, OcrOutcome) else "ocr_error",))
    text = outcome.text.strip()
    if not text:
        return ExtractionResult(chunks=())
    if len(text) > limits.max_document_characters:
        text = text[: limits.max_document_characters]
        return ExtractionResult(chunks=(), notes=("limit.characters",))
    chunks = tuple(ExtractionChunk(text=text[offset : offset + 4000]) for offset in range(0, len(text), 4000))
    return ExtractionResult(chunks=chunks)
