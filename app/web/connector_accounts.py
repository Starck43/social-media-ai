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


# Workspace consent deliberately uses a separate route and CSRF namespace from
# account-wide revocation. The active workspace comes only from middleware.
WORKSPACE_BACK = "/app/settings/telegram-accounts/workspace"


def _workspace_identity(session_id: str, actor_id: int, tenant_id: int) -> str:
    return f"{session_id}:workspace-connectors:{actor_id}:{tenant_id}"


def _workspace_scope(request):
    from app.core.tenant_context import current_tenant_id, is_bypass

    actor_id = _actor_id(request)
    tenant_id = getattr(request.state, "tenant_id", None)
    if (
        actor_id is None
        or type(tenant_id) is not int
        or tenant_id <= 0
        or current_tenant_id() != tenant_id
        or is_bypass()
    ):
        raise ConnectionDenied()
    return tenant_id, actor_id


def _workspace_page(
    request, *, accounts=(), grants=(), sources=(), next_after=None, can_attach=False, error=None, status=200
):
    try:
        tenant_id, actor_id = _workspace_scope(request)
        token = (
            get_csrf_manager(request).generate_token(
                session_id=_workspace_identity(ensure_session_id(request), actor_id, tenant_id)
            )
            if error is None
            else None
        )
    except ConnectionDenied:
        token = None
    return _private(
        render(
            request,
            "web/telegram_workspace_grants.html",
            status_code=status,
            section="settings",
            accounts=accounts,
            grants=grants,
            can_attach=can_attach,
            grant_error=error,
            grant_csrf=token,
            telegram_sources=sources,
            sources_next_after=next_after,
        )
    )


async def _workspace_form(request, *, attach):
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
    expected = {"_csrf", "confirm"} | ({"expected_revision"} if attach else set())
    if len(pairs) != len(expected) or len(form) != len(expected) or set(form) != expected:
        raise ValueError
    return form


@router.get("/workspace")
async def workspace_telegram_grants(request: Request):
    from app.schemas.connector_account import TelegramSourceCatalog, WorkspaceGrantMetadata
    from app.services.social.telegram_source_bindings import telegram_source_bindings

    try:
        tenant_id, actor_id = _workspace_scope(request)
        rows, can_attach = await connector_authorizations.list_reusable(tenant_id=tenant_id, actor_id=actor_id)
        grant_rows = await connector_authorizations.list_grants(tenant_id=tenant_id, actor_id=actor_id)
        accounts = [ConnectorAccountMetadata.model_validate(row).model_dump(mode="json") for row in rows]
        grants = [WorkspaceGrantMetadata.model_validate(row).model_dump(mode="json") for row in grant_rows]
        after_raw = request.query_params.get("after_id")
        after_id = _positive_form_integer(after_raw) if after_raw is not None else 0
        catalog = TelegramSourceCatalog.model_validate(
            await telegram_source_bindings.list_sources(tenant_id=tenant_id, actor_id=actor_id, after_id=after_id)
        ).model_dump(mode="json")
        if type(can_attach) is not bool or any(
            row["provider_id"] != "telegram" or row["auth_method"] != "mtproto" or row["state"] != "active"
            for row in accounts
        ):
            raise ValueError
    except ConnectionDenied:
        return _workspace_page(request, status=404, error="Разрешения недоступны. Обновите страницу.")
    except Exception:  # noqa: BLE001 - never expose internal projection or an apparently empty list
        logger.warning("workspace_connector_list_unavailable")
        return _workspace_page(request, status=503, error="Не удалось загрузить разрешения. Попробуйте позже.")
    return _workspace_page(
        request,
        accounts=accounts,
        grants=grants,
        can_attach=can_attach,
        sources=catalog["sources"],
        next_after=catalog["next_after"],
    )


