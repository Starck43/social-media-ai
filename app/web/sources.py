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
from sqlalchemy import func

from app.models.collected_item import CollectedItem
from app.models.source import Source
from app.services.social.connections import source_connection_status, source_connection_statuses
from app.types import SourceType

from .deps import (
    action_tenant_id,
    add_flash,
    ensure_csrf,
    guard_web,
    human_datetime,
    perms_can,
    plural,
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


def _connection_banners(sources, statuses: dict) -> list[dict]:
    """One entry per platform that blocks personal collection on this page.

    Computed from the sources actually on the page, so it stays silent while
    nothing needs a personal token and appears only when collection really is
    blocked — the sources page is where an operator looks when a source is
    quiet, so this is where a one-click fix belongs. A source may collect with a
    teammate's token, hence the owner's status rather than the caller's own.

    Every blocked platform gets an entry, not just the first: VK and Telegram
    break independently, and a banner that named only one of them sent people
    away to reconnect a platform that was already fine.

    Takes the mapping the list already built, so the badge column and the banner
    cost one pass of queries between them rather than two.
    """
    from app.services.social.connections import CONNECTIONS_BY_PLATFORM
    blocked: dict[str, dict] = {}
    for source in sources:
        status = statuses.get(source.id)
        if status is None or not status.needs_action:
            continue
        entry = blocked.setdefault(status.platform, {"status": status, "names": []})
        entry["names"].append(source.name or str(source.id))

    banners = []
    for platform, entry in blocked.items():
        spec = CONNECTIONS_BY_PLATFORM[platform]
        status = entry["status"]
        banners.append(
            {
                "platform": platform,
                "title": spec.title,
                "label": status.label,
                "detail": status.detail,
                "count": len(entry["names"]),
                "names": entry["names"][:3],
                # Only a platform with an OAuth flow can be fixed by clicking.
                "can_connect": status.can_connect,
                "is_oauth": spec.is_oauth,
            }
        )
    return banners


@router.get("")
@router.get("/")
async def sources_list(request: Request):
    user = getattr(request.state, "web_user", None)
    is_superuser = bool(user and user.is_superuser)
    filter_tenant_id, tenants = await tenant_filter_context(request, is_superuser)

    from app.core.tenant_context import tenant_scope

    if is_superuser:
        # Superuser sees all tenants' sources, optionally narrowed to one.
        # Bypass the tenant guard (the middleware already set a tenant scope) so
        # the manager query isn't silently scoped to the active workspace.
        with tenant_scope(bypass=True):
            query = Source.objects.select_related("platform", "tenant")
            if filter_tenant_id is not None:
                query = query.filter(tenant_id=filter_tenant_id)
            sources = await query.order_by(Source.created_at.desc())
    else:
        query = Source.objects.filter(tenant_id=request.state.tenant_id)
        sources = await query.select_related("platform").order_by(Source.created_at.desc())
    # The per-row badge and the banner are per-source (whose token is used), not
    # per-caller, so both are computed from the rows actually rendered above.
    # One pass, one set of queries: `_connection_banners` reads the same mapping.
    source_connections = await source_connection_statuses(sources)
    connection_banners = _connection_banners(sources, source_connections)

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
        source_connections=source_connections,
        connection_banners=connection_banners,
        is_superuser=is_superuser,
        tenants=tenants,
        filter_tenant_id=filter_tenant_id,
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

        # The workspace tier caps how many sources may exist. Checked here,
        # after the permission gate: a caller who may not create sources should
        # not learn the workspace's remaining quota from an error message.
        if tenant_id is not None:
            from app.models.managers.tenant_manager import tenants
            from app.services.tenancy.limits import check_source_limit

            tenant_row = await tenants.get(id=tenant_id)
            if tenant_row is not None:
                blocked = await check_source_limit(tenant_row)
                if blocked:
                    add_flash(request, "error", blocked)
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
    platform: str = Form(""),
    external_id: str = Form(""),
    source_type: str = Form(""),
    is_active: str = Form("", alias="is_active"),
    monitored_users: str = Form(""),
    mode: str = Form(""),
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

        # Platform and source_type: required for full edit, optional for quick edit.
        if platform:
            platform_row = await _resolve_platform(platform)
            if platform_row is None:
                add_flash(request, "error", f"Платформа '{platform}' не найдена")
                return RedirectResponse("/app/sources", status_code=302)
        else:
            platform_row = source.platform

        if source_type:
            st = SourceType.get_by_value(source_type)
            if st is None:
                add_flash(request, "error", f"Тип источника '{source_type}' не найден")
                return RedirectResponse("/app/sources", status_code=302)
        else:
            st = source.source_type

        # Only check duplicates if external_id changed.
        new_external_id = external_id.strip()[:200] or source.external_id
        if new_external_id != source.external_id:
            dup = await Source.objects.get(
                tenant_id=tenant_id,
                platform_id=platform_row.id,
                external_id=new_external_id,
            )
            if dup is not None and dup.id != source.id:
                add_flash(request, "error", "Источник с таким ID уже существует")
                return RedirectResponse("/app/sources", status_code=302)

        # Parse monitored users from comma-separated string into params.
        params = dict(source.params or {})
        params = _clean_params(monitored_users, params)

        # Mode: optional — keep existing if not posted.
        if mode:
            if mode not in dict(COLLECTION_MODES):
                add_flash(request, "error", f"Неизвестный режим сбора: {mode}")
                return RedirectResponse("/app/sources", status_code=302)
            params["mode"] = mode

        # An empty box means "fall back to the workspace owner", so the key is
        # removed rather than written as None.
        if token_owner or mode or platform or source_type:
            owner_id, owner_error = _validate_token_owner(
                token_owner, {u.id for u in await _workspace_members(tenant_id)}
            )
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
            external_id=new_external_id,
            source_type=st,
            is_active=is_active == "on",
            params=params,
        )

    add_flash(request, "success", "Источник обновлён")
    return RedirectResponse(f"/app/sources/{source_id}", status_code=302)


