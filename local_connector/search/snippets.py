"""Safe FTS5 query parsing and bounded snippet extraction."""

from __future__ import annotations

import re
from collections.abc import Sequence

from .models import MAX_SNIPPET_CHARS

_QUOTED_OR_WORD = re.compile(r'"([^"]*)"|([^\s]+)')
_WORD_CHARS = re.compile(r"[\w]+", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")


def extract_search_terms(query: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Parse user query into exact phrase strings and plain search tokens."""

    phrases: list[str] = []
    tokens: list[str] = []
    for phrase_match, raw_token in _QUOTED_OR_WORD.findall(query):
        if phrase_match:
            cleaned = _WHITESPACE.sub(" ", phrase_match).strip()
            if cleaned:
                phrases.append(cleaned)
        elif raw_token:
            for part in _WORD_CHARS.findall(raw_token):
                if part:
                    tokens.append(part)
    return tuple(phrases), tuple(tokens)


def build_fts5_match_query(query: str) -> str | None:
    """Convert raw user query text into a safe, syntax-error-free FTS5 MATCH string."""

    phrases, tokens = extract_search_terms(query)
    clauses: list[str] = []
    for phrase in phrases:
        escaped = phrase.replace('"', '""')
        clauses.append(f'"{escaped}"')
    for token in tokens:
        escaped = token.replace('"', '""')
        clauses.append(f'"{escaped}"')
    if not clauses:
        return None
    return " ".join(clauses)


def _match_spans(text: str, terms: Sequence[str]) -> list[tuple[int, int]]:
    lowered = text.casefold()
    spans: list[tuple[int, int]] = []
    for term in terms:
        needle = term.casefold()
        if not needle:
            continue
        start = 0
        while True:
            pos = lowered.find(needle, start)
            if pos < 0:
                break
            spans.append((pos, pos + len(needle)))
            start = pos + len(needle)
            if len(spans) >= 64:
                return sorted(spans)
    spans.sort()
    return spans


def extract_snippet(
    text: str,
    query: str,
    *,
    max_chars: int = MAX_SNIPPET_CHARS,
    window_chars: int = 240,
) -> str | None:
    """Extract one bounded snippet centered around matches without returning a whole file."""

    if max_chars <= 0 or not isinstance(text, str) or not text.strip():
        return None

    limit = min(max_chars, MAX_SNIPPET_CHARS)
    budget = min(limit, max(window_chars, 32)) if limit >= 32 else limit
    normalized = _WHITESPACE.sub(" ", text).strip()
    if not normalized:
        return None
    if len(normalized) <= budget:
        return normalized[:limit]

    phrases, tokens = extract_search_terms(query)
    terms = tuple(dict.fromkeys([*phrases, *tokens]))
    spans = _match_spans(normalized, terms)
    if spans:
        match_start, match_end = spans[0]
        match_len = match_end - match_start
        if match_len >= budget:
            start = match_start
        else:
            context = budget - match_len
            start = max(0, match_start - context // 2)
    else:
        start = 0

    end = min(len(normalized), start + budget)
    if end == len(normalized):
        start = max(0, end - budget)

    has_prefix = start > 0
    has_suffix = end < len(normalized)

    # Adjust to clean word boundaries when doing so stays near the target span.
    if has_prefix:
        next_space = normalized.find(" ", start, min(end, start + 24))
        if next_space != -1 and (not spans or next_space + 1 <= spans[0][0]):
            start = next_space + 1
    if has_suffix:
        prev_space = normalized.rfind(" ", max(start, end - 24), end)
        if prev_space != -1 and (not spans or prev_space >= spans[0][1]):
            end = prev_space

    core = normalized[start:end].strip()
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(normalized) else ""

    allowed_core = limit - len(prefix) - len(suffix)
    if allowed_core <= 0:
        return normalized[:limit]
    if len(core) > allowed_core:
        core = core[:allowed_core].rstrip()
    result = f"{prefix}{core}{suffix}"
    return result[:limit] if result else None
