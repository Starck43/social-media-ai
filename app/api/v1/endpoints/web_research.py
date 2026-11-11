"""HTTP endpoints for web research: status, search -> fetch, and public page snapshots (P20-T12)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_model_perm
from app.core.permissions import get_current_user, has_permission
from app.services.web_research.contracts import (
    PageSnapshotOut,
    WebFetchRequest,
    WebResearchRefusal,
    WebResearchRunOut,
    WebResearchStatusOut,
    WebSearchRequest,
)
from app.services.web_research.service import web_research_service
from app.types import ActionType

router = APIRouter(
    prefix="/web-research",
    tags=["web-research"],
    dependencies=[Depends(require_model_perm("source", ActionType.VIEW))],
)


def _raise_http_for_refusal(exc: WebResearchRefusal) -> None:
    if exc.code == "permission_denied":
        raise HTTPException(status_code=403, detail=exc.user_message) from None
    if exc.code in {"budget_exhausted", "rate_limited"}:
        raise HTTPException(status_code=429, detail=exc.user_message) from None
    if exc.code == "quota_state_unavailable":
        raise HTTPException(status_code=503, detail=exc.user_message) from None
    if exc.code in {
        "invalid_url",
        "invalid_query",
        "ssrf_blocked",
        "private_address_forbidden",
        "dns_rebinding_detected",
        "redirect_blocked",
        "too_many_redirects",
        "robots_disallowed",
        "unsupported_content_type",
        "response_too_large",
        "login_or_captcha_blocked",
        "stub_cannot_be_live_evidence",
    }:
        raise HTTPException(status_code=422, detail=exc.user_message) from None
    if exc.code == "fetch_timeout":
        raise HTTPException(status_code=504, detail=exc.user_message) from None
    raise HTTPException(status_code=502, detail=exc.user_message) from None


def _ensure_save_permission(save_evidence: bool) -> None:
    if save_evidence and not has_permission(get_current_user(), "source", ActionType.CREATE):
        raise HTTPException(status_code=403, detail="Недостаточно прав для сохранения доказательств.")


@router.get("/status", response_model=WebResearchStatusOut)
async def get_web_research_status(db: AsyncSession = Depends(get_db)):
    try:
        return await web_research_service.status_async(session=db, require_durable=True)
    except WebResearchRefusal as exc:
        _raise_http_for_refusal(exc)


@router.post("/search", response_model=WebResearchRunOut)
async def run_web_research(payload: WebSearchRequest, db: AsyncSession = Depends(get_db)):
    _ensure_save_permission(payload.save_evidence)
    try:
        return await web_research_service.research(payload, session=db, require_durable=True)
    except WebResearchRefusal as exc:
        _raise_http_for_refusal(exc)
    except ValidationError:
        raise HTTPException(status_code=422, detail="Некорректные параметры запроса веб-исследования.") from None


@router.post("/fetch", response_model=PageSnapshotOut)
async def fetch_web_page(payload: WebFetchRequest, db: AsyncSession = Depends(get_db)):
    _ensure_save_permission(payload.save_evidence)
    try:
        return await web_research_service.fetch_page(payload, session=db, require_durable=True)
    except WebResearchRefusal as exc:
        _raise_http_for_refusal(exc)
    except ValidationError:
        raise HTTPException(status_code=422, detail="Некорректные параметры загрузки страницы.") from None
