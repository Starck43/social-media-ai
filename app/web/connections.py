"""Connecting and disconnecting personal platform accounts (`/app/settings?tab=connections`).

One route per platform, driven by the registry in
`app.services.social.connections`: adding a platform with an OAuth flow is a
registry row plus a builder here, not another page. A `manual` platform has no
authorize endpoint at all — the user pastes a secret through the settings form.

Both endpoints keep the caller's *own* vault (`user_credentials` is keyed by
`users.id`), so there is no permission gate: a user may always manage their own
credentials, never someone else's. The old `/app/vk/*` routes stay as thin
aliases so bookmarks and the API flow keep working.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.services.social.connections import CONNECTIONS_BY_PLATFORM

from .deps import add_flash, safe_next

router = APIRouter()

BACK = "/app/settings?tab=connections"


@router.get("/connections/{platform}/authorize")
async def connection_authorize(request: Request, platform: str, next: str = ""):
    """Send the user to the platform to authorize personal access.

    Only OAuth platforms have somewhere to go. A `manual` one is redirected
    back with an explanation rather than 404 — the settings page offers a
    "connect" affordance for every platform it knows, and a dead link there is
    exactly the kind of thing that makes a UI feel unfinished.
    """
    back = safe_next(next) or BACK

    spec = CONNECTIONS_BY_PLATFORM.get(platform)
    if spec is None:
        add_flash(request, "error", f"Неизвестная платформа: {platform}")
        return RedirectResponse(back, status_code=302)
    if not spec.is_oauth:
        add_flash(request, "info", f"Для {spec.title} ключ вводится вручную — заполните поле ниже")
        return RedirectResponse(back, status_code=302)

    tenant_id = getattr(request.state, "tenant_id", None)
    user_id = getattr(getattr(request.state, "web_user", None), "id", None)
    if tenant_id is None or user_id is None:
        add_flash(request, "error", "Нет активного workspace — выберите workspace и повторите")
        return RedirectResponse(BACK, status_code=302)

    if platform == "vk":
        from app.services.social.vk_oauth import build_authorize_url

        try:
            url = await build_authorize_url(tenant_id, user_id=user_id)
        except RuntimeError as e:
            add_flash(request, "error", f"{e}. Задайте VK_APP_ID / VK_CLIENT_ACCESS_KEY в конфигурации")
            return RedirectResponse(BACK, status_code=302)
        return RedirectResponse(url, status_code=302)

    add_flash(request, "error", f"Авторизация {spec.title} пока не настроена")
    return RedirectResponse(BACK, status_code=302)


@router.post("/connections/{platform}/disconnect")
async def connection_disconnect(request: Request, platform: str):
    """Delete the caller's own row for this platform, plus any pending states.

    Scoped by `user_id` in the predicate on purpose: a crafted row id must not
    be enough to disconnect somebody else's account.
    """
    user_id = getattr(getattr(request.state, "web_user", None), "id", None)
    if user_id is None:
        add_flash(request, "error", "Только для вошедшего пользователя")
        return RedirectResponse(BACK, status_code=302)

    spec = CONNECTIONS_BY_PLATFORM.get(platform)
    if spec is None:
        add_flash(request, "error", f"Неизвестная платформа: {platform}")
        return RedirectResponse(BACK, status_code=302)

    from app.models.managers.user_credential_manager import user_credentials

    removed = 0
    for row in await user_credentials.filter(user_id=user_id, platform=platform, kind=spec.kind):
        await user_credentials.delete_by_id(row.id)
        removed += 1
    # Abandoned authorization attempts would otherwise linger in the vault.
    for row in await user_credentials.filter(user_id=user_id, platform=platform, kind="oauth_pending"):
        await user_credentials.delete_by_id(row.id)

    if removed:
        add_flash(request, "success", f"{spec.title}: личный доступ отключён")
    else:
        add_flash(request, "info", f"{spec.title}: и так не был подключён")
    return RedirectResponse(BACK, status_code=302)
