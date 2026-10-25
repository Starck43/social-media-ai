"""A small Russian inflection stripper for task text scope.

VK and Telegram users write the same place or brand in different cases:
«Италия», «Италию», «Италии» are one topic, but an exact word match treats them
as three. A full morphological analyser (pymorphy) is a heavy dependency for
this one job, so the scope uses a deliberately small suffix stripper: it only
needs to map inflected forms of a word to a common prefix, never to produce a
dictionary lemma.

Rules:

- only Cyrillic words of four or more characters are touched — short function
  words (`в`, `на`, `и`) stay as they are;
- the longest known ending wins, and a stem shorter than three characters is
  never produced, so `дом` does not become `д`;
- `ё` is folded to `е`, matching the scope's own normalisation.

The result is a *matching key*, not a word to show to a person.
"""

from __future__ import annotations

import re
from functools import lru_cache

_WORD_RE = re.compile(r"\w+", re.UNICODE)
_CYR_RE = re.compile(r"[а-яё]", re.UNICODE)

# Inflectional endings, longest first. Covers the noun/adjective/verb forms that
# appear in social posts; a missed rare form degrades to one extra candidate,
# while an over-stripped short stem would conflate unrelated words.
_ENDINGS: tuple[str, ...] = tuple(
    sorted(
        {
            "иями", "ями", "ами", "ыми", "ими", "ией", "иям", "ием", "иях",
            "ого", "его", "ому", "ему", "ыми", "ими", "ей", "ий", "ый", "ой",
            "ая", "ое", "ые", "ых", "их", "ую", "юю", "ов", "ев", "ам", "ям",
            "ах", "ях", "ом", "ем", "а", "е", "ё", "и", "й", "о", "у", "ы",
            "ь", "я", "ю", "ться", "тся", "ешь", "ишь", "ете", "ите", "ут",
            "ют", "ат", "ят", "ил", "ила", "или", "ло", "ть", "ся",
        },
        key=len,
        reverse=True,
    )
)


@lru_cache(maxsize=4096)
def stem_word(word: str) -> str:
    """Matching key for one word: lowercase, `ё`→`е`, longest ending removed."""
    value = (word or "").lower().replace("ё", "е")
    if len(value) < 4 or not _CYR_RE.search(value):
        return value
    for ending in _ENDINGS:
        if value.endswith(ending) and len(value) - len(ending) >= 3:
            return value[: -len(ending)]
    return value


def stem_tokens(text: str) -> list[str]:
    """Stemmed word sequence of a text, in order."""
    return [stem_word(token) for token in _WORD_RE.findall((text or "").lower().replace("ё", "е"))]
