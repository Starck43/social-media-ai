"""Follow-up reminders, quiet hours, and deadline tracking router (P12-T04).

Provides the `/app/followups` route displaying evidence-backed commitments,
quiet hours windows, imprecise date confirmations, and escalation policies.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.services.communications.followups import CANONICAL_FOLLOWUP_ITEMS
from app.web.deps import render

router = APIRouter()


@router.get("/followups")
async def followups_page(request: Request):
    """Render follow-up reminders catalog with quiet hours and human date confirmation gates."""
    return render(
        request,
        "web/followups.html",
        followup_items=CANONICAL_FOLLOWUP_ITEMS,
        reminders=CANONICAL_FOLLOWUP_ITEMS,
    )
