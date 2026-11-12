"""Bounded, inert UTF-8 extraction; no I/O, authorization or retained storage.

Only the isolated helper contract in docs/file-workspace/FILE_WORKSPACE_PLAIN_TEXT.md is
implemented. Real reads, processing policy and version lineage belong to the
owning service. These defensive limits are not approved production budgets.
"""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass

MAX_INPUT_BYTES = 262144
MAX_TEXT_CHARACTERS = 32768
MAX_LINES = 1000
MAX_NAME_CHARACTERS = 500
PARSER_VERSION = "utf8-plain-v1"


class PlainTextExtractionError(ValueError):
    """Sanitized rejection code only; never include source data in messages."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True, repr=False)
class PlainTextResult:
    """Canonical untrusted text and byte provenance, not a content version.

    The default repr hides ALL fields, including hashes and sizes. Consumers
    must not log/serialize this result automatically or treat text as commands.
    """

    text: str
    raw_sha256: str
    text_sha256: str
    parser_version: str
    input_bytes: int
    text_characters: int
    line_count: int


def extract_plain_text(payload: bytes, *, name: str) -> PlainTextResult:
    """Extract complete bounded .txt bytes, or reject without partial output.

    Check order: filename, exact bytes type, size, UTF-8, character safety,
    canonical output bounds. A display name is never opened or authorized.
    """
    if (
        not isinstance(name, str)
        or not name
        or len(name) > MAX_NAME_CHARACTERS
        or not name[-4:].lower() == ".txt"
        or "/" in name
        or "\\" in name
        or any(unicodedata.category(char).startswith("C") for char in name)
    ):
        raise PlainTextExtractionError("unsupported_format")
    if type(payload) is not bytes:
        raise PlainTextExtractionError("invalid_input")
    if len(payload) > MAX_INPUT_BYTES:
        raise PlainTextExtractionError("input_too_large")

    # Raise outside the except block: no decoder exception retaining raw input
    # is chained into even an automatically logged sanitized error traceback.
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    if text is None:
        raise PlainTextExtractionError("invalid_encoding")
    if text.startswith("\ufeff"):
        text = text[1:]
    if any(unicodedata.category(char).startswith("C") and char not in "\t\n\r" for char in text):
        raise PlainTextExtractionError("unsafe_text")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    line_count = text.count("\n") + 1 if text else 0
    if len(text) > MAX_TEXT_CHARACTERS or line_count > MAX_LINES:
        raise PlainTextExtractionError("extraction_limit")
    return PlainTextResult(
        text=text,
        raw_sha256=hashlib.sha256(payload).hexdigest(),
        text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        parser_version=PARSER_VERSION,
        input_bytes=len(payload),
        text_characters=len(text),
        line_count=line_count,
    )
