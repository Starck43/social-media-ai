"""Sources CRUD for `/app/sources` (docs/design/ui.md §4.3).

List + add + edit + toggle + delete + a per-source detail page. Sources are
scoped to the current workspace.

Two `Source.params` keys are part of *collecting*, not cosmetics, so they get
first-class form fields instead of being invisible defaults:

- `mode` — which layer collects this source (`pull` = the platform API,
  `push` = Telegram Bot API updates, `user` = the owner's L2 session). It was
  settable only in the DB, which made "why is nothing collected?" unanswerable
  from the UI.
- `token_owner` — the `users.id` whose personal L2 token this source may use.
  The value is validated against workspace membership here as well as at
  collection time (`app/services/social/owner.py`), so the UI cannot save a
  selection that would later be silently ignored.
"""

from __future__ import annotations

from contextlib import nullcontext

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import cast, func
from sqlalchemy.dialects.postgresql import JSONB

from app.models.source import Source
from app.types import SourceType

from .deps import (
    action_tenant_id,
    add_flash,
    ensure_csrf,
    guard_web,
    perms_can,
    render,
    safe_next,
    tenant_filter_context,
)

router = APIRouter(prefix="/sources")


# Collection modes a source can run in. `user` is not a layer of its own — it is
# the L2 variant of the platform client, chosen by `owner.resolve_source_owner`.
COLLECTION_MODES: tuple[tuple[str, str], ...] = (
    ("pull", "Через API платформы — токен сообщества, стабильно, с пагинацией"),
    ("push", "Через бот-API — источник сам присылает обновления (Telegram)"),
    ("user", "Через личный аккаунт — токен участника workspace (медленно, выше лимиты)"),
)

# Short names for the badges; `COLLECTION_MODES` carries the long explanation.
_MODE_LABELS: dict[str, str] = {"pull": "API платформы", "push": "Бот-API", "user": "Личный аккаунт"}


def _clean_params(raw: str, base: dict | None = None) -> dict:
    """Merge a comma-separated `monitored_users` field into `params`."""
    params = dict(base or {})
    tokens = [u.strip() for u in raw.split(",") if u.strip()]
    if tokens:
        params["monitored_users"] = tokens
    else:
        params.pop("monitored_users", None)
    return params


async def _workspace_members(tenant_id: int) -> list:
    """Web members of a workspace — the only valid `token_owner` choices."""
    from app.models.managers.tenant_manager import tenant_users
    from app.models.user import User

    memberships = await tenant_users.web_memberships_for_tenant(tenant_id)
    if not memberships:
        return []
    user_ids = [m.user_id for m in memberships]
    users = await User.objects.filter(id__in=user_ids)
    by_id = {u.id: u for u in users}
    return [by_id[uid] for uid in user_ids if uid in by_id]


def _validate_token_owner(form_value: str, member_ids: set[int]) -> tuple[int | None, str | None]:
    """Resolve the posted `token_owner` to a member id, or report why not.

    Empty means "no explicit owner" — collection then falls back to the
    workspace owner (`owner.resolve_source_owner`). A value that is not an
    active member is an error, not something to persist and drop later.
    """
    raw = (form_value or "").strip()
    if not raw:
        return None, None
    if not raw.isdigit() or int(raw) not in member_ids:
        # Echo the rejected value so the operator can see *which* member the
        # stale dropdown entry pointed at; Jinja escapes it when the flash
        # renders, so raw form input is safe to name here.
        return None, f"Пользователь {raw} не состоит в этом workspace"
    return int(raw), None


async def _platforms():
    from app.models.platform import Platform

    return await Platform.objects.order_by(Platform.id)


async def _resolve_platform(form_value: str):
    from app.models.platform import Platform
    from app.types import PlatformType

    pt = PlatformType.get_by_value(form_value)
    if pt is None:
        return None
    return await Platform.objects.filter(platform_type=pt).first()


