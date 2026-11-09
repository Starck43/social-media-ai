"""Unified approval inbox web routes (/app/commands) over P13-T04 command ledger (P20-T03)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Form, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.core.tenant_context import TenantContextError
from app.services.commands import (
    DEFAULT_TTL_SECONDS,
    CommandConflict,
    CommandDenied,
    CommandError,
    CommandNotFound,
    approval_inbox,
)
from app.web.deps import add_flash, ensure_csrf, render

router = APIRouter()


def _is_json_request(request: Request) -> bool:
    accept = (request.headers.get("accept") or "").lower()
    return "application/json" in accept or request.headers.get("x-requested-with") == "XMLHttpRequest"


def _str_param(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _int_param(value: Any, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _truthy_flag(value: Any) -> bool:
    return _str_param(value).strip().lower() in {"1", "true", "yes", "on"}


@router.get("/commands")
async def commands_page(
    request: Request,
    before_id: int | None = Query(None, gt=0),
    status: str | None = Query(None),
):
    perms = getattr(request.state, "web_perms", None)
    before_val = before_id if isinstance(before_id, int) else None
    status_val = status if isinstance(status, str) else None
    try:
        page = await approval_inbox.list_items(
            limit=50,
            before_id=before_val,
            status_filter=status_val,
            perms=perms,
        )
        pending_count = sum(1 for item in page.get("items", []) if item.get("status") == "pending" and item.get("actionable"))
        request.state.pending_approvals_count = pending_count
    except (PermissionError, CommandDenied, TenantContextError):
        raise HTTPException(403, "Command ledger access denied") from None
    except CommandError:
        raise HTTPException(403, "Command ledger access denied") from None
    return render(
        request,
        "web/commands.html",
        section="actions",
        status_filter=(status_val or "all"),
        selected_item=None,
        **page,
    )


@router.get("/commands/{command_id}")
async def command_detail_page(
    request: Request,
    command_id: int = Path(gt=0),
):
    perms = getattr(request.state, "web_perms", None)
    try:
        item = await approval_inbox.get_item(command_id, perms=perms)
    except CommandNotFound:
        raise HTTPException(404, "Command not found") from None
    except (PermissionError, CommandDenied, TenantContextError, CommandError):
        raise HTTPException(403, "Command review access denied") from None

    return render(
        request,
        "web/commands.html",
        section="actions",
        status_filter="all",
        selected_item=item,
        items=[item],
        limit=1,
        next_before_id=None,
    )


@router.post("/commands/{command_id}/approve")
async def command_approve_action(
    request: Request,
    command_id: int = Path(gt=0),
    token: str = Form("", alias="_csrf"),
    expected_payload_hash: str = Form(""),
    ttl_seconds: int = Form(DEFAULT_TTL_SECONDS),
    always_allow: str = Form(""),
    auto_execute: str = Form(""),
    bulk_ids: str = Form(""),
):
    is_json = _is_json_request(request)
    token_str = _str_param(token)
    if not ensure_csrf(request, token_str):
        if is_json:
            return JSONResponse({"ok": False, "error": "invalid_csrf"}, status_code=403)
        add_flash(request, "error", "Сессия истекла, обновите страницу и повторите подтверждение")
        return RedirectResponse(f"/app/commands/{command_id}", status_code=303)

    perms = getattr(request.state, "web_perms", None)
    bulk_str = _str_param(bulk_ids).strip()
    parsed_bulk = [int(x) for x in bulk_str.split(",") if x.strip().isdigit()] if bulk_str else None

    try:
        approved = await approval_inbox.approve_item(
            command_id,
            expected_payload_hash=_str_param(expected_payload_hash),
            ttl_seconds=_int_param(ttl_seconds, DEFAULT_TTL_SECONDS),
            perms=perms,
            always_allow=_truthy_flag(always_allow),
            bulk_command_ids=parsed_bulk,
            auto_execute=_truthy_flag(auto_execute),
        )
    except CommandNotFound:
        if is_json:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        raise HTTPException(404, "Command not found") from None
    except CommandConflict as exc:
        if is_json:
            return JSONResponse({"ok": False, "error": "conflict", "message": str(exc)}, status_code=409)
        add_flash(request, "error", f"Конфликт состояния команды: {exc}")
        return RedirectResponse(f"/app/commands/{command_id}", status_code=303)
    except (PermissionError, CommandDenied, TenantContextError) as exc:
        if is_json:
            return JSONResponse({"ok": False, "error": "forbidden", "message": str(exc)}, status_code=403)
        add_flash(request, "error", f"Согласование отклонено: {exc}")
        return RedirectResponse(f"/app/commands/{command_id}", status_code=303)

    if is_json:
        return JSONResponse({"ok": True, "item": approved}, headers={"Cache-Control": "no-store"})
    add_flash(
        request,
        "success",
        f"Команда #{command_id} согласована. Выполнение запускается отдельно по контракту владельца.",
    )
    return RedirectResponse(f"/app/commands/{command_id}", status_code=303)


@router.post("/commands/{command_id}/reject")
async def command_reject_action(
    request: Request,
    command_id: int = Path(gt=0),
    token: str = Form("", alias="_csrf"),
    expected_payload_hash: str = Form(""),
    reason: str = Form("user_rejected"),
    always_allow: str = Form(""),
    bulk_ids: str = Form(""),
):
    is_json = _is_json_request(request)
    token_str = _str_param(token)
    if not ensure_csrf(request, token_str):
        if is_json:
            return JSONResponse({"ok": False, "error": "invalid_csrf"}, status_code=403)
        add_flash(request, "error", "Сессия истекла, обновите страницу и повторите действие")
        return RedirectResponse(f"/app/commands/{command_id}", status_code=303)

    perms = getattr(request.state, "web_perms", None)
    bulk_str = _str_param(bulk_ids).strip()
    parsed_bulk = [int(x) for x in bulk_str.split(",") if x.strip().isdigit()] if bulk_str else None
    reason_str = _str_param(reason, "user_rejected").strip() or "user_rejected"
    hash_str = _str_param(expected_payload_hash).strip() or None

    try:
        rejected = await approval_inbox.reject_item(
            command_id,
            reason=reason_str,
            expected_payload_hash=hash_str,
            perms=perms,
            always_allow=_truthy_flag(always_allow),
            bulk_command_ids=parsed_bulk,
        )
    except CommandNotFound:
        if is_json:
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
        raise HTTPException(404, "Command not found") from None
    except CommandConflict as exc:
        if is_json:
            return JSONResponse({"ok": False, "error": "conflict", "message": str(exc)}, status_code=409)
        add_flash(request, "error", f"Конфликт состояния команды: {exc}")
        return RedirectResponse(f"/app/commands/{command_id}", status_code=303)
    except (PermissionError, CommandDenied, TenantContextError) as exc:
        if is_json:
            return JSONResponse({"ok": False, "error": "forbidden", "message": str(exc)}, status_code=403)
        add_flash(request, "error", f"Отклонение недоступно: {exc}")
        return RedirectResponse(f"/app/commands/{command_id}", status_code=303)

    if is_json:
        return JSONResponse({"ok": True, "item": rejected}, headers={"Cache-Control": "no-store"})
    add_flash(request, "success", f"Команда #{command_id} отклонена и отменена.")
    return RedirectResponse(f"/app/commands/{command_id}", status_code=303)
