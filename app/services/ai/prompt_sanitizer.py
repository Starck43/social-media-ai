"""Prompt injection sanitizer for untrusted text fields.

User-supplied text (post body, comments, collected captions) is injected into
prompts verbatim. Before it reaches the LLM we strip the most common jailbreak /
instruction-override patterns so a malicious post cannot hijack the system
prompt.
"""

from __future__ import annotations

import re

_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"ignore\s+all\s+previous\s+instructions", re.IGNORECASE),
    re.compile(r"ignore\s+previous\s+instructions", re.IGNORECASE),
    re.compile(r"disregard\s+previous\s+instructions", re.IGNORECASE),
    re.compile(r"forget\s+previous\s+instructions", re.IGNORECASE),
    re.compile(r"system\s*:", re.IGNORECASE),
    re.compile(r"\[INST\]", re.IGNORECASE),
    re.compile(r"\[/INST\]", re.IGNORECASE),
    re.compile(r"<\|im_start\|>", re.IGNORECASE),
    re.compile(r"<\|im_end\|>", re.IGNORECASE),
    re.compile(r"###\s*Human\s*:", re.IGNORECASE),
    re.compile(r"###\s*Assistant\s*:", re.IGNORECASE),
]


def sanitize_untrusted_text(text: str) -> str:
    """Remove common prompt-injection control sequences from untrusted text.

    The replacement is a single space so the surrounding text remains readable
    and token boundaries do not shift dramatically.
    """
    if not text:
        return text
    cleaned = text
    for pattern in _INJECTION_PATTERNS:
        cleaned = pattern.sub(" ", cleaned)
    return cleaned
