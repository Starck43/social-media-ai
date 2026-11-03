"""Browser login/recovery with exact secret-safe forms and no automatic replay."""

from __future__ import annotations

import logging
from urllib.parse import parse_qsl
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.admin.csrf import SESSION_ID_KEY, ensure_session_id, get_csrf_manager
from app.schemas.connector_account import TelegramLoginMetadata
from app.services.social.telegram_login import LoginRejected, telegram_logins

from .connector_accounts import WORKSPACE_BACK, _positive_form_integer, _private, _workspace_scope
from .deps import render

router = APIRouter(prefix="/settings/telegram-accounts/workspace/login")
BASE = f"{WORKSPACE_BACK}/login"
logger = logging.getLogger(__name__)


def _identity(session_id, tenant_id, actor_id, action):
    return f"{session_id}:telegram-login:{actor_id}:{tenant_id}:{action}"


def _start_action(key, target, revision):
    return f"start:{key}:{target or ''}:{revision or ''}"


def _page(request, *, attempt=None, target=None, revision=None, error=None, status=200, lookup_key=None):
    tokens = {}
    key = str(uuid4())
    if error is None:
        tenant_id, actor_id = _workspace_scope(request)
        session_id = ensure_session_id(request)
        actions = (
            {op: f"step:{attempt['attempt_id']}:{attempt['revision']}:{op}" for op in ("code", "password", "cancel")}
            if attempt
            else {"start": _start_action(key, target, revision)}
        )
        tokens = {
            op: get_csrf_manager(request).generate_token(session_id=_identity(session_id, tenant_id, actor_id, action))
            for op, action in actions.items()
        }
    return _private(
        render(
            request,
            "web/telegram_login.html",
            section="settings",
            status_code=status,
            attempt=attempt,
            recovery_target=target,
            recovery_revision=revision,
            login_error=error,
            login_tokens=tokens,
            request_key=key,
            lookup_key=lookup_key,
        )
    )


async def _form(request, expected):
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/x-www-form-urlencoded":
        raise ValueError
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 4096:
            raise ValueError
        body.extend(chunk)
    pairs = parse_qsl(
        body.decode("utf-8"), keep_blank_values=True, strict_parsing=True, errors="strict", max_num_fields=8
    )
    form = dict(pairs)
    if len(pairs) != len(expected) or len(form) != len(expected) or set(form) != expected:
        raise ValueError
    return form


def _verify(request, token, tenant_id, actor_id, action):
    session_id = request.session.get(SESSION_ID_KEY)
    if (
        not isinstance(session_id, str)
        or not session_id
        or not get_csrf_manager(request).verify_token(
            token, session_id=_identity(session_id, tenant_id, actor_id, action)
        )
    ):
        raise PermissionError


@router.get("")
async def telegram_login_begin(request: Request):
    try:
        tenant_id, actor_id = _workspace_scope(request)
        # Fresh CREATE rights; listing reusable metadata has no provider effect.
        from app.services.social.connector_authorizations import connector_authorizations

        _, can_attach = await connector_authorizations.list_reusable(tenant_id=tenant_id, actor_id=actor_id)
        if not can_attach:
            raise PermissionError
        target = request.query_params.get("authorization_id")
        revision = request.query_params.get("expected_revision")
        if (target is None) != (revision is None):
            raise ValueError
        if target is not None:
            target, revision = _positive_form_integer(target), _positive_form_integer(revision)
        return _page(request, target=target, revision=revision)
    except (PermissionError, ValueError):
        return _page(request, error="Вход недоступен. Проверьте пространство и права создания источников.", status=404)
    except Exception:
        logger.warning("telegram_login_page_unavailable")
        return _page(request, error="Не удалось загрузить страницу входа.", status=503)


@router.post("")
async def telegram_login_start(request: Request):
    try:
        tenant_id, actor_id = _workspace_scope(request)
        form = await _form(
            request, {"_csrf", "request_id", "phone", "authorization_id", "expected_revision", "confirm"}
        )
        key = str(UUID(form["request_id"]))
        target = _positive_form_integer(form["authorization_id"]) if form["authorization_id"] else None
        revision = _positive_form_integer(form["expected_revision"]) if form["expected_revision"] else None
        if (target is None) != (revision is None) or form["confirm"] != "begin-login":
            raise ValueError
        _verify(request, form["_csrf"], tenant_id, actor_id, _start_action(key, target, revision))
    except (ValueError, UnicodeError):
        return _page(request, error="Некорректная форма. Обновите страницу; секреты не сохранены в форме.", status=422)
    except PermissionError:
        return _page(request, error="Сессия подтверждения истекла или пространство изменилось.", status=403)
    try:
        result = TelegramLoginMetadata.model_validate(
            await telegram_logins.start(
                tenant_id=tenant_id,
                actor_id=actor_id,
                request_id=key,
                phone=form["phone"],
                authorization_id=target,
                expected_revision=revision,
            )
        )
    except LoginRejected as exc:
        return _page(
            request, error="Вход не начат: проверьте текущую попытку, настройки и лимит входов.", status=exc.status
        )
    except PermissionError:
        return _page(request, error="Авторизация недоступна или её ревизия изменилась.", status=404)
    except Exception:
        logger.warning("telegram_login_start_unconfirmed")
        # Preserve the idempotency key so a status-only rediscovery can resolve
        # an uncertain start; never silently resend with a new key.
        return _page(
            request,
            error="Результат не подтверждён. Проверьте статус без повторной отправки кода.",
            lookup_key=key,
            status=503,
        )
    return _private(RedirectResponse(f"{BASE}/{result.attempt_id}", status_code=303))


