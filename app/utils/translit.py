"""Cyrillic → Latin transliteration for URL-safe slugs.

No external dependency: a compact GOST-style table covers the Russian alphabet
plus ASCII letters and digits. Characters outside the table pass through
translation, then the slug filter removes anything not ASCII alphanumeric.
"""

from __future__ import annotations

import re

_TRANSLITERATION_TABLE = str.maketrans(
    {
        "а": "a",
        "б": "b",
        "в": "v",
        "г": "g",
        "д": "d",
        "е": "e",
        "ё": "e",
        "ж": "zh",
        "з": "z",
        "и": "i",
        "й": "y",
        "к": "k",
        "л": "l",
        "м": "m",
        "н": "n",
        "о": "o",
        "п": "p",
        "р": "r",
        "с": "s",
        "т": "t",
        "у": "u",
        "ф": "f",
        "х": "kh",
        "ц": "ts",
        "ч": "ch",
        "ш": "sh",
        "щ": "shch",
        "ъ": "",
        "ы": "y",
        "ь": "",
        "э": "e",
        "ю": "yu",
        "я": "ya",
    }
)

_NON_SLUG_CHARACTERS = re.compile(r"[^a-z0-9]+")


def translit_slug(text: str) -> str:
    """Lowercase, transliterate Cyrillic, strip to ASCII alphanumerics."""
    return _NON_SLUG_CHARACTERS.sub("", text.lower().translate(_TRANSLITERATION_TABLE))