@router.post("/{source_id}/toggle")
async def source_toggle(
    request: Request,
    source_id: int,
    token: str = Form("", alias="_csrf"),
    tenant_id: int | None = Form(default=None),
):
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse(f"/app/sources/{source_id}", status_code=302)

    tenant_id = action_tenant_id(request, tenant_id)

    denied = guard_web(request, "source", "update", back=f"/app/sources/{source_id}")
    if denied is not None:
        return denied

    from app.core.tenant_context import tenant_scope

    with tenant_scope(tenant_id):
        source = await Source.objects.get(id=source_id, tenant_id=tenant_id)
        if source is None:
            add_flash(request, "error", "Источник не найден")
            return RedirectResponse(f"/app/sources/{source_id}", status_code=302)

        await Source.objects.update_by_id(source.id, is_active=not source.is_active)
    action = "активирован" if not source.is_active else "деактивирован"
    add_flash(request, "success", f"Источник '{source.name}' {action}")
    return RedirectResponse(f"/app/sources/{source_id}", status_code=302)


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
        source = (
            await Source.objects.filter(id=source_id).select_related("platform", "tenant").first()
        )
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

    # Check whether the current user IS the workspace owner.
    user_is_owner = False
    if user is not None and source.tenant_id is not None:
        from app.models.tenant import TenantUser

        membership = await TenantUser.objects.filter(
            tenant_id=source.tenant_id, user_id=user.id, is_active=True
        ).first()
        user_is_owner = membership is not None and membership.is_owner

    if token_owner is not None:
        owner_label = f"{token_owner.username} (личный токен)"
        owner_hint = "Сбор идёт под аккаунтом для текущего пространства пользователя"
    elif user_is_owner:
        owner_label = "Вы (владелец workspace)"
        owner_hint = "Токен владельца используется по умолчанию. Можно задать явно для другого участника."
    elif members:
        owner_label = "Владелец workspace"
        owner_hint = "Явно не выбран — используется токен владельца. Можно задать явно."
    else:
        owner_label = "Не определён"
        owner_hint = "В этом пространстве нет пользователя с веб-доступом."

    is_ready, readiness_hint = await _readiness(source, schedules)

    # The owner's own connection, shown in "Чей токен" — the exact place an
    # operator looks when one source is silent while the rest work.
    owner_connection = await source_connection_status(source)

    platforms = await _platforms()
    source_types_list = list(SourceType)

    # What the collect jobs actually pulled from *this* source. `jobs.result`
    # carries a per-source breakdown (`per_source`); older rows written before it
    # existed only have the run-wide totals, so those are reported as unknown
    # rather than as zero.
    collections = await _recent_collections(source.id)

    # Content staged for analysis that the model could not process. Rows leave
    # the staging table only once an analysis stores them, so one that keeps
    # failing is retried until it burns `give_up_after_attempts` and then stops
    # being offered. Without surfacing the count, that day simply vanishes from
    # the source with no trace — the ceiling is doing its job, but silently.
    given_up = await CollectedItem.objects.exhausted_count(source.id)
    given_up_label = plural(given_up, "запись", "записи", "записей")

    # Check whether a collect or analyze job is currently running for this source.
    is_running = await _source_has_running_job(source.id)

    return render(
        request,
        "web/source_detail.html",
        section="sources",
        source=source,
        given_up=given_up,
        given_up_label=given_up_label,
        filter_tenant_id=filter_tenant_id,
        mode=_mode_view(mode_key),
        mode_label=dict(COLLECTION_MODES)[mode_key],
        monitored_users=(source.params or {}).get("monitored_users") or [],
        owner_label=owner_label,
        owner_hint=owner_hint,
        owner_connection=owner_connection,
        token_owner=token_owner,
        schedules=[_schedule_view(s) for s in schedules],
        usage_count=usage_count,
        can_delete=perms_can(request, "source", "delete"),
        recent=recent,
        analytics_count=analytics_count,
        cost_usd=float(cost_cents) / 100,
        sentiment_label=sentiment_label,
        last_checked_label=_last_checked_label(source.last_checked),
        collections=collections,
        is_ready=is_ready,
        readiness_hint=readiness_hint,
        platforms=platforms,
        source_types=source_types_list,
        is_running=is_running,
    )
