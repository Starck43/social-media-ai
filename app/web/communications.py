"""Communication outbox, verified routes, and delivery approval router (P12-T04).

Provides the `/app/communications` route displaying verified recipient routes,
outbox lifecycle state machines, content hashes, TTL approvals, and kill-switch controls.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.services.communications.outbox import CANONICAL_OUTBOX_ITEMS
from app.web.deps import render

router = APIRouter()


@router.get("/communications")
async def communications_page(request: Request):
    """Render communications outbox catalog with verified routes and lifecycle controls."""
    return render(
        request,
        "web/communications.html",
        outbox_items=CANONICAL_OUTBOX_ITEMS,
        outbox_messages=CANONICAL_OUTBOX_ITEMS,
    )
