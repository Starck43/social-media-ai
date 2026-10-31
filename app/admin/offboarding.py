"""Superuser console preview, confirmation and offboarding journal rendering."""

from __future__ import annotations

import csv
from io import StringIO

from fastapi import HTTPException
from starlette.responses import Response

from app.admin.actions import verified_action_form
from app.core.database import async_session_maker
from app.models import WorkspaceOffboardingEvent
from app.services.tenancy.offboarding import (
    POLICY_LABELS,
    REASON_LABELS,
    OffboardingError,
    confirm_offboarding,
    preview_offboarding,
)


def _actor(view, request):
    actor = view.get_admin_user(request)
    if actor is None or not actor.is_superuser:
        raise HTTPException(403, "Workspace offboarding requires the platform owner")
    return actor


def _workspace_id(raw):
    if not isinstance(raw, str) or not raw.isascii() or not raw.isdecimal():
        raise OffboardingError(400, "invalid_workspace", "Выберите одно рабочее пространство.")
    value = int(raw) if len(raw) <= 10 else 0
    if not 0 < value <= 2147483647:
        raise OffboardingError(400, "invalid_workspace", "Выберите одно рабочее пространство.")
    return value


async def offboarding_page(view, request, *, submit=False):
    actor = _actor(view, request)
    context = {
        "title": "Удаление рабочего пространства",
        "reason_labels": REASON_LABELS,
        "policy_labels": POLICY_LABELS,
        "stage": "setup",
        "preview": None,
        "receipt": None,
        "error": None,
        "action_url": f"{view._admin_ref.base_url}/tenant/action/offboard",
        "list_url": str(request.url_for("admin:list", identity=view.identity)),
        "journal_url": f"{view._admin_ref.base_url}/tenant/offboarding-journal",
    }
    status = 200
    try:
        if not submit:
            context["preview"] = await preview_offboarding(actor.id, _workspace_id(request.query_params.get("pks", "")))
        else:
            form = await verified_action_form(request)
            intent = form.get("intent")
            if intent == "preview":
                context["preview"] = await preview_offboarding(
                    actor.id,
                    _workspace_id(form.get("pks")),
                    str(form.get("reason_code", "")),
                    str(form.get("retention_policy", "")),
                )
                context["stage"] = "review"
            elif intent == "confirm":
                if form.get("confirmed") != "yes":
                    raise OffboardingError(
                        400, "confirmation_required", "Подтвердите необратимое удаление пространства."
                    )
                context["receipt"] = await confirm_offboarding(actor.id, str(form.get("review_token", "")))
                context["stage"] = "receipt"
            else:
                raise OffboardingError(400, "invalid_intent", "Выберите предварительный просмотр или подтверждение.")
    except OffboardingError as exc:
        status = exc.status_code
        context.update(stage="error", error=exc.message, error_code=exc.code)
    response = await view.templates.TemplateResponse(
        request, "sqladmin/workspace_offboarding.html", context, status_code=status
    )
    response.headers["Cache-Control"] = "no-store"
    return response


async def offboarding_journal(view, request):
    _actor(view, request)
    export = request.query_params.get("format") == "csv"
    try:
        async with async_session_maker() as session:
            rows = await WorkspaceOffboardingEvent.objects.export_events(session=session, limit=1000 if export else 100)
    except PermissionError:
        raise HTTPException(403, "Workspace offboarding journal requires the active platform owner") from None
    except RuntimeError:
        raise HTTPException(503, "Workspace offboarding journal requires migration 0119") from None
    if export:
        output = StringIO()
        fields = [
            "event_id",
            "workspace_id",
            "actor_id",
            "occurred_at",
            "reason_code",
            "retention_policy",
            "expired_audit_count",
            "outcome",
        ]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
        return Response(
            output.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="workspace-offboarding.csv"',
                "Cache-Control": "no-store",
            },
        )
    response = await view.templates.TemplateResponse(
        request,
        "sqladmin/workspace_offboarding_journal.html",
        {
            "title": "Журнал удалений пространств",
            "rows": rows,
            "reason_labels": REASON_LABELS,
            "policy_labels": POLICY_LABELS,
            "list_url": str(request.url_for("admin:list", identity=view.identity)),
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return response
