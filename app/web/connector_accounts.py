"""Personal Telegram metadata/local revoke, never provider login or workspace consent."""

from __future__ import annotations

import logging
import re
from urllib.parse import parse_qsl

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.admin.csrf import SESSION_ID_KEY, ensure_session_id, get_csrf_manager
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.schemas.connector_account import ConnectorAccountMetadata
from app.services.social.connector_authorizations import connector_authorizations

from .deps import add_flash, render

router = APIRouter(prefix="/settings/telegram-accounts")
logger = logging.getLogger(__name__)
BACK = "/app/settings/telegram-accounts"
MAX_FORM_BYTES = 4096


def _actor_id(request: Request) -> int | None:
    user = getattr(request.state, "web_user", None)
    value = getattr(user, "id", None)
    return value if getattr(user, "is_active", False) and type(value) is int and value > 0 else None


def _csrf_identity(session_id: str, actor_id: int) -> str:
    # Bind this destructive form to both the session and the current identity.
    # Existing unbound web tokens and admin tokens cannot authorize this action.
    return f"{session_id}:personal-connectors:{actor_id}"


def _private(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _page(request, *, accounts=(), error=None, status=200):
    actor_id = _actor_id(request)
    token = (
        get_csrf_manager(request).generate_token(session_id=_csrf_identity(ensure_session_id(request), actor_id))
        if actor_id is not None and error is None
        else None
    )
    return _private(
        render(
            request,
            "web/telegram_accounts.html",
            status_code=status,
            section="settings",
            accounts=accounts,
            account_error=error,
            account_csrf=token,
        )
    )


def _positive_form_integer(raw):
    if not isinstance(raw, str) or not re.fullmatch(r"[1-9][0-9]{0,17}", raw):
        raise ValueError
    return int(raw)


async def _selection(request):
    # Deliberately accept only a small ordinary HTML form, no files/JSON,
    # duplicate values or silently ignored owner/workspace/session fields.
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/x-www-form-urlencoded":
        raise ValueError
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_FORM_BYTES:
            raise ValueError
        body.extend(chunk)
    pairs = parse_qsl(
        body.decode("utf-8"), keep_blank_values=True, strict_parsing=True, errors="strict", max_num_fields=3
    )
    form = dict(pairs)
    if len(pairs) != 3 or len(form) != 3 or set(form) != {"_csrf", "expected_revision", "confirm"}:
        raise ValueError
    return form


@router.get("")
async def personal_telegram_accounts(request: Request):
    actor_id = _actor_id(request)
    if actor_id is None:
        return _page(request, status=401, error="Для просмотра войдите в аккаунт.")
    try:
        rows = await connector_authorizations.list_personal(actor_id=actor_id)
        accounts = [ConnectorAccountMetadata.model_validate(row).model_dump(mode="json") for row in rows]
        if any(row["provider_id"] != "telegram" or row["auth_method"] != "mtproto" for row in accounts):
            raise ValueError
    except ConnectionDenied:
        return _page(request, status=404, error="Личные авторизации недоступны. Обновите страницу.")
    except Exception:  # noqa: BLE001 - never echo database/validation input or show a false empty state
        logger.warning("personal_connector_list_unavailable")
        return _page(request, status=503, error="Не удалось загрузить авторизации. Попробуйте обновить страницу позже.")
    return _page(request, accounts=accounts)


@router.post("/{authorization_id}/revoke")
async def revoke_personal_telegram_account(request: Request, authorization_id: str):
    actor_id = _actor_id(request)
    if actor_id is None:
        return _page(request, status=401, error="Для изменения войдите в аккаунт.")
    try:
        form = await _selection(request)
    except (ValueError, UnicodeError):
        return _page(request, status=422, error="Некорректная форма. Обновите страницу и повторите выбор.")
    session_id = request.session.get(SESSION_ID_KEY)
    if (
        not isinstance(session_id, str)
        or not session_id
        or not get_csrf_manager(request).verify_token(form["_csrf"], session_id=_csrf_identity(session_id, actor_id))
    ):
        return _page(request, status=403, error="Сессия подтверждения истекла. Обновите страницу.")
    try:
        selected_id = _positive_form_integer(authorization_id)
        revision = _positive_form_integer(form["expected_revision"])
        if form["confirm"] != "revoke-local":
            raise ValueError
    except ValueError:
        return _page(request, status=422, error="Подтвердите локальный отзыв выбранной авторизации.")
    try:
        row = await connector_authorizations.revoke_personal(
            actor_id=actor_id,
            authorization_id=selected_id,
            expected_revision=revision,
        )
        metadata = ConnectorAccountMetadata.model_validate(row)
        if metadata.authorization_id != selected_id or metadata.state != "revoked":
            raise ValueError
    except ConnectionDenied:
        # Missing/foreign/stale selections stay indistinguishable.
        return _page(request, status=404, error="Выбранная авторизация недоступна или изменилась. Обновите страницу.")
    except Exception:  # noqa: BLE001 - transaction outcome may be unknown; do not claim rollback or retry
        logger.warning("personal_connector_revoke_unconfirmed")
        return _page(
            request,
            status=503,
            error="Не удалось подтвердить результат отзыва. Обновите список перед повторным действием.",
        )
    add_flash(
        request,
        "success",
        "Локальная авторизация отозвана, её разрешения пространств отключены. Telegram-сессия не завершалась.",
    )
    return _private(RedirectResponse(BACK, status_code=303))
