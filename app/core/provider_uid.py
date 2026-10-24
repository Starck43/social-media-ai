"""The provider UID: a short slug that links and API payloads point at.

One rule for both catalogs. The web settings screen derives it in the browser
while the user types a name; the console form asks the server. Two copies of
"take the name up to the dot, lowercase it, keep `a-z0-9_-`" would drift, and a
UID is not re-derivable once someone's link already contains it.
"""

from __future__ import annotations

import re

KEEP = re.compile(r"[^a-z0-9_-]+")
EDGES = re.compile(r"^-+|-+$")
MAX_LENGTH = 100


def slugify_uid(value: str | None) -> str:
    """`UnoRouter.AI` -> `unorouter`; `  Qwen 3 .Plus` -> `qwen-3`.

    Only the part before the first dot is kept: it is the provider's own name,
    the rest is a domain suffix. Characters outside `a-z0-9_-` collapse into a
    single dash, dashes at either end are trimmed, and an empty result means
    "nothing usable" — the caller decides whether that is a default or a
    validation error.
    """
    if not value:
        return ""
    head = str(value).split(".")[0].lower().strip()
    return EDGES.sub("", KEEP.sub("-", head))[:MAX_LENGTH]