@router.get("")
@router.get("/")
async def sources_list(request: Request):
    user = getattr(request.state, "web_user", None)
    is_superuser = bool(user and user.is_superuser)
    filter_tenant_id, tenants = await tenant_filter_context(request, is_superuser)

    raw_scenario = request.query_params.get("scenario_id")
    filter_scenario_id = int(raw_scenario) if raw_scenario and raw_scenario.isdigit() else None

    from app.core.tenant_context import tenant_scope
    from app.models import AgentScenario

    # Scenarios for the filter dropdown — scoped to the workspace being viewed.
    if is_superuser:
        with tenant_scope(filter_tenant_id):
            scenarios = list(await AgentScenario.objects.order_by(AgentScenario.name))
    else:
        scenarios = list(
            await AgentScenario.objects.filter(tenant_id=request.state.tenant_id).order_by(AgentScenario.name)
        )

    if is_superuser:
        # Superuser sees all tenants' sources, optionally narrowed to one.
        # Bypass the tenant guard (the middleware already set a tenant scope) so
        # the manager query isn't silently scoped to the active workspace.
        with tenant_scope(bypass=True):
            query = Source.objects.select_related("platform", "tenant")
            if filter_tenant_id is not None:
                query = query.filter(tenant_id=filter_tenant_id)
            if filter_scenario_id is not None:
                query = query.filter(agent_scenario_id=filter_scenario_id)
            sources = await query.order_by(Source.created_at.desc())
    else:
        query = Source.objects.filter(tenant_id=request.state.tenant_id)
        if filter_scenario_id is not None:
            query = query.filter(agent_scenario_id=filter_scenario_id)
        sources = await query.select_related("platform").order_by(Source.created_at.desc())
    user_sources = await (
        Source.objects.filter(tenant_id=request.state.tenant_id, source_type=SourceType.USER)
        .select_related("platform")
        .order_by(Source.name, Source.id)
    )
    user_sources_data = [
        {"id": s.id, "name": s.name or s.external_id, "platform": s.platform.platform_type.db_value}
        for s in user_sources
    ]
    return render(
        request,
        "web/sources.html",
        section="sources",
        sources=sources,
        platforms=await _platforms(),
        source_types=list(SourceType),
        modes=COLLECTION_MODES,
        members=await _workspace_members(request.state.tenant_id) if request.state.tenant_id else [],
        user_sources_json=user_sources_data,
        is_superuser=is_superuser,
        tenants=tenants,
        filter_tenant_id=filter_tenant_id,
        scenarios=scenarios,
        filter_scenario_id=filter_scenario_id,
    )


@router.post("")
async def source_add(
    request: Request,
    name: str = Form(""),
    platform: str = Form(...),
    external_id: str = Form(...),
    source_type: str = Form(...),
    monitored_users: str = Form(""),
    mode: str = Form("pull"),
    token_owner: str = Form(""),
    token: str = Form("", alias="_csrf"),
    tenant_id: int | None = Form(default=None),
    # The onboarding wizard posts here too — land back on the page that was
    # filled in (validating, so `?next=` cannot become an open redirect).
    next: str = Form(""),
):
    back = safe_next(next) or "/app/sources"

    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse(back, status_code=302)

    tenant_id = action_tenant_id(request, tenant_id)

    denied = guard_web(request, "source", "create", back=back)
    if denied is not None:
        return denied

    if mode not in dict(COLLECTION_MODES):
        # Never write an unknown mode: the collector would accept the row and
        # collect nothing with it. Say which value was rejected instead of
        # silently substituting a different collection layer.
        add_flash(request, "error", f"Неизвестный режим сбора: {mode}")
        return RedirectResponse(back, status_code=302)

    owner_id, owner_error = _validate_token_owner(token_owner, {u.id for u in await _workspace_members(tenant_id)})
    if owner_error:
        add_flash(request, "error", owner_error)
        return RedirectResponse(back, status_code=302)

    platform_row = await _resolve_platform(platform)
    if platform_row is None:
        add_flash(request, "error", f"Платформа '{platform}' не найдена")
        return RedirectResponse(back, status_code=302)

    st = SourceType.get_by_value(source_type)
    if st is None:
        add_flash(request, "error", f"Тип источника '{source_type}' не найден")
        return RedirectResponse(back, status_code=302)

    from app.core.tenant_context import tenant_scope

    with tenant_scope(tenant_id):
        existing = await Source.objects.get(
            tenant_id=tenant_id,
            platform_id=platform_row.id,
            external_id=external_id,
        )
        if existing is not None:
            add_flash(request, "error", "Источник с таким ID уже существует")
            return RedirectResponse(back, status_code=302)

        clean_name = name.strip()[:100] or f"{platform}:{external_id}"

        params: dict = _clean_params(monitored_users)
        params["mode"] = mode
        if owner_id is not None:
            params["token_owner"] = owner_id

        await Source.objects.create(
            name=clean_name,
            platform_id=platform_row.id,
            external_id=external_id.strip()[:200],
            source_type=st,
            is_active=True,
            params=params,
        )

    add_flash(request, "success", f"Источник '{clean_name}' добавлен")
    return RedirectResponse(back, status_code=302)