async def _source_has_running_job(source_id: int) -> bool:
    """Whether a collect/analyze job is currently running for this source."""
    from app.models.job import Job

    running = await Job.objects.filter(status="running")
    for job in running:
        payload = job.payload or {}
        source_ids = payload.get("source_ids", [])
        if source_id in source_ids:
            return True
    return False


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
        "id": a.id,
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
        "next_run_label": human_datetime(task.next_run_at),
    }


def _window_label(source) -> str:
    """The collection window in one line; unbounded on either side reads «…»."""

    def fmt(value) -> str:
        # A window is a calendar range, so keep the plain date even when the
        # shared filter would say «сегодня» — the bounds matter more than recency.
        return value.strftime("%d.%m.%Y") if value else "…"

    return f"с {fmt(source.date_from)} по {fmt(source.date_to)}"


def _last_checked_label(last_checked) -> str:
    return human_datetime(last_checked, empty="не проверялся")


# How many collect runs to scan for a source's history. Deep enough to show a
# weekly rhythm; the queue page (`/app/jobs`) is the place to debug the schedule
# itself, so this is a history strip, not a log viewer.
COLLECTION_HISTORY_LIMIT = 8
# Raw items shown per unanalysed run in «Что собрано». Enough to recognise what
# came back without turning the history table into a wall of text; the permalink
# next to each line is the way to read the rest.
RAW_PREVIEW_LIMIT = 5
RAW_PREVIEW_CHARS = 160


