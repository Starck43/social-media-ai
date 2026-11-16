"""Filtered 1C audit envelope and CSRF-gated export; no command payload projection."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response

from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.services.onec.policy import OneCAuditView

from .deps import ensure_csrf, render

router = APIRouter(prefix="/settings/onec/audit")
_FIELDS = ("id", "occurred_at", "actor_id", "action", "outcome", "resource_kind", "resource_id", "reason_code")


def _private(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _filters(request):
    values = request.query_params
    permitted = {"connection_id", "actor_id", "action", "since", "until", "before_id", "limit"}
    if set(values) - permitted or len(values.multi_items()) != len(values):
        raise ValueError("Invalid filter")
    upper = datetime.fromisoformat(values["until"]) if "until" in values else datetime.now(timezone.utc)
    lower = datetime.fromisoformat(values["since"]) if "since" in values else upper - timedelta(days=30)
    if lower.tzinfo is None or upper.tzinfo is None or not lower < upper or upper - lower > timedelta(days=90):
        raise ValueError("Invalid time window")
    result = {"since": lower, "until": upper, "limit": int(values.get("limit", "50"))}
    if not 1 <= result["limit"] <= 1000:
        raise ValueError("Invalid limit")
    for key in ("connection_id", "actor_id", "before_id"):
        if values.get(key):
            parsed = int(values[key])
            if not 0 < parsed <= 2147483647:
                raise ValueError("Invalid identity")
            result["event_actor_id" if key == "actor_id" else key] = parsed
    if values.get("action"):
        if values["action"] not in {"onec.command_approve", "onec.command_execute", "onec.audit_export"}:
            raise ValueError("Invalid operation")
        result["action"] = values["action"]
    return result


async def _read(request, *, export=False):
    user = getattr(request.state, "web_user", None)
    tenant_id = getattr(request.state, "tenant_id", None)
    if user is None:
        raise ConnectionDenied()
    return await OneCAuditView().read(tenant_id=tenant_id, actor_id=user.id, export=export, **_filters(request))


@router.get("")
async def audit_page(request: Request):
    try:
        rows = await _read(request)
    except ConnectionDenied:
        return _private(HTMLResponse("Недостаточно прав", status_code=403))
    except (ValueError, TypeError):
        return _private(HTMLResponse("Некорректный фильтр", status_code=400))
    return _private(
        render(
            request,
            "web/onec_audit.html",
            section="settings",
            rows=rows,
            fields=_FIELDS,
            filters=request.query_params,
            export_query=request.url.query,
        )
    )


@router.post("/export")
async def export_audit(request: Request, token: str = Form("", alias="_csrf")):
    if not ensure_csrf(request, token):
        return _private(HTMLResponse("Недостаточно прав", status_code=403))
    try:
        rows = await _read(request, export=True)
    except ConnectionDenied:
        return _private(HTMLResponse("Недостаточно прав", status_code=403))
    except (ValueError, TypeError):
        return _private(HTMLResponse("Некорректный фильтр", status_code=400))
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=_FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    return _private(
        Response(
            output.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="onec-audit.csv"',
            },
        )
    )