@router.post("/{source_id}")
async def source_edit(
    request: Request,
    source_id: int,
    name: str = Form(""),
    platform: str = Form(...),
    external_id: str = Form(...),
    source_type: str = Form(...),
    is_active: str = Form("", alias="is_active"),
    monitored_users: str = Form(""),
    mode: str = Form("pull"),
    token_owner: str = Form(""),
    token: str = Form("", alias="_csrf"),
    tenant_id: int | None = Form(default=None),
):
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse("/app/sources", status_code=302)

    tenant_id = action_tenant_id(request, tenant_id)

    denied = guard_web(request, "source", "update", back="/app/sources")
    if denied is not None:
        return denied

    from app.core.tenant_context import tenant_scope

    with tenant_scope(tenant_id):
        source = await Source.objects.get(id=source_id, tenant_id=tenant_id)
        if source is None:
            add_flash(request, "error", "Источник не найден")
            return RedirectResponse("/app/sources", status_code=302)

        platform_row = await _resolve_platform(platform)
        if platform_row is None:
            add_flash(request, "error", f"Платформа '{platform}' не найдена")
            return RedirectResponse("/app/sources", status_code=302)

        st = SourceType.get_by_value(source_type)
        if st is None:
            add_flash(request, "error", f"Тип источника '{source_type}' не найден")
            return RedirectResponse("/app/sources", status_code=302)

        dup = await Source.objects.get(
            tenant_id=tenant_id,
            platform_id=platform_row.id,
            external_id=external_id,
        )
        if dup is not None and dup.id != source.id:
            add_flash(request, "error", "Источник с таким ID уже существует")
            return RedirectResponse("/app/sources", status_code=302)

        # Parse monitored users from comma-separated string into params
        params = _clean_params(monitored_users, source.params)
        if mode not in dict(COLLECTION_MODES):
            add_flash(request, "error", f"Неизвестный режим сбора: {mode}")
            return RedirectResponse("/app/sources", status_code=302)
        params["mode"] = mode

        # An empty box means "fall back to the workspace owner", so the key is
        # removed rather than written as None.
        owner_id, owner_error = _validate_token_owner(token_owner, {u.id for u in await _workspace_members(tenant_id)})
        if owner_error:
            add_flash(request, "error", owner_error)
            return RedirectResponse("/app/sources", status_code=302)
        if owner_id is None:
            params.pop("token_owner", None)
        else:
            params["token_owner"] = owner_id

        await Source.objects.update_by_id(
            source.id,
            name=name.strip()[:100] or source.name,
            platform_id=platform_row.id,
            external_id=external_id.strip()[:200],
            source_type=st,
            is_active=is_active == "on",
            params=params,
        )

    add_flash(request, "success", "Источник обновлён")
    return RedirectResponse("/app/sources", status_code=302)


@router.post("/{source_id}/toggle")
async def source_toggle(
    request: Request,
    source_id: int,
    token: str = Form("", alias="_csrf"),
    tenant_id: int | None = Form(default=None),
):
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse("/app/sources", status_code=302)

    tenant_id = action_tenant_id(request, tenant_id)

    denied = guard_web(request, "source", "update", back="/app/sources")
    if denied is not None:
        return denied

    from app.core.tenant_context import tenant_scope

    with tenant_scope(tenant_id):
        source = await Source.objects.get(id=source_id, tenant_id=tenant_id)
        if source is None:
            add_flash(request, "error", "Источник не найден")
            return RedirectResponse("/app/sources", status_code=302)

        await Source.objects.update_by_id(source.id, is_active=not source.is_active)
    action = "активирован" if not source.is_active else "деактивирован"
    add_flash(request, "success", f"Источник '{source.name}' {action}")
    return RedirectResponse("/app/sources", status_code=302)


