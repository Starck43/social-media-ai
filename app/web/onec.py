"""Workspace 1C settings. No submitted secret is displayed, logged, or put in a URL."""

from urllib.parse import parse_qsl

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.integrations.onec.connections import OneCConnectionError, OneCConnectionService
from app.integrations.onec.credentials import OneCCredentialError
from app.integrations.onec.endpoint import OneCEndpointError
from app.integrations.onec.health import OneCHealthService
from app.models.managers.trusted_outbound_host_manager import TrustedHostError
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.models.managers.workspace_outbound_host_manager import WorkspaceHostError, WorkspaceOutboundHostManager

from .deps import add_flash, ensure_csrf, render
from .settings import _personal_access

router = APIRouter(prefix="/settings/onec")
BACK = "/app/settings/onec"
MAX_FORM_BYTES = 65536


def _scope(request):
    return {"tenant_id": getattr(request.state, "tenant_id", None), "actor_id": request.state.web_user.id}


def _private(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


async def _form(request, expected):
    if request.headers.get("content-type", "").split(";", 1)[0].lower().strip() != "application/x-www-form-urlencoded":
        raise ValueError
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > MAX_FORM_BYTES:
            raise ValueError
        content.extend(chunk)
    pairs = parse_qsl(
        content.decode("utf-8"), keep_blank_values=True, strict_parsing=True, max_num_fields=len(expected)
    )
    form = dict(pairs)
    if len(pairs) != len(expected) or set(form) != expected:
        raise ValueError
    return form


async def _validated(request, expected):
    if not _personal_access(request):
        return None
    try:
        form = await _form(request, expected | {"_csrf"})
    except (ValueError, UnicodeError):
        return None
    if not ensure_csrf(request, form["_csrf"]):
        return None
    return form


@router.get("")
async def page(request: Request):
    if not _personal_access(request):
        return _private(HTMLResponse("Недостаточно прав", status_code=403))
    try:
        rows = await OneCConnectionService().list_for_actor(**_scope(request))
    except ConnectionDenied:
        return _private(HTMLResponse("Подключения недоступны", status_code=403))
    try:
        hosts = await WorkspaceOutboundHostManager().list_hosts(**_scope(request))
        can_manage_hosts = True
    except ConnectionDenied:
        hosts, can_manage_hosts = [], False
    return _private(
        render(request, "web/onec.html", section="settings", rows=rows, hosts=hosts, can_manage_hosts=can_manage_hosts)
    )


@router.post("/connect")
async def connect(request: Request):
    form = await _validated(request, {"label", "base_url", "interface", "login", "password"})
    if form is None:
        return _private(HTMLResponse("Некорректная форма или сессия истекла", status_code=403))
    try:
        await OneCConnectionService().create(
            **_scope(request), **{key: form[key] for key in ("label", "base_url", "interface", "login", "password")}
        )
    except ConnectionDenied:
        return _private(HTMLResponse("Подключение недоступно", status_code=403))
    except (OneCConnectionError, OneCCredentialError, OneCEndpointError, TrustedHostError):
        add_flash(request, "error", "Проверьте поля и разрешение хоста. Подключение могло уже существовать.")
    else:
        add_flash(request, "success", "Подключение сохранено, доступ ещё не проверен.")
    return _private(RedirectResponse(BACK, status_code=303))


@router.post("/connections/{connection_id}/credentials")
async def replace_credentials(request: Request, connection_id: int):
    form = await _validated(request, {"login", "password"})
    if form is None:
        return _private(HTMLResponse("Некорректная форма или сессия истекла", status_code=403))
    try:
        await OneCConnectionService().replace_credentials(
            **_scope(request), connection_id=connection_id, login=form["login"], password=form["password"]
        )
    except ConnectionDenied:
        return _private(HTMLResponse("Подключение недоступно", status_code=403))
    except (OneCConnectionError, OneCCredentialError, TrustedHostError):
        add_flash(
            request, "error", "Проверьте учётные данные и разрешение хоста; такое подключение могло уже существовать."
        )
    else:
        add_flash(request, "success", "Данные сохранены; подключение включено, доступ ещё не проверен.")
    return _private(RedirectResponse(BACK, status_code=303))


@router.post("/connections/{connection_id}/{operation}")
async def action(request: Request, connection_id: int, operation: str):
    form = await _validated(request, set())
    if form is None:
        return _private(HTMLResponse("Некорректная форма или сессия истекла", status_code=403))
    if connection_id <= 0 or operation not in {"health", "disable"}:
        return _private(HTMLResponse("Действие недоступно", status_code=404))
    try:
        if operation == "health":
            result = await OneCHealthService().check(**_scope(request), connection_id=connection_id)
            add_flash(
                request, "success" if result["state"] == "verified" else "error", "Проверка: " + result["check_code"]
            )
        else:
            await OneCConnectionService().disable(**_scope(request), connection_id=connection_id)
            add_flash(request, "success", "Подключение отключено; локальный секрет удалён.")
    except ConnectionDenied:
        return _private(HTMLResponse("Подключение недоступно", status_code=403))
    except OneCConnectionError:
        add_flash(request, "error", "Настройки подключения недоступны для проверки.")
    return _private(RedirectResponse(BACK, status_code=303))


@router.post("/hosts")
async def approve(request: Request):
    form = await _validated(request, {"host"})
    if form is None:
        return _private(HTMLResponse("Некорректная форма или сессия истекла", status_code=403))
    try:
        await WorkspaceOutboundHostManager().approve(**_scope(request), host=form["host"])
    except ConnectionDenied:
        return _private(HTMLResponse("Действие доступно владельцу или администратору", status_code=403))
    except (TrustedHostError, WorkspaceHostError):
        add_flash(request, "error", "Проверьте имя хоста: возможно, оно уже разрешено.")
    else:
        add_flash(request, "success", "Хост разрешён в этом пространстве.")
    return _private(RedirectResponse(BACK, status_code=303))


@router.post("/hosts/{host_id}/revoke")
async def revoke(request: Request, host_id: int):
    form = await _validated(request, set())
    if form is None:
        return _private(HTMLResponse("Некорректная форма или сессия истекла", status_code=403))
    try:
        await WorkspaceOutboundHostManager().revoke(**_scope(request), host_id=host_id)
    except ConnectionDenied:
        return _private(HTMLResponse("Хост недоступен", status_code=403))
    add_flash(request, "success", "Разрешение хоста отозвано.")
    return _private(RedirectResponse(BACK, status_code=303))