async def _change_workspace_grant(request: Request, selection_id: str, *, attach: bool):
    from app.schemas.connector_account import WorkspaceGrantMetadata

    try:
        tenant_id, actor_id = _workspace_scope(request)
    except ConnectionDenied:
        return _workspace_page(request, status=404, error="Разрешения недоступны. Обновите страницу.")
    try:
        form = await _workspace_form(request, attach=attach)
    except (ValueError, UnicodeError):
        return _workspace_page(request, status=422, error="Некорректная форма. Обновите страницу.")
    session_id = request.session.get(SESSION_ID_KEY)
    if (
        not isinstance(session_id, str)
        or not session_id
        or not get_csrf_manager(request).verify_token(
            form["_csrf"], session_id=_workspace_identity(session_id, actor_id, tenant_id)
        )
    ):
        return _workspace_page(request, status=403, error="Сессия подтверждения истекла или пространство изменилось.")
    try:
        selected_id = _positive_form_integer(selection_id)
        if form["confirm"] != ("attach-workspace" if attach else "detach-workspace"):
            raise ValueError
        revision = _positive_form_integer(form["expected_revision"]) if attach else None
    except ValueError:
        return _workspace_page(request, status=422, error="Подтвердите действие и обновите страницу.")
    try:
        if attach:
            row = await connector_authorizations.attach(
                tenant_id=tenant_id, actor_id=actor_id, authorization_id=selected_id, expected_revision=revision
            )
        else:
            row = await connector_authorizations.detach(tenant_id=tenant_id, actor_id=actor_id, grant_id=selected_id)
        result = WorkspaceGrantMetadata.model_validate(row)
        if (
            attach
            and (
                result.authorization_id != selected_id
                or result.authorization_revision != revision
                or not result.is_active
            )
        ) or (not attach and (result.grant_id != selected_id or result.is_active)):
            raise ValueError
    except ConnectionDenied:
        return _workspace_page(request, status=404, error="Выбор недоступен или изменился. Обновите страницу.")
    except Exception:  # noqa: BLE001 - commit outcome can be unknown
        logger.warning("workspace_connector_change_unconfirmed")
        return _workspace_page(request, status=503, error="Результат не подтверждён. Обновите список перед повтором.")
    add_flash(request, "success", "Локальное разрешение этого пространства изменено. Сессия Telegram не завершалась.")
    return _private(RedirectResponse(WORKSPACE_BACK, status_code=303))


@router.post("/workspace/accounts/{authorization_id}/attach")
async def attach_workspace_telegram(request: Request, authorization_id: str):
    return await _change_workspace_grant(request, authorization_id, attach=True)


@router.post("/workspace/grants/{grant_id}/detach")
async def detach_workspace_telegram(request: Request, grant_id: str):
    return await _change_workspace_grant(request, grant_id, attach=False)


def _source_csrf_identity(session_id, actor_id, tenant_id, source_id):
    return f"{session_id}:source-connectors:{actor_id}:{tenant_id}:{source_id}"


def _source_page(request, *, picker=None, error=None, status=200):
    from app.core.config import settings

    token = None
    if picker is not None and error is None:
        tenant_id, actor_id = _workspace_scope(request)
        token = get_csrf_manager(request).generate_token(
            session_id=_source_csrf_identity(
                ensure_session_id(request), actor_id, tenant_id, picker["source"]["source_id"]
            )
        )
    return _private(
        render(
            request,
            "web/telegram_source_account.html",
            status_code=status,
            section="settings",
            picker=picker,
            source_error=error,
            source_csrf=token,
            runtime_enabled=settings.TELEGRAM_ACCOUNT_RUNTIME_ENABLED,
        )
    )


@router.get("/workspace/sources/{source_id}")
async def telegram_source_account(request: Request, source_id: str):
    from app.schemas.connector_account import TelegramSourcePicker
    from app.services.social.telegram_source_bindings import telegram_source_bindings

    try:
        tenant_id, actor_id = _workspace_scope(request)
        selected_id = _positive_form_integer(source_id)
        picker = TelegramSourcePicker.model_validate(
            await telegram_source_bindings.get(tenant_id=tenant_id, actor_id=actor_id, source_id=selected_id)
        ).model_dump(mode="json")
        if picker["source"]["source_id"] != selected_id:
            raise ValueError
    except (ConnectionDenied, ValueError):
        return _source_page(request, status=404, error="Источник недоступен. Обновите список пространства.")
    except Exception:
        logger.warning("telegram_source_picker_unavailable")
        return _source_page(request, status=503, error="Не удалось загрузить выбор. Попробуйте позже.")
    return _source_page(request, picker=picker)


