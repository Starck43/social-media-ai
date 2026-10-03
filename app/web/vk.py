"""«Войти через VK ID» — web surface for the VK OAuth (L2 user token) flow.

A web user clicks the button on the sources page; this endpoint builds the VK
authorize URL for their active workspace and redirects the browser there. After
consent, VK redirects to the public callback (`/api/v1/social/callback`), which
exchanges the code and stores `vk/user_token` in the user's personal vault. The
user simply returns to the sources page.

Everything server-side already exists in `app.services.social.vk_oauth`; this
route just wires the web user + active workspace into it.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.services.social.vk_oauth import build_authorize_url

from .deps import add_flash

router = APIRouter()


@router.get("/vk/oauth")
async def vk_oauth_start(request: Request):
    """Redirect to VK to authorize the L2 (user) token for the active workspace."""
    tenant_id = getattr(request.state, "tenant_id", None)
    user_id = getattr(getattr(request.state, "web_user", None), "id", None)
    if tenant_id is None or user_id is None:
        add_flash(request, "error", "Нет активного workspace — выберите workspace и повторите")
        return RedirectResponse("/app/sources", status_code=302)

    try:
        url = await build_authorize_url(tenant_id, user_id=user_id)
    except RuntimeError as e:
        add_flash(request, "error", f"{e}. Задайте VK_APP_ID / VK_SERVICE_KEY в конфигурации")
        return RedirectResponse("/app/sources", status_code=302)
    return RedirectResponse(url, status_code=302)


@router.post("/vk/logout")
async def vk_logout(request: Request):
    """Delete the user's VK L2 user token from the personal vault."""
    user_id = getattr(getattr(request.state, "web_user", None), "id", None)
    if user_id is None:
        add_flash(request, "error", "Нет активного пользователя")
        return RedirectResponse("/app/sources", status_code=302)

    from app.models.managers.user_credential_manager import user_credentials

    rows = await user_credentials.filter(user_id=user_id, platform="vk", kind="user_token")
    for row in rows:
        await user_credentials.delete_by_id(row.id)

    rows_pending = await user_credentials.filter(user_id=user_id, platform="vk", kind="oauth_pending")
    for row in rows_pending:
        await user_credentials.delete_by_id(row.id)

    add_flash(request, "success", "Аккаунт VK отключён")
    return RedirectResponse("/app/sources", status_code=302)
