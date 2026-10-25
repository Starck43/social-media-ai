"""Authenticated personal LLM account and explicit task payer controls."""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.models.managers.personal_billing_manager import PersonalBillingError, PersonalBillingUnavailable
from app.services.billing import (
    configure_current_personal_account,
    consent_current_user_as_task_payer,
    current_personal_account,
    current_personal_spend_summary,
    unpriced_attempt_models,
    workspace_spend_summary,
    zero_price_active_models,
)

from .deps import add_flash, ensure_csrf, guard_superuser, render

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing")


def _authenticated(request: Request) -> bool:
    user = getattr(request.state, "web_user", None)
    return bool(user is not None and user.is_active and type(user.id) is int and user.id > 0)


def _account_payload(account) -> dict:
    if account is None:
        return {"configured": False, "active": False, "daily_limit_usd": None, "policy_version": None}
    return {
        "configured": account.configured_at is not None,
        "active": bool(account.is_active),
        "daily_limit_usd": str(account.daily_limit_usd) if account.daily_limit_usd is not None else None,
        "unlimited": bool(account.configured_at is not None and account.daily_limit_usd == 0),
        "policy_version": account.policy_version,
    }


def _error(status_code: int, code: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": code}, status_code=status_code, headers={"Cache-Control": "no-store"})


@router.get("/exposure")
async def billing_exposure(request: Request):
    """Operator view of unpriced model evidence and workspace spend.

    Platform-operator surface only: the same `guard_superuser` rule as the queue
    page. The workspace pin keeps the ledger read inside the selected tenant;
    the model catalog is actor-owned and read-only. A failed read is shown as
    unavailable instead of a fabricated zero.
    """
    denied = guard_superuser(request, back="/app/jobs")
    if denied is not None:
        return denied
    tenant_id = getattr(request.state, "tenant_id", None)
    if type(tenant_id) is not int or tenant_id <= 0:
        add_flash(request, "error", "Выберите пространство для просмотра финансовой экспозиции")
        return RedirectResponse("/app/jobs", status_code=302)

    spend = None
    unpriced = None
    zero_price = None
    unavailable = False
    try:
        spend = await workspace_spend_summary(tenant_id)
        unpriced = await unpriced_attempt_models(tenant_id)
        zero_price = await zero_price_active_models()
    except Exception:  # noqa: BLE001 - a read failure must not fabricate zeros
        logger.warning("billing_exposure_read_failed error_code=billing_read_unavailable")
        unavailable = True

    return render(
        request,
        "web/billing_exposure.html",
        section="jobs",
        spend=spend,
        unpriced=unpriced,
        zero_price=zero_price,
        exposure_unavailable=unavailable,
        exposure_days=7,
    )


@router.get("/account")
async def personal_billing_account(request: Request):
    """Read the signed-in user's personal account without creating it."""
    if not _authenticated(request):
        return _error(401, "authentication_required")
    try:
        account = await current_personal_account()
    except PersonalBillingError:
        return _error(403, "account_unavailable")
    payload = _account_payload(account)
    if account is not None:
        try:
            payload["spend_today"] = await current_personal_spend_summary()
        except PersonalBillingError:
            return _error(403, "account_unavailable")
        except Exception:
            return _error(503, "spend_summary_unavailable")
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


@router.post("/account/daily-limit")
async def set_personal_daily_limit(
    request: Request,
    daily_limit_usd: str = Form(...),
    token: str = Form("", alias="_csrf"),
):
    """Configure only the signed-in user's cap; zero is explicit unlimited."""
    if not ensure_csrf(request, token):
        return _error(403, "csrf_failed")
    if not _authenticated(request):
        return _error(401, "authentication_required")
    try:
        amount = Decimal(daily_limit_usd)
        account = await configure_current_personal_account(amount)
    except (InvalidOperation, ValueError):
        return _error(422, "invalid_daily_limit")
    except PersonalBillingError:
        return _error(403, "account_unavailable")
    return JSONResponse(_account_payload(account), headers={"Cache-Control": "no-store"})


@router.post("/tasks/{task_id}/payer")
async def consent_as_task_payer(request: Request, task_id: int, token: str = Form("", alias="_csrf")):
    """Record only the current user's consent to personally pay for this task."""
    if not ensure_csrf(request, token):
        return _error(403, "csrf_failed")
    if not _authenticated(request):
        return _error(401, "authentication_required")
    try:
        consent = await consent_current_user_as_task_payer(task_id)
    except PersonalBillingUnavailable:
        return _error(409, "personal_account_not_configured")
    except PersonalBillingError:
        return _error(403, "task_payer_consent_denied")
    return JSONResponse(
        {
            "ok": True,
            "task_id": consent.agent_task_id,
            "payer_user_id": consent.payer_user_id,
            "consent_version": consent.consent_version,
        },
        headers={"Cache-Control": "no-store"},
    )
