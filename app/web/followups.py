"""Follow-up reminders, quiet hours, and deadline tracking router (P12-T04).

Provides the `/app/followups` route displaying evidence-backed commitments,
quiet hours windows, imprecise date confirmations, and escalation policies.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.services.knowledge.workspace import snippet_rows
from app.web.deps import render

router = APIRouter()


@router.get("/followups")
async def followups_page(request: Request, boundary: str = Query("cloud", pattern="^(local_only|company|cloud)$")):
    """Render follow-up reminders catalog with quiet hours and human date confirmation gates."""
    return render(
        request,
        "web/followups.html",
        evidence_rows=await snippet_rows("followup", boundary),
        boundary=boundary,
    )
