"""Communication unified inbox, triage, outbox, and contact reconciliation router (P12-T04, P15-T03, P15-T04, P20-T09).

Provides `/app/communications` with unified inbox tabs (`inbox`, `outbox`, `contacts_merge`),
metadata-only thread/message filtering, inbox triage classification (`needs_reply`, `fyi`,
`can_wait`, `uncertain`) with editable proposals and user decision overrides (`P20-T09`),
chronological thread view with channel badges and unavailable-original indicator (`TZ 2.5`),
and evidence drawer handoff (`data_boundary` respected). Also mounts `/app/contacts/merge` (`P15-T03`).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.core.database import async_session_maker
from app.core.permissions import get_current_user
from app.core.tenant_context import TenantContextError, current_tenant_id
from app.services.communications.inbox import (
    CHANNEL_BADGES,
    InboxItemUnavailable,
    get_thread_detail,
    list_inbox_threads,
)
from app.services.communications.triage import (
    TRIAGE_CATEGORIES,
    TRIAGE_LABELS,
    list_triaged_threads,
    record_user_triage_override,
    triage_thread,
)
from app.services.contacts.reconcile import suggest_contact_merges
from app.services.knowledge.workspace import snippet_rows
from app.web.contacts_merge import router as contacts_merge_router
from app.web.deps import ensure_csrf, perms_can, render

logger = logging.getLogger(__name__)
router = APIRouter()
router.include_router(contacts_merge_router)


@router.get("/communications")
async def communications_page(
    request: Request,
    boundary: str = Query("cloud", pattern="^(local_only|company|cloud)$"),
    tab: str = Query("inbox", pattern="^(inbox|outbox|contacts_merge)$"),
    channel: str | None = Query(None),
    triage_category: str | None = Query(None),
    contact_id: int | None = Query(None, gt=0),
    thread_id: int | None = Query(None, gt=0),
    is_read: bool | None = Query(None),
    is_archived: bool = Query(False),
    q: str | None = Query(None, max_length=200),
    before_id: int | None = Query(None, gt=0),
):
    """Render unified inbox tabs, triage classifications/proposals, chronological thread messages, and outbox catalog."""
    evidence_rows = await snippet_rows("communication", boundary)
    inbox_threads: list[dict[str, Any]] = []
    next_before_id: int | None = None
    selected_thread_detail: dict[str, Any] | None = None
    selected_thread_triage: dict[str, Any] | None = None
    triage_by_thread: dict[int, dict[str, Any]] = {}
    triage_counts: dict[str, int] = {cat: 0 for cat in TRIAGE_CATEGORIES}
    merge_suggestions: list[dict[str, Any]] = []

    norm_triage_cat = (
        triage_category if triage_category in TRIAGE_CATEGORIES else None
    )

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

                try:
                    triage_page = await list_triaged_threads(
                        session,
                        category=norm_triage_cat,
                        channel=channel,
                        include_archived=is_archived,
                        limit=50,
                    )
                    triage_counts = triage_page.get("counts_by_category", triage_counts)
                    for item in triage_page.get("items", []):
                        tid = item.get("thread_id")
                        if isinstance(tid, int):
                            triage_by_thread[tid] = item
                except Exception:  # noqa: BLE001
                    logger.warning("Failed to list triaged threads on communications page", exc_info=True)

                if norm_triage_cat is not None:
                    inbox_threads = [
                        th for th in inbox_threads if th.get("id") in triage_by_thread
                    ]

                active_thread_id = thread_id or (inbox_threads[0]["id"] if inbox_threads else None)
                if active_thread_id is not None:
                    selected_thread_detail = await get_thread_detail(session, int(active_thread_id))
                    selected_thread_triage = triage_by_thread.get(int(active_thread_id))
                    if selected_thread_triage is None:
                        try:
                            selected_thread_triage = await triage_thread(session, int(active_thread_id))
                        except Exception:  # noqa: BLE001
                            logger.warning("Failed to triage selected thread %s", active_thread_id, exc_info=True)
                            selected_thread_triage = None
                if tab == "contacts_merge":
                    merge_suggestions = await suggest_contact_merges(session, limit=50)
        except (PermissionError, TenantContextError):
            raise HTTPException(status_code=403) from None
        except Exception:  # noqa: BLE001
            logger.warning("Failed to load communications page data", exc_info=True)

    return render(
        request,
        "web/communications.html",
        section="communications",
        evidence_rows=evidence_rows,
        boundary=boundary,
        active_tab=tab,
        channel_filter=channel or "",
        triage_category_filter=norm_triage_cat or "",
        triage_labels=TRIAGE_LABELS,
        triage_counts=triage_counts,
        triage_by_thread=triage_by_thread,
        contact_id_filter=contact_id,
        is_read_filter=is_read,
        is_archived_filter=is_archived,
        q=q or "",
        inbox_threads=inbox_threads,
        next_before_id=next_before_id,
        selected_thread_detail=selected_thread_detail,
        selected_thread_triage=selected_thread_triage,
        merge_suggestions=merge_suggestions,
        channel_badges=CHANNEL_BADGES,
    )


@router.post("/communications/threads/{thread_id}/triage")
async def update_thread_triage_decision(
    request: Request,
    thread_id: int = Path(..., gt=0),
    category: str = Form(...),
    reason: str = Form("", max_length=240),
    token: str = Form("", alias="_csrf"),
):
    """Record a user triage classification correction as our decision (`is_model_fact=False`)."""
    if not ensure_csrf(request, token):
        return JSONResponse({"ok": False, "error": "invalid_csrf"}, status_code=403)
    if not perms_can(request, "comms.message", "update"):
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)

    tenant_id = getattr(request.state, "tenant_id", None) or current_tenant_id()
    user = getattr(request.state, "web_user", None)
    _cur_user = get_current_user()
    user_id = getattr(user, "id", None) or (getattr(_cur_user, "id", None) if _cur_user is not None else None)
    if type(tenant_id) is not int or type(user_id) is not int:
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)

    norm_cat = str(category or "").strip().lower()
    if norm_cat not in TRIAGE_CATEGORIES:
        return JSONResponse({"ok": False, "error": "invalid_category"}, status_code=400)

    try:
        async with async_session_maker() as session, session.begin():
            await triage_thread(session, thread_id)
            decision = await record_user_triage_override(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                thread_id=thread_id,
                category=norm_cat,
                reason=reason or None,
            )
    except PermissionError:
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    except InboxItemUnavailable:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    accept = request.headers.get("accept", "")
    if "application/json" in accept:
        return JSONResponse({"ok": True, "decision": decision}, status_code=200)
    return RedirectResponse(url=f"/app/communications?tab=inbox&thread_id={thread_id}", status_code=303)