@router.get("/{source_id}")
async def source_detail(request: Request, source_id: int):
    """One source: how it collects, what came out of it, what runs it.

    The list page answers "what do I have"; this one answers the two questions
    that used to require the DB — *why* is nothing collected from it, and
    *what did it produce*.
    """
    from app.core.tenant_context import tenant_scope
    from app.models.agent_task import AgentTask, agent_task_sources
    from app.models.ai_analytics import AIAnalytics

    user = getattr(request.state, "web_user", None)
    is_superuser = bool(user and user.is_superuser)
    filter_tenant_id, _tenants = await tenant_filter_context(request, is_superuser)

    with tenant_scope(bypass=True) if is_superuser else nullcontext():
        source = await Source.objects.filter(id=source_id).select_related("platform", "agent_scenario").first()
        # A superuser browsing all workspaces may open any source; anyone else is
        # confined to the workspace the middleware opened.
        if source is not None and not is_superuser and source.tenant_id != request.state.tenant_id:
            source = None
        if source is None:
            return render(
                request,
                "web/source_detail.html",
                section="sources",
                source=None,
                filter_tenant_id=filter_tenant_id,
            )

        analytics = await (
            AIAnalytics.objects.filter(source_id=source.id).order_by(AIAnalytics.created_at.desc()).limit(10)
        )
        analytics_count = (
            await AIAnalytics.objects.filter(source_id=source.id).values(func.count(AIAnalytics.id)).scalar(0)
        )
        cost_cents = await (
            AIAnalytics.objects.filter(source_id=source.id)
            .values(func.coalesce(func.sum(AIAnalytics.estimated_cost), 0))
            .scalar(0)
        )
        # Tasks that actually drive this source. The m2m row lives in
        # `agent_task_sources`, and the manager's kwarg lookup cannot express it:
        # `sources__id` splits on the last `__`, so `id` is parsed as a column
        # lookup and rejected. An explicit join keeps the read on the guarded
        # manager instead of a hand-built session, and the table is keyed by
        # (task_id, source_id) so no row can duplicate a task.
        # A task with no sources at all ("all active") is reported by the
        # readiness hint below.
        linked_tasks = AgentTask.objects.join(agent_task_sources).filter(agent_task_sources.c.source_id == source.id)
        schedules = await linked_tasks.order_by(AgentTask.created_at.desc())
        usage_count = (
            await AgentTask.objects.join(agent_task_sources)
            .filter(agent_task_sources.c.source_id == source.id)
            .values(func.count(func.distinct(AgentTask.id)))
            .scalar(0)
        )

    mode_key = (source.params or {}).get("mode", "pull")
    if mode_key not in dict(COLLECTION_MODES):
        # A row written before the mode existed, or by hand: show the default
        # rather than an empty badge.
        mode_key = "pull"

    recent = [_analysis_row(a) for a in analytics]
    scores = [row["sentiment"] for row in recent if row["sentiment"] is not None]
    sentiment_label = f"{sum(scores) / len(scores):.2f}" if scores else "—"

    members = {u.id: u for u in await _workspace_members(source.tenant_id)}
    owner_id = (source.params or {}).get("token_owner")
    token_owner = members.get(int(owner_id)) if str(owner_id or "").isdigit() else None
    if token_owner is not None:
        owner_label = f"{token_owner.username} (личный токен)"
        owner_hint = "Сбор идёт под аккаунтом этого участника workspace."
    elif members:
        owner_label = "Владелец workspace"
        owner_hint = "Явно не выбран — используется токен владельца. Можно задать явно."
    else:
        owner_label = "Не определён"
        owner_hint = "В workspace нет участников с веб-доступом — сбор пойдёт на переменные окружения."

    is_ready, readiness_hint = _readiness(source, schedules)

    return render(
        request,
        "web/source_detail.html",
        section="sources",
        source=source,
        filter_tenant_id=filter_tenant_id,
        mode=_mode_view(mode_key),
        mode_label=dict(COLLECTION_MODES)[mode_key],
        monitored_users=(source.params or {}).get("monitored_users") or [],
        owner_label=owner_label,
        owner_hint=owner_hint,
        token_owner=token_owner,
        window_label=_window_label(source),
        schedules=[_schedule_view(s) for s in schedules],
        usage_count=usage_count,
        can_delete=perms_can(request, "source", "delete"),
        recent=recent,
        analytics_count=analytics_count,
        cost_usd=float(cost_cents) / 100,
        sentiment_label=sentiment_label,
        last_checked_label=_last_checked_label(source.last_checked),
        is_ready=is_ready,
        readiness_hint=readiness_hint,
    )


def _mode_view(mode_key: str) -> dict:
    """The mode as the template needs it: a label, an explanation, and the
    `push` flag — a pushed source has neither a token owner nor a window."""
    return {
        "key": mode_key,
        "label": _MODE_LABELS[mode_key],
        "hint": dict(COLLECTION_MODES)[mode_key],
        "is_push": mode_key == "push",
    }


