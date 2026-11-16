"""Bounded text-model types shared by all extractors.

Extractors never raise for bad content: a failure becomes a sanitized note
code (``<area>.<code>``) inside the result, so the indexer records the file
with its metadata instead of crashing the scan.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

MAX_NOTE_CODES = 16
MAX_NAMED_VALUE = 200


@dataclass(frozen=True)
class ExtractionChunk:
    """Bounded text unit with an optional structural anchor.

    Anchors are opaque short strings for page or sheet references; they carry
    no untrusted content beyond the documented code format.
    """

    text: str
    page_anchor: str | None = None
    sheet_anchor: str | None = None


@dataclass(frozen=True)
class ExtractionResult:
    """Canonical bounded extraction output for one file.

    The default repr is deliberately empty: text is untrusted input and must
    not be logged or serialized automatically.
    """

    chunks: tuple[ExtractionChunk, ...]
    author: str | None = None
    created: int | None = None
    modified: int | None = None
    notes: tuple[str, ...] = ()

    def __repr__(self) -> str:
        return f"ExtractionResult(chunks={len(self.chunks)}, notes={self.notes!r})"

    def limit_notes(self) -> tuple[str, ...]:
        """Keep the earliest failure notes so one note list stays bounded."""

        return self.notes[:MAX_NOTE_CODES]


def encode_notes(notes: tuple[str, ...]) -> str:
    """Persist notes as bounded JSON; malformed input degrades to an empty list."""

    safe = [note for note in notes[:MAX_NOTE_CODES] if isinstance(note, str) and 0 < len(note) <= 128]
    return json.dumps(safe, ensure_ascii=True, separators=(",", ":"))


def decode_notes(raw: str | None) -> tuple[str, ...]:
    """Read notes back without ever raising on stored content."""

    if not raw:
        return ()
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return ()
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value[:MAX_NOTE_CODES] if isinstance(item, str) and 0 < len(item) <= 128)


def bounded_name(value: str | None) -> str | None:
    """Bounded, denormalizable stored name (author/sheet); never a data source."""

    if not isinstance(value, str) or not value:
        return None
    name = value.strip()
    if not name:
        return None
    return name[:MAX_NAMED_VALUE]
