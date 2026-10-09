"""Versioned deterministic splitting for the digest renderer's HTML subset.

Preserve text/entities/whitespace and close/reopen active tags at boundaries.
No sanitization, truncation, HTTP or fallback-to-plain-text on invalid input.
The raw UTF-16 bound includes wrapper markup and fits both existing transports.
"""

from __future__ import annotations

import re
from html import unescape

PART_LIMIT = 4000
SPLITTER_VERSION = "digest-html-v1:4000-utf16"
_TAG = re.compile(r"</?(b|i|blockquote)>")
_ENTITY = re.compile(r"&(?:amp|lt|gt|quot|#[0-9]+|#x[0-9a-fA-F]+);")


class HtmlPartsError(ValueError):
    """Input cannot be safely represented by this explicit splitter version."""


def _fail() -> None:
    raise HtmlPartsError("Invalid or unsupported digest HTML")


def _units(text: str) -> int:
    try:
        return len(text.encode("utf-16-le")) // 2
    except UnicodeEncodeError:
        _fail()


def _tokens(text: str):
    index = 0
    while index < len(text):
        if text[index] == "<":
            match = _TAG.match(text, index)
            if not match:
                _fail()
            raw = match.group()
            yield raw, "close" if raw.startswith("</") else "open", match.group(1)
            index = match.end()
        elif text[index] == "&":
            match = _ENTITY.match(text, index)
            if not match:
                _fail()
            raw = match.group()
            if raw.startswith("&#"):
                numeric = raw[2:-1]
                try:
                    value = int(numeric[1:], 16) if numeric.startswith("x") else int(numeric)
                except ValueError:
                    _fail()
                if value == 0 or value > 0x10FFFF or 0xD800 <= value <= 0xDFFF:
                    _fail()
            yield raw, "text", None
            index = match.end()
        else:
            yield text[index], "text", None
            index += 1


def _atoms(text: str):
    # Keep trailing whitespace attached to preceding visible content. Otherwise
    # a greedy cut can leave a final newline-only message (provider rejects it).
    # Long indivisible whitespace runs fail closed; never silently strip them.
    group = []
    has_content = False
    for token in _tokens(text):
        meaningful = token[1] == "text" and bool(unescape(token[0]).strip())
        if meaningful and has_content:
            yield group
            group = []
            has_content = False
        group.append(token)
        has_content = has_content or meaningful
    if group:
        yield group


def split_digest_html(text: str, *, limit: int = PART_LIMIT) -> list[str]:
    """Split b/i/blockquote HTML into independently balanced parts.

    Entities and Unicode code points are atomic; tags are never cut. This v1
    does not promise grapheme-cluster or paragraph/word boundary preservation.
    Only the default limit corresponds to SPLITTER_VERSION for frozen resume.
    Whitespace-only parts and markup too wide for the limit fail closed rather
    than discard original text. Unknown tags/attributes/malformed nesting fail.
    """
    if not isinstance(text, str) or not text.strip() or type(limit) is not int or not 0 < limit <= PART_LIMIT:
        _fail()
    _units(text)
    stack: list[str] = []
    buffer: list[str] = []
    size = 0
    meaningful = False
    parts: list[str] = []

    def finish() -> None:
        if not meaningful:
            _fail()
        parts.append("".join(buffer) + "".join(f"</{tag}>" for tag in reversed(stack)))

    for atom in _atoms(text):
        next_stack = list(stack)
        raw = "".join(token[0] for token in atom)
        for _, action, tag in atom:
            if action == "open":
                next_stack.append(tag)
            elif action == "close":
                if not next_stack or next_stack[-1] != tag:
                    _fail()
                next_stack.pop()
        closing_size = sum(len(t) + 3 for t in next_stack)
        raw_size = _units(raw)
        if size + raw_size + closing_size > limit:
            finish()
            buffer = [f"<{t}>" for t in stack]
            size = sum(len(t) + 2 for t in stack)
            meaningful = False
            if size + raw_size + closing_size > limit:
                _fail()
        buffer.append(raw)
        size += raw_size
        stack = next_stack
        if any(action == "text" and unescape(value).strip() for value, action, _ in atom):
            meaningful = True
    if stack:
        _fail()
    finish()
    return parts
