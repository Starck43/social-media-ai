"""Pure, bounded quotes tied to one in-memory read; not signed permissions.

No I/O, persistence, model, renderer or KnowledgeSnippet projection. Source
objects are trusted application outputs, never browser-supplied authorizations.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.integrations.file_workspace.content_read import ContentReadContext, ContentReadRequest, ReadPlainText
from app.integrations.file_workspace.plain_text import (
    MAX_INPUT_BYTES,
    MAX_TEXT_CHARACTERS,
    PlainTextResult,
    extract_plain_text,
)

MAX_EVIDENCE_RANGES = 8
MAX_EVIDENCE_LINES = 32
MAX_EVIDENCE_CHARACTERS = 8192


class LineEvidenceUnavailable(Exception):
    """Sanitized failure without text, paths, hashes, version or request IDs."""

    def __init__(self):
        super().__init__("line_evidence_unavailable")


@dataclass(frozen=True, slots=True, repr=False)
class LineRange:
    start: int
    end: int


@dataclass(frozen=True, slots=True, repr=False)
class LineExcerpt:
    start: int
    end: int
    text: str


@dataclass(frozen=True, slots=True, repr=False)
class LineEvidence:
    """Untrusted exact quotes, not conclusions, current rights or public links."""

    context: ContentReadContext
    raw_sha256: str
    text_sha256: str
    parser_version: str
    provider_version: str | None
    line_count: int
    excerpts: tuple[LineExcerpt, ...]


def _context_shape(context: object) -> bool:
    # Shape only, not an authorization implementation. Strict primitive types
    # prevent True/1 equality from making a tampered context appear identical.
    if type(context) is not ContentReadContext or type(context.request) is not ContentReadRequest:
        return False
    request = context.request
    return (
        all(
            type(value) is int and value > 0
            for value in (
                request.tenant_id,
                request.actor_id,
                request.connection_id,
                request.object_id,
                context.observation_number,
            )
        )
        and type(request.nonce) is str
        and re.fullmatch(r"[0-9a-f]{32}", request.nonce) is not None
        and all(
            type(value) is str
            for value in (
                context.path,
                context.name,
                context.root_key,
                context.policy_key,
                context.credential_key,
                context.processing_boundary,
                context.processing_policy_key,
            )
        )
        and context.processing_boundary == "cloud"
    )


def _source_lines(source: ReadPlainText) -> list[str]:
    if (
        type(source) is not ReadPlainText
        or not _context_shape(source.context)
        or type(source.extraction) is not PlainTextResult
    ):
        raise ValueError
    # Validate canonical text/provenance consistency, NOT real authority or the
    # raw hash (raw bytes are intentionally absent). No new read/version claim.
    result = source.extraction
    if type(result.text) is not str or len(result.text) > MAX_TEXT_CHARACTERS:
        raise ValueError
    canonical = extract_plain_text(result.text.encode("utf-8"), name=source.context.name)
    if (
        canonical.text != result.text
        or type(result.text_characters) is not int
        or result.text_characters != canonical.text_characters
        or type(result.line_count) is not int
        or result.line_count != canonical.line_count
        or type(result.parser_version) is not str
        or result.parser_version != canonical.parser_version
        or type(result.text_sha256) is not str
        or result.text_sha256 != canonical.text_sha256
        or type(result.raw_sha256) is not str
        or not re.fullmatch(r"[0-9a-f]{64}", result.raw_sha256)
        or type(result.input_bytes) is not int
        or not canonical.input_bytes <= result.input_bytes <= MAX_INPUT_BYTES
    ):
        raise ValueError
    version = source.provider_version
    if version is not None and (
        type(version) is not str
        or not 0 < len(version) <= 512
        or any(unicodedata.category(char).startswith("C") for char in version)
    ):
        raise ValueError
    return result.text.split("\n") if result.text else []


def _build(source, ranges) -> LineEvidence:
    if type(ranges) is not tuple or not 1 <= len(ranges) <= MAX_EVIDENCE_RANGES:
        raise ValueError
    lines = _source_lines(source)
    previous_end = total_lines = total_characters = 0
    excerpts = []
    for selected in ranges:
        if (
            type(selected) is not LineRange
            or type(selected.start) is not int
            or type(selected.end) is not int
            or not previous_end < selected.start <= selected.end <= len(lines)
        ):
            raise ValueError
        total_lines += selected.end - selected.start + 1
        chosen = lines[selected.start - 1 : selected.end]
        # Include only LF separators WITHIN this range, not outside its boundary
        # or across disjoint excerpts. Never strip, merge, reorder or truncate.
        total_characters += sum(len(line) for line in chosen) + selected.end - selected.start
        if total_lines > MAX_EVIDENCE_LINES or total_characters > MAX_EVIDENCE_CHARACTERS:
            raise ValueError
        excerpts.append(LineExcerpt(selected.start, selected.end, "\n".join(chosen)))
        previous_end = selected.end
    result = source.extraction
    return LineEvidence(
        source.context,
        result.raw_sha256,
        result.text_sha256,
        result.parser_version,
        source.provider_version,
        result.line_count,
        tuple(excerpts),
    )


def build_line_evidence(source: ReadPlainText, *, ranges: tuple[LineRange, ...]) -> LineEvidence:
    """Quote 1-based inclusive sorted/disjoint ranges over canonical LF text.

    Limits: 8 ranges, 32 selected lines, 8192 total code points including internal
    LF separators. Empty source has zero lines and no valid range. Input types
    are exact; no iterable coercion. All output remains untrusted data. These
    synthetic bounds are provisional pending P12-T13 review.
    """
    try:
        return _build(source, ranges)
    except Exception:
        pass
    raise LineEvidenceUnavailable()


def verify_line_evidence(evidence: LineEvidence, source: ReadPlainText) -> bool:
    """Check consistency against this SAME read, never current authorization.

    Rebuild exact quotes and compare the full context (including nonce), hashes,
    parser and version assertion. Even equal bytes in a second read are a
    different source. This is not cryptographic authentication: trusted code
    can construct/replace both objects. Future external use needs fresh rights.
    """
    try:
        if (
            type(evidence) is not LineEvidence
            or not _context_shape(evidence.context)
            or type(evidence.line_count) is not int
            or any(
                type(value) is not str for value in (evidence.raw_sha256, evidence.text_sha256, evidence.parser_version)
            )
            or (evidence.provider_version is not None and type(evidence.provider_version) is not str)
            or type(evidence.excerpts) is not tuple
            or not 1 <= len(evidence.excerpts) <= MAX_EVIDENCE_RANGES
            or any(type(item) is not LineExcerpt or type(item.text) is not str for item in evidence.excerpts)
        ):
            return False
        rebuilt = _build(source, tuple(LineRange(item.start, item.end) for item in evidence.excerpts))
        return rebuilt == evidence
    except Exception:
        return False
