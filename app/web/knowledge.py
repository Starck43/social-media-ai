"""Knowledge base and ACL-aware snippet retrieval router (P12-T03).

Provides the `/app/knowledge` route displaying indexed snippets from mounted
cloud drives, email attachments, and local gateways with strict ACL filtering.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.services.knowledge.retrieval import CANONICAL_SNIPPETS, search_knowledge_snippets
from app.web.deps import render

router = APIRouter()


@router.get("/knowledge")
async def knowledge_page(request: Request, q: str = "", boundary: str = "cloud"):
    """Render knowledge snippets catalog with ACL and data boundary checks."""
    user = getattr(request.state, "web_user", None)
    user_scopes: list[str] = getattr(user, "scopes", None) or ["public", "contracts:read", "procurement:read"]

    snippets = search_knowledge_snippets(
        query=q,
        snippets=CANONICAL_SNIPPETS,
        user_scopes=user_scopes,
        allowed_boundary=boundary,
    )

    return render(
        request,
        "web/knowledge.html",
        knowledge_snippets=snippets,
        q=q,
        boundary=boundary,
    )