async def _recent_collections(source_id: int) -> list[dict]:
    """Recent collect runs for one source, newest first, with what each yielded.

    The collect handler records a per-source breakdown in `jobs.result`
    (`per_source`), keyed by source id — that is the only place the number of
    items a *specific* source produced survives, because `result["items"]` is a
    single total for the whole run. Jobs written before that breakdown existed
    have no entry for this source and are skipped rather than shown as zero: "we
    do not know" and "nothing was collected" are different answers.
    """
    from app.models.job import Job

    rows = (
        await Job.objects.filter(job_type="collect")
        .order_by(Job.created_at.desc())
        .limit(COLLECTION_HISTORY_LIMIT * 10)
    )

    out: list[dict] = []
    for job in rows:
        result = job.result if isinstance(job.result, dict) else {}
        for entry in result.get("per_source") or []:
            if entry.get("source_id") != source_id:
                continue
            out.append(
                {
                    "job_id": job.id,
                    "when": human_datetime(job.created_at),
                    # `items` is what the platform returned, `new_items` what of it
                    # was unseen. Absent on jobs written before the counter existed
                    # — None renders as "—" rather than a fabricated zero.
                    "items": int(entry.get("items") or 0),
                    "new_items": None if entry.get("new_items") is None else int(entry["new_items"]),
                    "analyzed": int(entry.get("analyzed") or 0),
                    "outcome": entry.get("outcome") or "empty",
                    "failed": job.status == "failed",
                    # Filled in below, once every run id is known. Both are
                    # seeded so the template never compares an undefined value.
                    "preview": [],
                    "staged_total": 0,
                }
            )
            break
        if len(out) >= COLLECTION_HISTORY_LIMIT:
            break

    await _attach_raw_previews(out)
    return out


async def _attach_raw_previews(collections: list[dict]) -> None:
    """Give each still-unanalysed run its raw items, ready to show.

    Only rows still in `collected_items` are visible here — they are the runs
    whose content nobody has analysed yet. Once an analysis stores the matching
    items those rows are retired, and the run falls back to counters alone,
    which is the intended behaviour: what was collected stays on record as
    numbers, the text is only kept while something might still need it.
    """
    if not collections:
        return
    from app.models import CollectedItem

    run_ids = [c["job_id"] for c in collections]
    try:
        rows = await CollectedItem.objects.filter(run_id__in=run_ids).order_by(CollectedItem.published_at.asc())
    except Exception:
        return

    grouped: dict[int, list] = {}
    for row in rows:
        grouped.setdefault(row.run_id, []).append(row)

    for entry in collections:
        staged = grouped.get(entry["job_id"], [])
        entry["preview"] = [
            {
                "when": human_datetime(row.published_at) if row.published_at else None,
                "text": _preview(row.text),
                "link": row.permalink,
            }
            for row in staged[:RAW_PREVIEW_LIMIT]
        ]
        entry["staged_total"] = len(staged)


def _preview(text: str | None, limit: int = RAW_PREVIEW_CHARS) -> str:
    """A readable one-line excerpt, with long posts cut at a word boundary."""
    body = " ".join((text or "").split())
    if not body:
        return "(без текста)"
    if len(body) <= limit:
        return body
    cut = body[:limit]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" .,;:!?—-") + " …"


