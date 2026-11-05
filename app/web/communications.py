"""Communication unified inbox, outbox, and contact reconciliation router (P12-T04, P15-T03, P15-T04).

Provides `/app/communications` with unified inbox tabs (`inbox`, `outbox`, `contacts_merge`),
metadata-only thread/message filtering, chronological thread view with channel badges
and unavailable-original indicator (`TZ 2.5`), and evidence drawer handoff (`data_boundary` respected).
Also mounts `/app/contacts/merge` (`P15-T03`).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.core.database import async_session_maker
from app.core.tenant_context import TenantContextError, current_tenant_id
from app.services.communications.inbox import (
    CHANNEL_BADGES,
    get_thread_detail,
    list_inbox_threads,
)
from app.services.contacts.reconcile import suggest_contact_merges
from app.services.knowledge.workspace import snippet_rows
from app.web.contacts_merge import router as contacts_merge_router
from app.web.deps import render

router = APIRouter()
router.include_router(contacts_merge_router)


@router.get("/communications")
async def communications_page(
    request: Request,
    boundary: str = Query("cloud", pattern="^(local_only|company|cloud)$"),
    tab: str = Query("inbox", pattern="^(inbox|outbox|contacts_merge)$"),
    channel: str | None = Query(None),
    contact_id: int | None = Query(None, gt=0),
    thread_id: int | None = Query(None, gt=0),
    is_read: bool | None = Query(None),
    is_archived: bool = Query(False),
    q: str | None = Query(None, max_length=200),
    before_id: int | None = Query(None, gt=0),
):
    """Render unified inbox tabs, chronological thread messages, and outbox evidence catalog."""
    evidence_rows = await snippet_rows("communication", boundary)
    inbox_threads: list[dict[str, Any]] = []
    next_before_id: int | None = None
    selected_thread_detail: dict[str, Any] | None = None
    merge_suggestions: list[dict[str, Any]] = []

    if current_tenant_id() is not None:
        try:
            async with async_session_maker() as session:
                page = await list_inbox_threads(
                    session,
                    channel=channel,
                    contact_id=contact_id,
                    is_read=is_read,
                    is_archived=is_archived,
                    q=q,
                    limit=50,
                    before_id=before_id,
                )
                inbox_threads = page["items"]
                next_before_id = page["next_before_id"]
                active_thread_id = thread_id or (inbox_threads[0]["id"] if inbox_threads else None)
                if active_thread_id is not None:
                    selected_thread_detail = await get_thread_detail(session, int(active_thread_id))
                if tab == "contacts_merge":
                    merge_suggestions = await suggest_contact_merges(session, limit=50)
        except (PermissionError, TenantContextError):
            raise HTTPException(status_code=403) from None
        except Exception:  # noqa: BLE001
            pass

    return render(
        request,
        "web/communications.html",
        section="communications",
        evidence_rows=evidence_rows,
        boundary=boundary,
        active_tab=tab,
        channel_filter=channel or "",
        contact_id_filter=contact_id,
        is_read_filter=is_read,
        is_archived_filter=is_archived,
        q=q or "",
        inbox_threads=inbox_threads,
        next_before_id=next_before_id,
        selected_thread_detail=selected_thread_detail,
        merge_suggestions=merge_suggestions,
        channel_badges=CHANNEL_BADGES,
    )
