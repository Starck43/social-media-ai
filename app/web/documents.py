"""Versioned documents and revision approval router (P12-T03).

Provides the `/app/documents` route displaying versioned contracts, specifications,
content hashes, and revision approval statuses.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.services.knowledge.workspace import document_rows
from app.web.deps import render

router = APIRouter()


@router.get("/documents")
async def documents_page(request: Request, boundary: str = Query("cloud", pattern="^(local_only|company|cloud)$")):
    """Render versioned documents page with immutable SHA256 hashes and approval states."""
    return render(
        request,
        "web/documents.html",
        evidence_rows=await document_rows(boundary),
        boundary=boundary,
    )
