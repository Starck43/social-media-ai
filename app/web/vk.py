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
        url = build_authorize_url(tenant_id, user_id=user_id)
    except RuntimeError as e:
        add_flash(request, "error", f"{e}. Задайте VK_APP_ID / VK_CLIENT_ACCESS_KEY в конфигурации")
        return RedirectResponse("/app/sources", status_code=302)
    return RedirectResponse(url, status_code=302)
