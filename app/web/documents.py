"""Versioned documents and revision approval router (P12-T03).

Provides the `/app/documents` route displaying versioned contracts, specifications,
content hashes, and revision approval statuses.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.services.knowledge.documents import CANONICAL_DOCUMENTS
from app.web.deps import render

router = APIRouter()


@router.get("/documents")
async def documents_page(request: Request):
    """Render versioned documents page with immutable SHA256 hashes and approval states."""
    return render(
        request,
        "web/documents.html",
        documents=CANONICAL_DOCUMENTS,
        versioned_documents=CANONICAL_DOCUMENTS,
    )
