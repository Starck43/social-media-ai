"""Cyrillic → Latin transliteration for URL-safe slugs.

No external dependency: a compact GOST-style table covers the Russian alphabet
plus the few characters that survive the slug filter. Anything not in the table
falls through unchanged (the caller strips non-alphanumerics afterwards).
"""

from __future__ import annotations

_CYRILLIC_TO_LATIN: dict[str, str] = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def translit_slug(text: str) -> str:
    """Lowercase, transliterate Cyrillic, strip to ASCII alphanumerics."""
    out = []
    for ch in text.lower():
        out.append(_CYRILLIC_TO_LATIN.get(ch, ch))
    return "".join(c for c in out if c.isascii() and c.isalnum())
