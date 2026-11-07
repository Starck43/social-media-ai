"""Bounded encoding detection for plain-text files.

Deterministic probe order: BOMs (UTF-32/UTF-16/UTF-8), BOM-less UTF-16
(NUL-density heuristic with endianness), strict UTF-8, Latin-1 fallback.
The fallback is recorded as a note; probes are O(input) or bounded-by-prefix.
BOM-less UTF-16 text that contains no NUL bytes at all (for example,
Cyrillic-only text) cannot be distinguished from UTF-8 and degrades to the
UTF-8/Latin-1 path without raising.
"""

from __future__ import annotations

import codecs

from .models import ExtractionChunk, ExtractionResult

_UTF8_BOM = codecs.BOM_UTF8
_UTF32_LE_BOM = codecs.BOM_UTF32_LE
_UTF32_BE_BOM = codecs.BOM_UTF32_BE
_UTF16_LE_BOM = codecs.BOM_UTF16_LE
_UTF16_BE_BOM = codecs.BOM_UTF16_BE

# NUL-density lookahead for BOM-less UTF-16 detection; a bounded window keeps
# the probe cheap for every text file. Real UTF-16 text carries at least a
# fifth of its bytes as NUL (ASCII-range characters); UTF-8/Latin-1 text
# almost never does.
_UTF16_PROBE_BYTES = 8192
_UTF16_MIN_PROBE = 32
_UTF16_NUL_FRACTOR_NUM = 5  # NUL bytes * 5 >= size  ->  >= 20% NUL density
_UTF16_NUL_FRACTOR_DEN = 1

_BOM_FIRST: tuple[tuple[bytes, str], ...] = (
    (_UTF32_BE_BOM, "utf-32-be"),
    (_UTF32_LE_BOM, "utf-32-le"),
    (_UTF16_BE_BOM, "utf-16-be"),
    (_UTF16_LE_BOM, "utf-16-le"),
    (_UTF8_BOM, "utf-8-sig"),
)


def _utf16_flavor(text: bytes) -> str | None:
    """Pick little/big-endian for BOM-less UTF-16; None when not confident."""

    window = text[:_UTF16_PROBE_BYTES]
    size = len(window)
    if size < _UTF16_MIN_PROBE:
        return None
    if window.count(0) * _UTF16_NUL_FRACTOR_NUM < size * _UTF16_NUL_FRACTOR_DEN:
        return None
    little = window[1::2].count(0)
    big = window[0::2].count(0)
    return "utf-16-le" if little >= big else "utf-16-be"


def detect_encoding(text: bytes) -> tuple[str, str | None]:
    """Return the codec to use and an optional fallback note."""

    for bom, codec in _BOM_FIRST:
        if text.startswith(bom):
            return codec, None
    flavor = _utf16_flavor(text)
    if flavor is not None:
        return flavor, None
    try:
        text.decode("utf-8")
        return "utf-8", None
    except UnicodeDecodeError:
        pass
    return "latin-1", "text.encoding_fallback_latin1"


def extract_plain_text(data: bytes, *, chunk_size: int = 4000) -> ExtractionResult:
    """Extract bounded .txt/.md text; failures become notes, never exceptions."""

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if not data:
        return ExtractionResult(chunks=(), notes=("text.empty",))
    try:
        codec, note = detect_encoding(data)
        text = codecs.decode(data, codec)
    except (UnicodeDecodeError, LookupError):
        return ExtractionResult(chunks=(), notes=("text.invalid_encoding",))
    if text.startswith("\ufeff"):
        text = text[1:]
    if not text:
        return ExtractionResult(chunks=(), notes=("text.empty",))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    chunks = tuple(
        ExtractionChunk(text=text[offset : offset + chunk_size]) for offset in range(0, len(text), chunk_size)
    )
    notes: tuple[str, ...] = (note,) if note else ()
    return ExtractionResult(chunks=chunks, notes=notes)
