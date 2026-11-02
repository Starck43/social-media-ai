"""Procurement cases, supplier comparison table, and approval router (P12-T05).

Provides the `/app/cases` route displaying deterministic supplier price comparisons
in plain user-friendly Russian, with version-bound approval status and transparent
4-layer connector provenance.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.services.knowledge.workspace import snippet_rows
from app.web.deps import render

router = APIRouter()


@router.get("/cases")
async def cases_page(request: Request, boundary: str = Query("cloud", pattern="^(local_only|company|cloud)$")):
    """Render procurement case comparison table and version-bound approval card."""
    return render(
        request,
        "web/cases.html",
        evidence_rows=await snippet_rows("case", boundary),
        boundary=boundary,
    )
