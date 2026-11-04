"""Web routes for provider-neutral workspace OAuth 2.0 connections (`/app/settings/oauth`, P13-T08)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.core.permissions import set_current_user
from app.core.tenant_context import tenant_scope
from app.integrations.oauth import OAuthFlowError, workspace_oauth

from .deps import add_flash

router = APIRouter(prefix="/settings/oauth")
BACK = "/app/settings?tab=connections"


def _scope(request: Request) -> tuple[int, int]:
    user = getattr(request.state, "web_user", None)
    actor_id = getattr(user, "id", None)
    tenant_id = getattr(request.state, "tenant_id", None)
    if (
        user is None
        or not getattr(user, "is_active", False)
        or type(actor_id) is not int
        or actor_id <= 0
        or type(tenant_id) is not int
        or tenant_id <= 0
    ):
        raise OAuthFlowError("connection_denied")
    set_current_user(user)
    return tenant_id, actor_id


@router.post("/{provider_id}/start")
async def oauth_start(request: Request, provider_id: str):
    try:
        tenant_id, actor_id = _scope(request)
        with tenant_scope(tenant_id):
            started = await workspace_oauth.start_authorization(
                tenant_id=tenant_id,
                actor_id=actor_id,
                provider_id=provider_id,
            )
    except OAuthFlowError as exc:
        return JSONResponse({"error": {"code": exc.code}}, status_code=400)
    return RedirectResponse(started["authorize_url"], status_code=302)


@router.get("/{provider_id}/callback")
async def oauth_callback(request: Request, provider_id: str, state: str = "", code: str = ""):
    try:
        tenant_id, actor_id = _scope(request)
        with tenant_scope(tenant_id):
            await workspace_oauth.complete_callback(
                tenant_id=tenant_id,
                actor_id=actor_id,
                provider_id=provider_id,
                state_token=state,
                code=code,
            )
    except OAuthFlowError:
        add_flash(request, "error", "Не удалось подтвердить OAuth-авторизацию провайдера.")
        return RedirectResponse(BACK, status_code=302)
    add_flash(request, "success", "Интеграция OAuth успешно подключена.")
    return RedirectResponse(BACK, status_code=302)


@router.post("/{provider_id}/{connection_id}/disconnect")
async def oauth_disconnect(request: Request, provider_id: str, connection_id: int):
    try:
        tenant_id, actor_id = _scope(request)
        with tenant_scope(tenant_id):
            await workspace_oauth.disconnect(
                tenant_id=tenant_id,
                actor_id=actor_id,
                connection_id=connection_id,
            )
    except OAuthFlowError:
        add_flash(request, "error", "Не удалось отключить OAuth-интеграцию.")
        return RedirectResponse(BACK, status_code=302)
    add_flash(request, "success", "Интеграция OAuth отключена, секреты удалены.")
    return RedirectResponse(BACK, status_code=302)