@router.get("/request/{request_id}")
async def telegram_login_request_view(request: Request, request_id: str):
    try:
        tenant_id, actor_id = _workspace_scope(request)
        key = str(UUID(request_id))
        result = TelegramLoginMetadata.model_validate(
            await telegram_logins.status_by_request(
                tenant_id=tenant_id,
                actor_id=actor_id,
                request_id=key,
            )
        )
        return _private(RedirectResponse(f"{BASE}/{result.attempt_id}", status_code=303))
    except (ValueError, PermissionError):
        return _page(request, error="Попытка не найдена. Отправка запроса провайдеру не выполнялась.", status=404)
    except Exception:
        logger.warning("telegram_login_request_status_unavailable")
        return _page(
            request, error="Статус недоступен. Повторите только проверку статуса, не отправку кода.", status=503
        )


@router.get("/{attempt_id}")
async def telegram_login_view(request: Request, attempt_id: str):
    try:
        tenant_id, actor_id = _workspace_scope(request)
        key = str(UUID(attempt_id))
        result = TelegramLoginMetadata.model_validate(
            await telegram_logins.status(
                tenant_id=tenant_id,
                actor_id=actor_id,
                attempt_id=key,
            )
        ).model_dump(mode="json")
        if result["attempt_id"] != key:
            raise ValueError
        return _page(request, attempt=result)
    except (ValueError, PermissionError):
        return _page(request, error="Попытка недоступна в этом пространстве.", status=404)
    except Exception:
        logger.warning("telegram_login_status_unavailable")
        return _page(
            request, error="Статус не подтверждён. Обновите эту страницу без повторной отправки секрета.", status=503
        )


async def _step(request, attempt_id, operation):
    try:
        tenant_id, actor_id = _workspace_scope(request)
        key = str(UUID(attempt_id))
        fields = {"_csrf", "expected_revision", "confirm"} | ({"secret"} if operation != "cancel" else set())
        form = await _form(request, fields)
        revision = _positive_form_integer(form["expected_revision"])
        if form["confirm"] != operation:
            raise ValueError
        _verify(request, form["_csrf"], tenant_id, actor_id, f"step:{key}:{revision}:{operation}")
    except (ValueError, UnicodeError):
        return _page(request, error="Некорректная форма. Обновите статус попытки.", status=422)
    except PermissionError:
        return _page(request, error="Сессия подтверждения истекла или пространство изменилось.", status=403)
    try:
        if operation == "cancel":
            # Cancel is safe/idempotent; it never calls a provider or resumes an
            # in-flight request. A late result is fenced by state/revision.
            result = await telegram_logins.cancel(tenant_id=tenant_id, actor_id=actor_id, attempt_id=key)
        else:
            result = await telegram_logins.submit(
                tenant_id=tenant_id,
                actor_id=actor_id,
                attempt_id=key,
                expected_revision=revision,
                operation=operation,
                secret=form["secret"],
            )
        metadata = TelegramLoginMetadata.model_validate(result)
        if str(metadata.attempt_id) != key:
            raise ValueError
    except LoginRejected as exc:
        return _page(
            request, error="Шаг не принят. Обновите статус; повторная отправка не выполнялась.", status=exc.status
        )
    except PermissionError:
        return _page(request, error="Попытка недоступна или права изменились.", status=404)
    except Exception:
        logger.warning("telegram_login_step_unconfirmed")
        return _page(
            request,
            error="Исход шага неизвестен. Обновите статус этой попытки; не отправляйте секрет повторно.",
            status=503,
        )
    return _private(RedirectResponse(f"{BASE}/{key}", status_code=303))


@router.post("/{attempt_id}/code")
async def telegram_login_code(request: Request, attempt_id: str):
    return await _step(request, attempt_id, "code")


@router.post("/{attempt_id}/password")
async def telegram_login_password(request: Request, attempt_id: str):
    return await _step(request, attempt_id, "password")


@router.post("/{attempt_id}/cancel")
async def telegram_login_cancel(request: Request, attempt_id: str):
    return await _step(request, attempt_id, "cancel")
