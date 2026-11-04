"""Installation administrator surface for trusted outbound hosts (`/app/admin/trusted-hosts`, P14-T09).

Every route asks `guard_superuser` first (hiding a link is not a check) and the
manager re-checks the platform role from the database at execution. Tenant actors
use approved hosts through connectors; they never reach these routes.
"""

from __future__ import annotations

import asyncio
import logging
import re

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.core import outbound
from app.core.tenant_context import tenant_scope
from app.models.managers.trusted_outbound_host_manager import TrustedHostError, to_entry

from .deps import add_flash, ensure_csrf, guard_superuser, render

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/trusted-hosts")
BACK = "/app/admin/trusted-hosts"
DENIED_BACK = "/app"
RESOLVE_TIMEOUT = 3.0
# Injected for tests; production uses the seam's only resolver.
RESOLVER: outbound.Resolver = outbound.default_dns_resolver

_ERRORS = {
    "invalid_host": "Укажите доменное имя хоста, например crm.example.ru (без схемы, порта и IP-адреса).",
    "invalid_kind": "Выберите тип хоста: публичный или внутренний.",
    "public_host_takes_no_addresses": "У публичного хоста не указываются адреса: он проверяется как публичный.",
    "private_host_requires_addresses": "Для внутреннего хоста укажите от 1 до 16 разрешённых IP-адресов.",
    "invalid_address": "Один из адресов не является IP-адресом.",
    "address_forbidden": "Loopback, link-local, multicast и служебные адреса разрешить нельзя.",
    "host_already_trusted": "Этот хост уже есть в списке.",
    "host_not_found": "Хост уже удалён.",
    "confirmation_required": "Удаление не подтверждено.",
    "admin_required": "Недостаточно прав для этого действия",
}


def _actor_id(request: Request) -> int | None:
    user = getattr(request.state, "web_user", None)
    actor_id = getattr(user, "id", None)
    if user is None or not getattr(user, "is_active", False) or type(actor_id) is not int or actor_id <= 0:
        return None
    return actor_id


def _tenant_id(request: Request) -> int | None:
    tenant_id = getattr(request.state, "tenant_id", None)
    return tenant_id if type(tenant_id) is int and tenant_id > 0 else None


def _check(entry: outbound.TrustedHost) -> dict:
    """Resolve now, through the seam, exactly as the next connector attempt would."""
    try:
        target = outbound.acquire_outbound_target(
            entry.host, 443, registry=outbound.registry_from_entries([entry]), resolver=RESOLVER
        )
    except outbound.OutboundRefusal as refusal:
        return {"ok": False, "code": refusal.code, "addresses": ()}
    return {"ok": True, "code": "", "addresses": target.addresses}


async def _resolve(entry: outbound.TrustedHost) -> dict:
    try:
        return await asyncio.wait_for(asyncio.to_thread(_check, entry), RESOLVE_TIMEOUT)
    except (asyncio.TimeoutError, OSError):
        return {"ok": False, "code": outbound.DNS_RESOLUTION_FAILED, "addresses": ()}


@router.get("")
async def trusted_hosts_page(request: Request):
    from app.models.integrations.trusted_outbound_host import TrustedOutboundHost

    denied = guard_superuser(request, back=DENIED_BACK)
    if denied is not None:
        return denied
    actor_id = _actor_id(request)
    try:
        rows = await TrustedOutboundHost.objects.list_hosts(actor_id=actor_id)
    except TrustedHostError:
        add_flash(request, "error", _ERRORS["admin_required"])
        return RedirectResponse(DENIED_BACK, status_code=302)
    checks = await asyncio.gather(*(_resolve(to_entry(row)) for row in rows))
    return render(
        request,
        "web/trusted_hosts.html",
        section="admin",
        hosts=list(zip(rows, checks)),
        can_write=_tenant_id(request) is not None,
    )


def _split_addresses(raw: str) -> list[str]:
    return [part for part in re.split(r"[\s,;]+", raw or "") if part]


@router.post("/add")
async def trusted_host_add(
    request: Request,
    host: str = Form(""),
    kind: str = Form("public"),
    addresses: str = Form(""),
    note: str = Form(""),
    token: str = Form("", alias="_csrf"),
):
    from app.models.integrations.trusted_outbound_host import TrustedOutboundHost

    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия устарела, повторите действие")
        return RedirectResponse(BACK, status_code=302)
    denied = guard_superuser(request, back=DENIED_BACK)
    if denied is not None:
        return denied
    tenant_id = _tenant_id(request)
    if tenant_id is None:
        add_flash(request, "error", "Выберите пространство: изменение записывается в его журнал аудита")
        return RedirectResponse(BACK, status_code=302)
    try:
        with tenant_scope(tenant_id):
            row = await TrustedOutboundHost.objects.add_host(
                actor_id=_actor_id(request),
                host=host,
                kind=kind,
                addresses=_split_addresses(addresses),
                note=note,
            )
    except TrustedHostError as exc:
        add_flash(request, "error", _ERRORS.get(exc.code, "Не удалось добавить хост"))
        return RedirectResponse(BACK, status_code=302)
    add_flash(request, "success", f"Хост {row.host} добавлен; действует со следующего подключения.")
    return RedirectResponse(BACK, status_code=302)


@router.post("/{host_id}/remove")
async def trusted_host_remove(
    request: Request,
    host_id: int,
    confirm_host: str = Form(""),
    token: str = Form("", alias="_csrf"),
):
    from app.models.integrations.trusted_outbound_host import TrustedOutboundHost

    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия устарела, повторите действие")
        return RedirectResponse(BACK, status_code=302)
    denied = guard_superuser(request, back=DENIED_BACK)
    if denied is not None:
        return denied
    tenant_id = _tenant_id(request)
    if tenant_id is None:
        add_flash(request, "error", "Выберите пространство: изменение записывается в его журнал аудита")
        return RedirectResponse(BACK, status_code=302)
    try:
        with tenant_scope(tenant_id):
            host = await TrustedOutboundHost.objects.remove_host(
                actor_id=_actor_id(request), host_id=host_id, confirm_host=confirm_host
            )
    except TrustedHostError as exc:
        add_flash(request, "error", _ERRORS.get(exc.code, "Не удалось удалить хост"))
        return RedirectResponse(BACK, status_code=302)
    add_flash(request, "success", f"Хост {host} удалён; следующее подключение к нему будет заблокировано.")
    return RedirectResponse(BACK, status_code=302)