async def _source_form(request, *, clear):
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/x-www-form-urlencoded":
        raise ValueError
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_FORM_BYTES:
            raise ValueError
        body.extend(chunk)
    pairs = parse_qsl(
        body.decode("utf-8"), keep_blank_values=True, strict_parsing=True, errors="strict", max_num_fields=4
    )
    expected = {"_csrf", "expected_binding", "confirm"} | (set() if clear else {"grant_choice"})
    form = dict(pairs)
    if len(pairs) != len(expected) or len(form) != len(expected) or set(form) != expected:
        raise ValueError
    return form


async def _change_source_account(request, source_id, *, clear):
    from app.schemas.connector_account import TelegramSourceMetadata
    from app.services.social.telegram_source_bindings import telegram_source_bindings

    try:
        tenant_id, actor_id = _workspace_scope(request)
        selected_id = _positive_form_integer(source_id)
    except (ConnectionDenied, ValueError):
        return _source_page(request, status=404, error="Источник недоступен. Обновите список.")
    try:
        form = await _source_form(request, clear=clear)
    except (ValueError, UnicodeError):
        return _source_page(request, status=422, error="Некорректная форма. Обновите страницу.")
    session_id = request.session.get(SESSION_ID_KEY)
    if (
        not isinstance(session_id, str)
        or not session_id
        or not get_csrf_manager(request).verify_token(
            form["_csrf"], session_id=_source_csrf_identity(session_id, actor_id, tenant_id, selected_id)
        )
    ):
        return _source_page(request, status=403, error="Сессия подтверждения истекла или пространство изменилось.")
    try:
        if not re.fullmatch("[0-9a-f]{64}", form["expected_binding"]):
            raise ValueError
        if form["confirm"] != ("clear-source" if clear else "bind-source"):
            raise ValueError
        selection = {}
        if not clear:
            pieces = form["grant_choice"].split(":")
            if len(pieces) != 3:
                raise ValueError
            selection = dict(
                zip(
                    ("grant_id", "authorization_id", "authorization_revision"),
                    map(_positive_form_integer, pieces),
                )
            )
    except ValueError:
        return _source_page(request, status=422, error="Выберите аккаунт и подтвердите действие.")
    try:
        changed = TelegramSourceMetadata.model_validate(
            await telegram_source_bindings.set(
                tenant_id=tenant_id,
                actor_id=actor_id,
                source_id=selected_id,
                expected_binding=form["expected_binding"],
                **selection,
            )
        )
        if changed.source_id != selected_id or changed.selection_state != ("unselected" if clear else "selected"):
            raise ValueError
    except ConnectionDenied:
        return _source_page(request, status=404, error="Выбор недоступен или изменился. Обновите страницу.")
    except Exception:
        logger.warning("telegram_source_binding_unconfirmed")
        return _source_page(
            request, status=503, error="Результат не подтверждён. Обновите страницу перед повторным действием."
        )
    add_flash(
        request,
        "success",
        "Выбор источника изменён локально. Создайте новое задание: ожидавшие задания не переходят на другой аккаунт.",
    )
    return _private(RedirectResponse(f"{WORKSPACE_BACK}/sources/{selected_id}", status_code=303))


@router.post("/workspace/sources/{source_id}/bind")
async def bind_telegram_source_account(request: Request, source_id: str):
    return await _change_source_account(request, source_id, clear=False)


@router.post("/workspace/sources/{source_id}/clear")
async def clear_telegram_source_account(request: Request, source_id: str):
    return await _change_source_account(request, source_id, clear=True)
