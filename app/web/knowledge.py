"""Knowledge base and ACL-aware snippet retrieval router (P12-T03).

Provides the `/app/knowledge` route displaying indexed snippets from mounted
cloud drives, email attachments, and local gateways with strict ACL filtering.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.services.knowledge.workspace import snippet_rows
from app.web.deps import render

router = APIRouter()


@router.get("/knowledge")
async def knowledge_page(
    request: Request, q: str = "", boundary: str = Query("cloud", pattern="^(local_only|company|cloud)$")
):
    """Render knowledge snippets catalog with ACL and data boundary checks."""
    snippets = await snippet_rows("knowledge", boundary, q)

    return render(
        request,
        "web/knowledge.html",
        evidence_rows=snippets,
        q=q,
        boundary=boundary,
    )
