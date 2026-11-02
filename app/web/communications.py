"""Communication outbox, verified routes, and delivery approval router (P12-T04).

Provides the `/app/communications` route displaying verified recipient routes,
outbox lifecycle state machines, content hashes, TTL approvals, and kill-switch controls.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.services.knowledge.workspace import snippet_rows
from app.web.deps import render

router = APIRouter()


@router.get("/communications")
async def communications_page(request: Request, boundary: str = Query("cloud", pattern="^(local_only|company|cloud)$")):
    """Render communications outbox catalog with verified routes and lifecycle controls."""
    return render(
        request,
        "web/communications.html",
        evidence_rows=await snippet_rows("communication", boundary),
        boundary=boundary,
    )
