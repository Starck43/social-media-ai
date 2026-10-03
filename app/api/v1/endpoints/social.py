"""Public OAuth callbacks for external platform authorization.

This router is deliberately **public**: the platform (VK) redirects the user's
browser here after they consent, so it cannot require a bearer token. Security
comes from the signed `state` (workspace + PKCE verifier), verified inside
`app.services.social.vk_oauth` before any token is exchanged.
"""

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import HTMLResponse, JSONResponse

from app.services.social.vk_oauth import build_authorize_url, complete_authorization

router = APIRouter(tags=["social"])

_SUCCESS_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>VK авторизация</title></head>
<body style="font-family:system-ui,sans-serif;text-align:center;padding:4rem">
<h2>Авторизация VK завершена</h2>
<p>Сейчас страница обновится автоматически...</p>
<script>setTimeout(function(){window.location.href="/app/sources";}, 800);</script>
</body></html>
"""

_ERROR_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>VK авторизация</title></head>
<body style="font-family:system-ui,sans-serif;text-align:center;padding:4rem">
<h2>Авторизация VK не удалась</h2>
<p style="color:#b91c1c">{error}</p>
<script>setTimeout(function(){window.close();}, 3000);</script>
</body></html>
"""


@router.get("/start")
async def vk_oauth_start(request: Request):
    """Initiate the VK OAuth (L2 user token) flow for the caller's workspace.

    Authenticated via `ApiScopeMiddleware`; returns the authorize URL for the
    caller to open in a browser. The public `/callback` completes the exchange
    and stores `vk/user_token` in the caller's personal vault.
    """
    tenant_id = getattr(request.state, "tenant_id", None)
    user_id = getattr(getattr(request.state, "api_user", None), "id", None)
    if tenant_id is None or user_id is None:
        raise HTTPException(status_code=403, detail="No active workspace")
    try:
        url = await build_authorize_url(tenant_id, user_id=user_id)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return JSONResponse({"authorize_url": url, "tenant_id": tenant_id})


@router.get("/callback", response_class=HTMLResponse)
async def vk_callback(
    code: str | None = None,
    state: str | None = None,
    device_id: str | None = None,
    error: str | None = None,
):
    """Handle the VK ID redirect for the L2 (user) token."""
    if error or not code or not state:
        msg = error or "Отсутствует code/state"
        return HTMLResponse(_ERROR_HTML.replace("{error}", msg), status_code=400)
    try:
        await complete_authorization(code, state, device_id=device_id)
    except ValueError as e:
        return HTMLResponse(_ERROR_HTML.replace("{error}", str(e)), status_code=400)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return HTMLResponse(_SUCCESS_HTML)
