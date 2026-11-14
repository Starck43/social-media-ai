"""Compatibility entry point of the dispatch seam (re-export).

The seam, its compatibility adapter and its identity key live in
[dispatch_seam](dispatch_seam.py); clients import only that module so one
provider attempt crosses exactly one module boundary.
"""

from __future__ import annotations

from app.services.ai.dispatch_seam import (  # noqa: F401
    CHAT_DISPATCH_ID_KEY,
    dispatch_attempt,
    seam_enabled,
)

#: Private key that carries the seam identity of a chat try to the journal.
DISPATCH_ID_KEY = CHAT_DISPATCH_ID_KEY
