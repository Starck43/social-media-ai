"""Web UI surface for web research and public site analysis under `/app/knowledge/research` (P20-T12)."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Query, Request

from app.services.web_research.contracts import RESEARCH_PRESETS, WebResearchRefusal
from app.services.web_research.service import web_research_service
from app.types import ActionType
from app.web.deps import add_flash, ensure_csrf, guard_web, perms_can, render

router = APIRouter(prefix="/knowledge/research")


def _require_research_view(request: Request) -> int:
    user = getattr(request.state, "web_user", None)
    tenant_id = getattr(request.state, "tenant_id", None)
    if (
        user is None
        or type(tenant_id) is not int
        or tenant_id <= 0
        or not (perms_can(request, "aianalytics", "view") or perms_can(request, "source", "view"))
    ):
        raise HTTPException(status_code=403, detail="Веб-исследование недоступно в текущем пространстве")
    return tenant_id


@router.get("")
async def web_research_page(
    request: Request,
    q: str = Query("", max_length=400),
    preset: str = Query("general_search", max_length=40),
    url: str = Query("", max_length=2000),
):
    tenant_id = _require_research_view(request)
    active_preset = preset if preset in RESEARCH_PRESETS else "general_search"
    status = web_research_service.status(tenant_id=tenant_id)
    saved_snapshots = web_research_service.list_saved_snapshots(tenant_id=tenant_id)
    return render(
        request,
        "web/web_research.html",
        section="knowledge",
        status=status,
        presets=list(RESEARCH_PRESETS.values()),
        active_preset=active_preset,
        query=q,
        target_url=url,
        run_result=None,
        saved_snapshots=saved_snapshots,
        can_save_evidence=perms_can(request, "source", "create"),
    )


@router.post("")
async def web_research_submit(
    request: Request,
    csrf_token: str = Form(""),
    query: str = Form("", max_length=400),
    preset: str = Form("general_search", max_length=40),
    target_url: str = Form("", max_length=2000),
    save_evidence: bool = Form(False),
):
    tenant_id = _require_research_view(request)
    if not ensure_csrf(request, csrf_token):
        raise HTTPException(status_code=403, detail="Недействительный CSRF-токен")

    if save_evidence:
        denied = guard_web(request, "source", ActionType.CREATE, back="/app/knowledge/research")
        if denied is not None:
            return denied

    active_preset = preset if preset in RESEARCH_PRESETS else "general_search"
    clean_url = (target_url or "").strip()
    clean_query = (query or "").strip() or clean_url

    run_result = None
    try:
        run_result = await web_research_service.research(
            {
                "query": clean_query,
                "preset": active_preset,
                "target_urls": [clean_url] if clean_url else [],
                "save_evidence": bool(save_evidence and clean_url),
            },
            tenant_id=tenant_id,
        )
    except WebResearchRefusal as exc:
        add_flash(request, "error", exc.user_message)
    except ValueError:
        add_flash(request, "error", "Укажите корректный поисковый запрос или публичный URL.")

    status = web_research_service.status(tenant_id=tenant_id)
    saved_snapshots = web_research_service.list_saved_snapshots(tenant_id=tenant_id)
    return render(
        request,
        "web/web_research.html",
        section="knowledge",
        status=status,
        presets=list(RESEARCH_PRESETS.values()),
        active_preset=active_preset,
        query=clean_query,
        target_url=clean_url,
        run_result=run_result,
        saved_snapshots=saved_snapshots,
        can_save_evidence=perms_can(request, "source", "create"),
    )
