"""Digests `/app/digests` — what was sent, and a manual send (docs/DIGEST.md).

Three things live here, and the distinction between them is the whole point:

* **history** — `digest_runs` rows: what actually went out to a channel, to
  which chat, and what it cost. Read-only.
* **preview** — what the digest *would* say right now. Aggregates and renders,
  but never summarises through the LLM and never publishes, so it is free to
  click and cannot spam the channel. The page labels it "без AI-сводки".
* **send now** — the real `build_and_publish`, the same call the CLI's
  `digest send-now` makes, so both surfaces share one implementation and one
  `digest_runs` audit trail.

Delivery is `force=True`: a manual send gets its own run row (a NULL
`agent_task_id` never collides in the unique constraint), so asking twice gets
two digests — which is what was asked for.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.models.notifications.digest_run import DigestRun
from app.services.digest.builder import DigestDeliveryError, build_and_publish
from app.services.digest.render import render_digest

from .deps import action_tenant_id, add_flash, ensure_csrf, guard_web, perms_can, render, tenant_filter_context

router = APIRouter(prefix="/digests")

PERIODS = (("day", "День"), ("week", "Неделя"), ("month", "Месяц"))

# Enough runs to spot a broken schedule, short enough that the page stays a
# status screen rather than a log viewer.
HISTORY_LIMIT = 40


def valid_period(raw: str | None, default: str = "day") -> str:
    """A known period name — anything else falls back to the default."""
    return raw if raw in dict(PERIODS) else default


async def _runs_for(tenant_id: int | None) -> list[DigestRun]:
    """Most recent digest runs, newest first, for one workspace (None = all)."""
    query = DigestRun.objects.order_by(DigestRun.created_at.desc()).limit(HISTORY_LIMIT)
    if tenant_id is not None:
        query = query.filter(tenant_id=tenant_id)
    return list(await query)


def _stats(runs: list[DigestRun]) -> dict[str, float | int]:
    return {
        "sent": sum(1 for r in runs if r.status == "sent"),
        "failed": sum(1 for r in runs if r.status == "failed"),
        "skipped": sum(1 for r in runs if r.status == "skipped"),
        "cost_usd": sum(float(r.llm_cost or 0.0) for r in runs),
    }


@router.get("")
@router.get("/")
async def digests_list(request: Request, period: str | None = None, group_by: str | None = None):
    """History of digest runs plus the manual-send panel."""
    return await _page(request, period, group_by=group_by)


@router.get("/preview")
async def digest_preview(
    request: Request,
    period: str | None = None,
    group_by: str | None = None,
    time_breakdown: str | None = None,
):
    """Render the digest for `period` without summarising or publishing.

    Deliberately skips `_summarize`: a preview that quietly spends LLM budget
    (and could be spammed by reloading) would make this button a cost. The
    panel says "без AI-сводки" so nobody mistakes it for the real delivery.
    """
    return await _page(request, period, preview=True, group_by=group_by, time_breakdown=time_breakdown)


def _brief_timezone(raw: str | None):
    from fastapi import HTTPException

    if raw is None or raw == "":
        return None
    try:
        if len(raw) > 64:
            raise ValueError("zone too long")
        ZoneInfo(raw)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(400, "Invalid timezone") from None
    return raw


def _brief_fixed_window(start, end, days):
    from fastapi import HTTPException

    if start is None and end is None:
        return {}
    if type(start) is not date or type(end) is not date or start > end or (end - start).days != days - 1:
        raise HTTPException(400, "Invalid report window")
    return {"report_start": start, "report_end": end}


@router.get("/morning")
async def morning_brief_preview(
    request: Request, days: int = 1, tz: str | None = None, start: date | None = None, end: date | None = None
):
    """Actor-scoped deterministic preview; no paid ranking or external send."""
    from fastapi import HTTPException

    from app.services.social.morning_brief import preview

    if days not in range(1, 32):
        raise HTTPException(400, "Invalid report window")
    actor = getattr(request.state, "web_user", None)
    tenant_id = getattr(request.state, "tenant_id", None)
    if actor is None or tenant_id is None:
        raise HTTPException(403, "Workspace and actor required")
    tz = _brief_timezone(tz)
    fixed_window = _brief_fixed_window(start, end, days)
    key = f"social_triage:{tenant_id}:{actor.id}"
    try:
        brief = await preview(
            tenant_id,
            actor.id,
            days=days,
            overrides=request.session.get(key, {}),
            **({"timezone_name": tz} if tz else {}),
            **fixed_window,
        )
    except PermissionError:
        raise HTTPException(403, "Social brief unavailable") from None
    except ValueError:
        raise HTTPException(400, "Invalid report window or timezone") from None
    except Exception:
        return render(
            request,
            "web/social_brief.html",
            section="digests",
            brief=None,
            days=days,
            error="Социальный источник или сервис сводки временно недоступен. Полнота данных не подтверждена.",
            status_code=503,
        )
    return render(
        request,
        "web/social_brief.html",
        section="digests",
        brief=brief,
        days=days,
        can_schedule=perms_can(request, "agenttask", "create") and perms_can(request, "agenttask", "update"),
    )


@router.post("/morning/correct")
async def morning_brief_correct(
    request: Request,
    analysis_id: int = Form(...),
    category: str = Form(...),
    days: int = Form(1),
    token: str = Form("", alias="_csrf"),
    tz: Annotated[str | None, Form()] = None,
    start: Annotated[date | None, Form()] = None,
    end: Annotated[date | None, Form()] = None,
):
    from fastapi import HTTPException

    from app.services.social.morning_brief import preview

    if not ensure_csrf(request, token):
        raise HTTPException(403, "Invalid CSRF")
    actor = getattr(request.state, "web_user", None)
    tenant_id = getattr(request.state, "tenant_id", None)
    if actor is None or tenant_id is None or days not in range(1, 32):
        raise HTTPException(403, "Workspace and actor required")
    if category not in {"needs_attention", "can_wait", "uncertain", "restore"}:
        raise HTTPException(400, "Invalid classification")
    tz = _brief_timezone(tz)
    fixed_window = _brief_fixed_window(start, end, days)
    try:
        brief = await preview(tenant_id, actor.id, days=days, **({"timezone_name": tz} if tz else {}), **fixed_window)
    except PermissionError:
        raise HTTPException(403, "Social brief unavailable") from None
    except ValueError:
        raise HTTPException(400, "Invalid report window or timezone") from None
    except Exception:
        raise HTTPException(503, "Social brief service unavailable") from None
    reference = f"analysis:{analysis_id}"
    if not any(item.authoritative_ref == reference for item in brief.items):
        raise HTTPException(404, "Evidence unavailable")
    key = f"social_triage:{tenant_id}:{actor.id}"
    decisions = dict(request.session.get(key, {}))
    if category == "restore":
        decisions.pop(reference, None)
    else:
        # Bound cookie/session size. This state stores references and decisions only.
        decisions.pop(reference, None)
        decisions[reference] = category
        decisions = dict(list(decisions.items())[-20:])
    request.session[key] = decisions
    query = urlencode(
        {
            "days": days,
            **({"tz": tz} if tz else {}),
            **({"start": start.isoformat(), "end": end.isoformat()} if fixed_window else {}),
        }
    )
    return RedirectResponse(f"/app/digests/morning?{query}", status_code=303)


@router.post("/morning/schedule")
async def morning_brief_schedule(
    request: Request,
    local_time: Annotated[str, Form()],
    tz: Annotated[str, Form()],
    days: Annotated[int, Form()] = 1,
    channel: Annotated[str, Form()] = "inbox",
    confirm_external: Annotated[bool, Form()] = False,
    token: Annotated[str, Form(alias="_csrf")] = "",
):
    from fastapi import HTTPException

    from app.services.social.brief_schedule import configure_schedule

    if not ensure_csrf(request, token):
        raise HTTPException(403, "Invalid CSRF")
    actor = getattr(request.state, "web_user", None)
    tenant_id = getattr(request.state, "tenant_id", None)
    if actor is None or tenant_id is None:
        raise HTTPException(403, "Workspace and actor required")
    if channel != "inbox" and not confirm_external:
        raise HTTPException(400, "Explicit notification permission required")
    zone = _brief_timezone(tz)
    if zone is None:
        raise HTTPException(400, "Timezone required")
    try:
        await configure_schedule(
            tenant_id,
            actor.id,
            local_time=local_time,
            timezone_name=zone,
            days=days,
            channel=channel,
            notify_confirmed=confirm_external,
        )
    except PermissionError:
        raise HTTPException(403, "Social schedule unavailable") from None
    except ValueError:
        raise HTTPException(400, "Invalid schedule or conflicting task") from None
    add_flash(request, "success", "Расписание уведомлений сохранено. Тихие часы применяются перед внешней доставкой.")
    query = urlencode({"days": days, "tz": zone})
    return RedirectResponse(f"/app/digests/morning?{query}", status_code=303)


async def _page(
    request: Request,
    period: str | None,
    preview: bool = False,
    group_by: str | None = None,
    time_breakdown: str | None = None,
):
    """Shared render for both views — they differ only by the preview text."""
    user = getattr(request.state, "web_user", None)
    is_superuser = False
    filter_tenant_id, tenants = await tenant_filter_context(request, is_superuser)
    tenant_id = getattr(request.state, "tenant_id", None)

    from app.core.tenant_context import tenant_scope

    # Which workspace's runs to show. A superuser with no active workspace sees
    # the global history — the same rule the dashboard's aggregates use (never
    # "no tenant means everything" for an ordinary user).
    if is_superuser and tenant_id is None:
        with tenant_scope(bypass=True):
            runs = await _runs_for(filter_tenant_id)
    else:
        runs = await _runs_for(filter_tenant_id if is_superuser else tenant_id)

    delivery, delivery_unavailable = None, False
    if tenant_id is not None and user is not None:
        # P8-T11: fresh role grants of the active workspace; no operator bypass.
        try:
            from app.core.access_catalog import workspace_grants
            from app.services.notifications.delivery_signal import delivery_scope, workspace_signal

            web_perms = getattr(request.state, "web_perms", None)
            codename = web_perms.workspace_role if web_perms is not None else None
            scope = delivery_scope(tenant_id, user.id, codename, workspace_grants(codename))
            if scope is not None:
                delivery = await workspace_signal(scope)
        except Exception:
            delivery_unavailable = True

    context: dict = {
        "section": "digests",
        "delivery": delivery,
        "delivery_unavailable": delivery_unavailable,
        "runs": runs,
        "stats": _stats(runs),
        "periods": PERIODS,
        "selected_period": valid_period(period),
        "selected_group_by": group_by or "themes",
        "selected_time_breakdown": time_breakdown == "true",
        "is_superuser": is_superuser,
        "tenants": tenants,
        "filter_tenant_id": filter_tenant_id,
        "preview_text": None,
        "preview_period": None,
    }

    if preview:
        from app.services.digest.builder import aggregate

        chosen = context["selected_period"]
        gby = context["selected_group_by"]
        td = context["selected_time_breakdown"]
        if is_superuser and tenant_id is None:
            with tenant_scope(bypass=True):
                data, start, end = await aggregate(chosen, group_by=gby, time_breakdown=td)
        else:
            data, start, end = await aggregate(chosen, group_by=gby, time_breakdown=td)
        context["preview_text"] = render_digest(data, summary=None)
        context["preview_period"] = (chosen, start, end)

    return render(request, "web/digests.html", **context)


@router.post("/send-now")
async def digest_send_now(
    request: Request,
    period: str = Form("day"),
    group_by: str = Form("themes"),
    time_breakdown: str = Form(""),
    token: str = Form("", alias="_csrf"),
    tenant_id: int | None = Form(default=None),
):
    """Build and publish a digest right now — the manual send."""
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse("/app/digests", status_code=302)

    tenant_id = action_tenant_id(request, tenant_id)

    # Publishing spends LLM budget and writes to a customer-facing channel, so
    # it is gated like any other mutation: `digestrun` rights, owner or superuser.
    denied = guard_web(request, "digestrun", "create", back="/app/digests")
    if denied is not None:
        return denied

    from app.core.tenant_context import tenant_scope

    try:
        # `force=True` — a manual send is its own run, not a retry of the
        # scheduled one, so the "already sent" guard must not swallow it.
        with tenant_scope(tenant_id):
            result = await build_and_publish(
                period=valid_period(period),
                force=True,
                group_by=group_by,
                time_breakdown=bool(time_breakdown),
            )
    except DigestDeliveryError as e:
        add_flash(request, "error", f"Дайджест собран, но не доставлен: {e}")
    except Exception as e:  # noqa: BLE001 - a manual send must not 500 the page
        add_flash(request, "error", f"Не удалось собрать дайджест: {e}")
    else:
        status = result.get("status")
        if status == "sent":
            add_flash(request, "success", "Дайджест отправлен в каналы")
        elif status == "skipped":
            add_flash(request, "error", result.get("reason") or "Отправка пропущена — проверьте каналы")
        else:
            add_flash(request, "error", result.get("error") or "Дайджест не отправлен")
    return RedirectResponse("/app/digests", status_code=302)
