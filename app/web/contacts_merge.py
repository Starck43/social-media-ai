"""Web workspace route for contact identity reconciliation and reversible merge (`P15-T03`)."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.core.database import async_session_maker
from app.core.tenant_context import current_tenant_id
from app.services.contacts.reconcile import suggest_contact_merges
from app.web.deps import render

router = APIRouter()


@router.get("/contacts/merge")
async def contacts_merge_page(
    request: Request,
    limit: int = Query(50, ge=1, le=100),
):
    """Render `/app/contacts/merge` candidate suggestions and manual merge/unmerge controls."""
    suggestions = []
    if current_tenant_id() is not None:
        try:
            async with async_session_maker() as session:
                suggestions = await suggest_contact_merges(session, limit=limit)
        except Exception:  # noqa: BLE001
            suggestions = []
    return render(
        request,
        "web/communications.html",
        active_tab="contacts_merge",
        merge_suggestions=suggestions,
    )