async def _readiness(source, schedules) -> tuple[bool, str]:
    """Why nothing is coming out of this source — or True when nothing is wrong.

    The reasons are ordered as an operator would hit them, so the hint names
    the first thing to fix instead of listing candidates. A missing personal
    token comes first among the *collection* reasons: without it the source can
    only be read with the community token, which is exactly the case where it
    looks alive but silently returns nothing.
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

    # An L2 source whose owner never authorized reads as "collected, empty"
    # forever; saying so here is cheaper than discovering it in a digest.
    status = await source_connection_status(source)
    if status is not None and status.needs_action:
        return False, f"{status.label}: {status.detail.lower()} — личный доступ нужен для сбора."
    return True, ""


@router.post("/{source_id}/collect")
async def source_collect_now(
    request: Request,
    source_id: int,
    token: str = Form("", alias="_csrf"),
    tenant_id: int | None = Form(default=None),
):
    """Collect this source right now — inline, not through the queue.

    This used to `enqueue` and leave the job `pending`, so "Собрать сейчас" only
    *requested* a collection: the page redirected with a `job_id` that nothing on
    it could render (the run modal lives on the tasks page), and the work happened
    whenever a worker happened to be free. The button now runs the job here, so
    the outcome is known before the response is sent and can be reported plainly.

    A single-source collect is quick enough for this: it is one source, not a
    whole workspace's worth.
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
    from app.jobs.dispatcher import run_job_inline

    with tenant_scope(tenant_id):
        source = await Source.objects.get(id=source_id, tenant_id=tenant_id)
        if source is None:
            add_flash(request, "error", "Источник не найден")
            return RedirectResponse("/app/sources", status_code=302)

        outcome = await run_job_inline("collect", {"source_ids": [source.id]})

    name = source.name
    if not outcome:
        add_flash(request, "error", f"Сбор по источнику «{name}» не удалось запустить")
    elif outcome.get("status") == "failed":
        add_flash(request, "error", f"Сбор по источнику «{name}» не удался: {outcome.get('error') or '?'}")
    else:
        items = (outcome.get("result") or {}).get("items", 0)
        add_flash(request, "success", f"Сбор по источнику «{name}» завершён: записей — {items}")
    # No `?job_id=` here: the run modal lives on the tasks page, so the parameter
    # would only produce a URL that renders nothing. The flash carries the result
    # and the «Что собрано» block on this very page now shows the run.
    return RedirectResponse(f"/app/sources/{source_id}", status_code=302)


@router.post("/{source_id}/analyze")
async def source_analyze_now(
    request: Request,
    source_id: int,
    token: str = Form("", alias="_csrf"),
    tenant_id: int | None = Form(default=None),
):
    """Analyze staged collected items for this source right now.

    Enqueues an `analyze` job so the background worker drains the
    `collected_items` queue through the same `handle_analyze` path.
    Exhausted rows (burned `give_up_after_attempts`) are reset so the
    operator can retry after fixing the model or raising the ceiling.
    """
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse(f"/app/sources/{source_id}", status_code=302)

    tenant_id = action_tenant_id(request, tenant_id)

    denied = guard_web(request, "source", "analyze", back=f"/app/sources/{source_id}")
    if denied is not None:
        return denied

    from app.core.database import new_session
    from app.core.tenant_context import tenant_scope
    from app.models.managers.job_manager import JobManager

    with tenant_scope(tenant_id):
        source = await Source.objects.get(id=source_id, tenant_id=tenant_id)
        if source is None:
            add_flash(request, "error", "Источник не найден")
            return RedirectResponse("/app/sources", status_code=302)

        # Reset attempts on exhausted rows so they are offered again.
        reset_count = 0
        session = new_session()
        try:
            async with session.begin():
                reset_count = await CollectedItem.objects.reset_attempts(session, source_id)
        except Exception:
            pass
        finally:
            await session.close()

        # Enqueue — the worker loop picks it up; no HTTP timeout.
        await JobManager().enqueue(
            job_type="analyze",
            payload={"source_ids": [source.id]},
            tenant_id=tenant_id,
        )

    name = source.name
    if reset_count:
        add_flash(request, "info", f"Сброшено {reset_count} записей(ей) из «досрочного выхода» — будут проанализированы заново")
    add_flash(request, "success", f"Задача анализа по источнику «{name}» поставлена в очередь")
    return RedirectResponse(f"/app/sources/{source_id}", status_code=302)


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
