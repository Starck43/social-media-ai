"""Global User inbox with origin-aware, freshly authorized workspace navigation."""

from __future__ import annotations

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.models import NotificationPreference
from app.models.managers.notification_preference_manager import NotificationPreferenceUserInactive
from app.services.notifications.attention import positive_id
from app.services.notifications.delivery_signal import navigation_allowed
from app.services.notifications.inbox import PersonalInbox
from app.services.notifications.links import valid_intent
from app.services.notifications.preferences import DEFAULTS as PREFERENCE_DEFAULTS
from app.services.notifications.preferences import validate as validate_preferences

from .deps import ensure_csrf, render

router = APIRouter(prefix="/notifications")


def _unavailable(status=409):
    return JSONResponse({"ok": False, "reset": True}, status_code=status, headers={"Cache-Control": "no-store"})


def _active_user(request: Request):
    user = getattr(request.state, "web_user", None)
    if user is None or not user.is_active or not positive_id(user.id):
        return None
    expected = request.headers.get("X-App-User")
    if expected is not None and expected != str(user.id):
        return None
    return user


def _preferences_response(user_id: int, value: dict, status_code: int = 200):
    return JSONResponse(
        {"user_id": user_id, "preferences": value},
        status_code=status_code,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/preferences")
async def notification_preferences_get(request: Request):
    user = _active_user(request)
    if user is None:
        return _unavailable(401)
    try:
        value = await NotificationPreference.objects.get_for_user(user.id)
    except NotificationPreferenceUserInactive:
        return _unavailable(401)
    except Exception:
        return _unavailable(503)
    return _preferences_response(user.id, value or PREFERENCE_DEFAULTS)


@router.post("/preferences")
async def notification_preferences_save(request: Request):
    user = _active_user(request)
    if user is None:
        return _unavailable(401)
    try:
        body = await request.json()
    except Exception:
        body = None
    token = body.get("_csrf") if isinstance(body, dict) else None
    if (
        not isinstance(body, dict)
        or set(body) != {"_csrf", "preferences"}
        or not isinstance(token, str)
        or not ensure_csrf(request, token)
    ):
        return _unavailable(403)
    value = validate_preferences(body.get("preferences"))
    if value is None:
        return JSONResponse(
            {"ok": False, "error": "invalid_preferences"}, status_code=422, headers={"Cache-Control": "no-store"}
        )
    try:
        stored = await NotificationPreference.objects.save_for_user(user.id, value)
    except NotificationPreferenceUserInactive:
        return _unavailable(401)
    except Exception:
        return _unavailable(503)
    return _preferences_response(user.id, stored)


@router.post("/preferences/reset")
async def notification_preferences_reset(request: Request):
    user = _active_user(request)
    if user is None:
        return _unavailable(401)
    try:
        body = await request.json()
    except Exception:
        body = None
    token = body.get("_csrf") if isinstance(body, dict) else None
    if (
        not isinstance(body, dict)
        or set(body) != {"_csrf"}
        or not isinstance(token, str)
        or not ensure_csrf(request, token)
    ):
        return _unavailable(403)
    try:
        stored = await NotificationPreference.objects.save_for_user(user.id, PREFERENCE_DEFAULTS)
    except NotificationPreferenceUserInactive:
        return _unavailable(401)
    except Exception:
        return _unavailable(503)
    return _preferences_response(user.id, stored)


@router.get("")
@router.get("/")
async def notifications_list(request: Request, limit: int = Query(30, ge=1, le=100)):
    user = _active_user(request)
    if user is None:
        return _unavailable(401)
    try:
        data = await PersonalInbox(user).snapshot(getattr(request.state, "tenant_id", None), limit)
    except Exception:
        return _unavailable(503)
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


@router.get("/inbox")
async def notification_inbox_page(request: Request, page: int = Query(1, ge=1)):
    user = _active_user(request)
    if user is None:
        return _unavailable(401)
    entries = await PersonalInbox(user).entries(limit=50, offset=(page - 1) * 50)
    response = render(request, "web/notification_inbox.html", entries=entries, page=page)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/read-all")
async def notifications_read_all(request: Request, token: str = Form("", alias="_csrf"), workspace_id: str = Form("")):
    user = _active_user(request)
    if user is None or not ensure_csrf(request, token):
        return _unavailable(403)
    count = await PersonalInbox(user).read_window()
    return JSONResponse(
        {
            "marked": count,
            "windowed": True,
            "context": {"user_id": user.id, "workspace_id": getattr(request.state, "tenant_id", None)},
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/read/{notification_id}")
async def notification_read_one(
    request: Request, notification_id: int, token: str = Form("", alias="_csrf"), workspace_id: str = Form("")
):
    user = _active_user(request)
    if user is None or not ensure_csrf(request, token):
        return _unavailable(403)
    ok = positive_id(notification_id) and await PersonalInbox(user).read(notification_id)
    if "text/html" in request.headers.get("accept", "") and ok:
        return RedirectResponse("/app/notifications/inbox", status_code=303)
    return JSONResponse(
        {"ok": bool(ok), "context": {"user_id": user.id, "workspace_id": getattr(request.state, "tenant_id", None)}},
        headers={"Cache-Control": "no-store"},
    )


@router.get("/open/{notification_id}")
async def notification_open_landing(request: Request, notification_id: int, intent: str | None = None):
    """Only a valid user-bound navigation intent permits one-click context switching.

    Unsigned links require the normal CSRF POST. Receipt and action state never
    change on GET; the signed intent authorizes only the browser's navigation.
    """
    user = _active_user(request)
    if user is None:
        return _unavailable(401)
    entries = await PersonalInbox(user).entries(notification_id=notification_id)
    if not entries:
        return _unavailable(404)
    if intent is not None:
        if not valid_intent(intent, notification_id, user.id):
            return _unavailable(403)
        entry = entries[0]
        request.session["app_tenant_id"] = entry.event.tenant_id
        return RedirectResponse(
            entry.destination, status_code=303, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
        )
    response = render(request, "web/notification_open.html", entry=entries[0])
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/open/{notification_id}")
async def notification_open(request: Request, notification_id: int, token: str = Form("", alias="_csrf")):
    user = _active_user(request)
    if user is None or not ensure_csrf(request, token):
        return _unavailable(403)
    # New service instance: membership, grant, receipt ownership, expiry and target
    # are re-read on this request. No posted workspace ID or redirect is trusted.
    entries = await PersonalInbox(user).entries(notification_id=notification_id)
    if not entries:
        return _unavailable(404)
    entry = entries[0]
    request.session["app_tenant_id"] = entry.event.tenant_id
    return RedirectResponse(entry.destination, status_code=303, headers={"Cache-Control": "no-store"})


DELIVERY_DESTINATION = "/app/digests#delivery"


@router.get("/delivery/{workspace_id}")
async def delivery_signal_landing(request: Request, workspace_id: int):
    """Bell link for the read-time delivery signal; GET never switches workspace."""
    user = _active_user(request)
    if user is None:
        return _unavailable(401)
    if not await navigation_allowed(user, workspace_id):
        return _unavailable(404)
    if getattr(request.state, "tenant_id", None) == workspace_id:
        return RedirectResponse(DELIVERY_DESTINATION, status_code=303, headers={"Cache-Control": "no-store"})
    response = render(request, "web/delivery_signal_open.html", workspace_id=workspace_id)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/delivery/{workspace_id}")
async def delivery_signal_open(request: Request, workspace_id: int, token: str = Form("", alias="_csrf")):
    user = _active_user(request)
    if user is None or not ensure_csrf(request, token):
        return _unavailable(403)
    # Membership and grant are re-read on this request; the path ID alone is not trusted.
    if not await navigation_allowed(user, workspace_id):
        return _unavailable(404)
    request.session["app_tenant_id"] = workspace_id
    return RedirectResponse(DELIVERY_DESTINATION, status_code=303, headers={"Cache-Control": "no-store"})