def _analysis_row(a) -> dict:
    """One analysis row, pre-formatted for the table."""
    from app.web.dashboard import _extract_sentiment_score

    cost = a.estimated_cost
    return {
        "analysis_date": a.analysis_date.isoformat() if a.analysis_date else "—",
        "main_topics": a.main_topics or [],
        "sentiment": _extract_sentiment_score(a.summary_data),
        "llm_model": a.llm_model,
        "cost_usd": float(cost) / 100 if cost is not None else None,
    }


def _schedule_view(task) -> dict:
    """A task as the schedule list needs it."""
    from app.tasks.cron import cron_to_human

    return {
        "name": task.name,
        "is_active": task.is_active,
        "cron_label": cron_to_human(task.cron_expr),
        "next_run_label": task.next_run_at.strftime("%d.%m %H:%M") if task.next_run_at else "—",
    }


def _window_label(source) -> str:
    """The collection window in one line; unbounded on either side reads «…»."""

    def fmt(value) -> str:
        return value.strftime("%d.%m.%Y") if value else "…"

    return f"с {fmt(source.date_from)} по {fmt(source.date_to)}"


def _last_checked_label(last_checked) -> str:
    return last_checked.strftime("%d.%m.%Y %H:%M") if last_checked else "не проверялся"


def _readiness(source, schedules) -> tuple[bool, str]:
    """Why nothing is coming out of this source — or True when nothing is wrong.

    The reasons are ordered as an operator would hit them, so the hint names
    the first thing to fix instead of listing candidates.
    """
    if not source.is_active:
        return False, "Источник выключен — сбор по нему не идёт."
    if not schedules:
        return False, "Ни одна задача не ссылается на этот источник — сбор не запускается."
    if not any(s.is_active for s in schedules):
        return False, "Все задачи по этому источнику на паузе."
    if (source.params or {}).get("mode", "pull") == "push":
        # Nothing to schedule: pushes arrive on the channel listener's own.
        return False, "Push-источник наполняет канал Telegram — проверьте, что бот подключён."
    return True, ""


@router.post("/{source_id}/collect")
async def source_collect_now(
    request: Request,
    source_id: int,
    token: str = Form("", alias="_csrf"),
    tenant_id: int | None = Form(default=None),
):
    """Enqueue a collect job for this source only — the UI's "try it now".

    Runs through the queue rather than inline: a collect can take minutes, and
    blocking an HTTP request on it would time the browser out. The result shows
    up in the source's job history.
    """
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse(f"/app/sources/{source_id}", status_code=302)

    tenant_id = action_tenant_id(request, tenant_id)

    # Collection costs LLM money and writes rows — the same right as running a
    # task, not the weaker "can edit the source row".
    denied = guard_web(request, "source", "analyze", back=f"/app/sources/{source_id}")
    if denied is not None:
        return denied

    from app.core.tenant_context import tenant_scope
    from app.models.managers.job_manager import JobManager

    with tenant_scope(tenant_id):
        source = await Source.objects.get(id=source_id, tenant_id=tenant_id)
        if source is None:
            add_flash(request, "error", "Источник не найден")
            return RedirectResponse("/app/sources", status_code=302)

        job = await JobManager().enqueue(job_type="collect", payload={"source_ids": [source.id]})

    add_flash(request, "success", f"Сбор по источнику '{source.name}' поставлен в очередь")
    return RedirectResponse(f"/app/sources/{source_id}?job_id={job.id}", status_code=302)


@router.post("/{source_id}/delete")
async def source_delete(
    request: Request,
    source_id: int,
    token: str = Form("", alias="_csrf"),
    tenant_id: int | None = Form(default=None),
):
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse("/app/sources", status_code=302)

    tenant_id = action_tenant_id(request, tenant_id)

    denied = guard_web(request, "source", "delete", back="/app/sources")
    if denied is not None:
        return denied

    from app.core.tenant_context import tenant_scope

    with tenant_scope(tenant_id):
        source = await Source.objects.get(id=source_id, tenant_id=tenant_id)
        if source is None:
            add_flash(request, "error", "Источник не найден")
            return RedirectResponse("/app/sources", status_code=302)

        # `sources.analytics` is delete-orphan, so the analysis rows go with it.
        # Say so before it happens rather than after.
        name = source.name
        await Source.objects.delete(id=source_id)

    add_flash(request, "success", f"Источник '{name}' удалён вместе с его аналитикой")
    return RedirectResponse("/app/sources", status_code=302)
