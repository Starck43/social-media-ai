"""Digests `/app/digests` — what was sent, and a manual send (docs/design/ui.md §4.5).

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

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.models.digest_run import DigestRun
from app.services.digest.builder import DigestDeliveryError, build_and_publish
from app.services.digest.render import render_digest

from .deps import action_tenant_id, add_flash, ensure_csrf, guard_web, render, tenant_filter_context

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


async def _page(
    request: Request,
    period: str | None,
    preview: bool = False,
    group_by: str | None = None,
    time_breakdown: str | None = None,
):
    """Shared render for both views — they differ only by the preview text."""
    user = getattr(request.state, "web_user", None)
    is_superuser = bool(user and user.is_superuser)
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

    context: dict = {
        "section": "digests",
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
