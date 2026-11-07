"""Bounded PDF extraction with page anchors and metadata (pypdf).

Pypdf is the only PDF dependency. Page text is pulled page by page, so a
broken page becomes a per-page note instead of killing the file. Embedded
images are handed to the OCR runner only when it is ready, under a bounded
image budget. Extraction never raises: every failure is a note code.
"""

from __future__ import annotations

import io
import time
from collections.abc import Callable, Sequence
from datetime import datetime

import pypdf

from .limits import DocumentLimits
from .models import ExtractionChunk, ExtractionResult
from .ocr import OcrOutcome, OcrRunner

_COMPACT_DATE = "%Y%m%d%H%M%S"


def _compact(value: str) -> str:
    return "".join(part for part in value.split(" ")[0] if part not in "-:/T")


def _epoch(value: object) -> int | None:
    """Normalize a pypdf date/datetime value to epoch seconds; None on error."""

    if isinstance(value, bool) or value is None:
        return None
    if hasattr(value, "timestamp") and not isinstance(value, str):
        try:
            return int(value.timestamp())
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    text = _compact(value.strip().lstrip("D:"))
    if len(text) < 14:
        return None
    try:
        return int(datetime.strptime(text[:14], _COMPACT_DATE).timestamp())
    except ValueError:
        return None


def _image_to_png(source: object) -> bytes | None:
    """Rasterize a PIL image or raw image bytes to PNG; None on bad input."""

    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow is a declared dependency
        return None
    try:
        if isinstance(source, Image.Image):
            picture = source
        elif isinstance(source, (bytes, bytearray)):
            picture = Image.open(io.BytesIO(bytes(source)))
        else:
            return None
        picture.load()
        converted = picture.convert("RGB")
        buffer = io.BytesIO()
        converted.save(buffer, format="PNG")
        return buffer.getvalue()
    except (OSError, ValueError, MemoryError, TypeError):
        return None


def extract_pdf(
    data: bytes,
    limits: DocumentLimits,
    ocr: OcrRunner | None,
    *,
    reader_factory: Callable[[Sequence[bytes]], object] = pypdf.PdfReader,
    clock: Callable[[], float] = time.monotonic,
) -> ExtractionResult:
    """Extract bounded text/pages/metadata; every failure is a note code."""

    notes: list[str] = []

    try:
        reader = reader_factory(io.BytesIO(data))
    except Exception as exc:
        return ExtractionResult(chunks=(), notes=("pdf.unsupported_format", type(exc).__name__.lower()))

    author: str | None = None
    created: int | None = None
    modified: int | None = None
    try:
        metadata = reader.metadata
        if metadata is not None:
            author_value = metadata.get("/Author")
            if isinstance(author_value, str) and author_value.strip():
                author = author_value.strip()[:200]
            created = _epoch(metadata.get("/CreationDate"))
            modified = _epoch(metadata.get("/ModDate"))
    except Exception:
        pass

    try:
        encrypted = bool(reader.is_encrypted)
    except Exception:
        encrypted = True
    if encrypted:
        return ExtractionResult(chunks=(), author=author, created=created, modified=modified, notes=("pdf.encrypted",))

    ocr_images = 0
    ocr_failures = 0
    chunks: list[ExtractionChunk] = []
    characters = 0
    started = clock()

    try:
        total_pages = len(reader.pages)
    except Exception:
        return ExtractionResult(
            chunks=(), author=author, created=created, modified=modified, notes=("pdf.pages_unavailable",)
        )
    if total_pages > limits.max_pdf_pages:
        notes.append("pdf.pages_truncated")
        total_pages = limits.max_pdf_pages

    for zero in range(total_pages):
        if clock() - started > limits.max_document_seconds:
            notes.append("limit.time")
            break
        page_number = zero + 1
        page_anchor = str(page_number)
        try:
            page = reader.pages[zero]
            text = page.extract_text() or ""
        except Exception:
            notes.append(f"pdf.page_{page_number}_failure")
            continue
        if not isinstance(text, str):
            notes.append(f"pdf.page_{page_number}_failure")
            continue
        if text.strip():
            if characters + len(text) > limits.max_document_characters:
                notes.append("limit.characters")
                break
            characters += len(text)
            chunks.append(ExtractionChunk(text=text, page_anchor=page_anchor))

        if ocr is not None and ocr_images < limits.max_pdf_ocr_images:
            try:
                images = list(page.images)
            except Exception:
                images = ()
            for image in images:
                if ocr_images >= limits.max_pdf_ocr_images or ocr_failures >= 4:
                    break
                candidate = None
                try:
                    candidate = image.image
                except Exception:
                    candidate = None
                if candidate is None:
                    raw = getattr(image, "data", None)
                    candidate = raw if isinstance(raw, (bytes, bytearray)) else None
                if candidate is None or (isinstance(candidate, (bytes, bytearray)) and not candidate):
                    continue
                if isinstance(candidate, (bytes, bytearray)):
                    if len(candidate) > limits.max_embedded_image_bytes:
                        ocr_failures += 1
                        notes.append("ocr_image_too_large")
                        break
                    ocr_input = bytes(candidate)
                else:
                    ocr_input = candidate
                png: bytes | None
                if isinstance(ocr_input, bytes) and ocr_input[:3] == b"\xff\xd8\xff":
                    png = ocr_input
                else:
                    png = _image_to_png(ocr_input)
                if png is None:
                    ocr_failures += 1
                    notes.append("ocr_image_unsupported")
                    break
                # The budget also applies to the normalized payload that will
                # actually reach the OCR runner (PIL-sourced images skip the
                # raw-bytes check above).
                if len(png) > limits.max_embedded_image_bytes:
                    ocr_failures += 1
                    notes.append("ocr_image_too_large")
                    break
                ocr_images += 1
                outcome = ocr.ocr_image(png)
                if isinstance(outcome, OcrOutcome) and outcome.ok and outcome.text.strip():
                    if characters + len(outcome.text) > limits.max_document_characters:
                        notes.append("limit.characters")
                        break
                    characters += len(outcome.text)
                    chunks.append(ExtractionChunk(text=outcome.text, page_anchor=page_anchor))
                else:
                    ocr_failures += 1
                    note = outcome.note if isinstance(outcome, OcrOutcome) else "ocr_error"
                    notes.append(note or "ocr_error")
    return ExtractionResult(
        chunks=tuple(chunks),
        author=author,
        created=created,
        modified=modified,
        notes=tuple(dict.fromkeys(notes))[:16],
    )
