"""Sources CRUD for `/app/sources` (docs/API.md).

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

import logging
from contextlib import nullcontext
from typing import Any

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
logger = logging.getLogger(__name__)


# Collection modes a source can run in. `user` is not a layer of its own — it is
# the L2 variant of the platform client, chosen by `owner.resolve_source_owner`.
COLLECTION_MODES: tuple[tuple[str, str], ...] = (
    ("pull", "Через API платформы — токен сообщества, стабильно, с пагинацией"),
    ("push", "Через бот-API — источник сам присылает обновления (Telegram)"),
    ("user", "Через личный аккаунт — токен участника workspace (медленно, выше лимиты)"),
)

# Short names for the badges; `COLLECTION_MODES` carries the long explanation.
_MODE_LABELS: dict[str, str] = {"pull": "API платформы", "push": "Бот-API", "user": "Личный аккаунт"}


def _clean_params(base: dict | None = None) -> dict:
    """A copy of the source's collection params for editing.

    Whose items are analysed is a task property (`tracked_users`), never a source
    property (`docs/COLLECTION.md#task-selection-contract`), so nothing author-related is
    written here.
    """
    return dict(base or {})


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


async def _delete_blockers(sources: list[Any], lane_tasks: dict[int, Any]) -> dict[int, str]:
    """Why each row's delete control is disabled, keyed by source id.

    Active explicit readers block deletion, as does a paused task that would
    lose its only explicit source. A task-owned lane blocks only while its
    owner is active; a paused owner alone leaves a deletable orphan.
    """
    from app.core.tenant_context import tenant_scope
    from app.models.managers.agent_task_manager import AgentTaskManager

    ids_by_tenant: dict[int, list[int]] = {}
    for source in sources:
        ids_by_tenant.setdefault(int(source.tenant_id), []).append(int(source.id))

    dependencies_by_source: dict[int, list[Any]] = {}
    for tenant_id, ids in ids_by_tenant.items():
        with tenant_scope(tenant_id):
            dependencies_by_source.update(await AgentTaskManager().delete_blockers_for_sources(ids, tenant_id))

    blockers: dict[int, str] = {}
    for source in sources:
        tasks = dependencies_by_source.get(int(source.id), [])
        owner = lane_tasks.get(source.owner_task_id) if source.owner_task_id else None
        active_tasks = [task for task in tasks if task.is_active]
        paused_last_source_tasks = [task for task in tasks if not task.is_active]
        if source.owner_task_id is not None and owner is None:
            blockers[source.id] = (
                "Не удалось проверить владельца и связи задачи; восстановите или удалите задачу "
                "перед удалением источника"
            )
            continue
        if owner is not None and owner.is_active:
            active_tasks = [owner, *[task for task in active_tasks if task.id != owner.id]]

        reasons = []
        if active_tasks:
            named = ", ".join(f"№{task.id} «{task.name}»" for task in active_tasks)
            action = "удалите источник в настройках задачи"
            if source.owner_task_id and owner is not None and owner.is_active:
                action = "остановите поиск или удалите источник в задаче"
            reasons.append(f"Используется активной задачей {named} — {action}")
        if paused_last_source_tasks:
            named = ", ".join(f"№{task.id} «{task.name}»" for task in paused_last_source_tasks)
            reasons.append(
                f"Это единственный источник приостановленной задачи {named} — назначьте ей другой источник или удалите задачу"
            )
        if reasons:
            blockers[source.id] = ". ".join(reasons)
    return blockers


@router.get("")
@router.get("/")
async def sources_list(request: Request):
    user = getattr(request.state, "web_user", None)
    is_superuser = False
    filter_tenant_id, tenants = await tenant_filter_context(request, is_superuser)

    from app.core.tenant_context import tenant_scope

    if is_superuser:
        # Superuser sees all tenants' sources, optionally narrowed to one.
        # Bypass the tenant guard (the middleware already set a tenant scope) so
        # the manager query isn't silently scoped to the active workspace.
        # Task-owned lanes are listed too: hiding them made the page show fewer
        # sources than a task actually reads.
        with tenant_scope(bypass=True):
            query = Source.objects.select_related("platform", "tenant")
            if filter_tenant_id is not None:
                query = query.filter(tenant_id=filter_tenant_id)
            sources = await query.order_by(Source.created_at.desc())
    else:
        sources = await (
            Source.objects.filter(tenant_id=request.state.tenant_id)
            .select_related("platform")
            .order_by(Source.created_at.desc())
        )
    # The per-row badge and the banner are per-source (whose token is used), not
    # per-caller, so both are computed from the rows actually rendered above.
    # One pass, one set of queries: `_connection_banners` reads the same mapping.
    source_connections = await source_connection_statuses(sources)
    connection_banners = _connection_banners(sources, source_connections)
    stale_sources = await _stale_sources_summary(request, sources)

    # A lane row (`owner_task_id`) names its owning task: the marker on the row
    # explains why a source nobody added is on the page and why it is deleted
    # in its task rather than here.
    lane_tasks: dict[int, Any] = {}
    lane_task_ids = sorted({source.owner_task_id for source in sources if source.owner_task_id})
    if lane_task_ids:
        from app.models.managers.agent_task_manager import AgentTaskManager

        if is_superuser:
            with tenant_scope(bypass=True):
                owners = await AgentTaskManager().filter(id__in=lane_task_ids)
        else:
            owners = await AgentTaskManager().filter(id__in=lane_task_ids, tenant_id=request.state.tenant_id)
        lane_tasks = {task.id: task for task in owners}

    delete_blockers = await _delete_blockers(sources, lane_tasks)

    user_sources = await (
        Source.objects.filter(
            tenant_id=request.state.tenant_id,
            source_type=SourceType.USER,
            owner_task_id__isnull=True,
        )
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
        lane_tasks=lane_tasks,
        delete_blockers=delete_blockers,
        stale_sources=stale_sources,
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

        params: dict = _clean_params()
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

    submitted_form = await request.form()
    token_owner_was_posted = "token_owner" in submitted_form

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

        # Recheck uniqueness when either half of the platform identifier changes.
        new_external_id = external_id.strip()[:200] or source.external_id
        if new_external_id != source.external_id or platform_row.id != source.platform_id:
            dup = await Source.objects.get(
                tenant_id=tenant_id,
                platform_id=platform_row.id,
                external_id=new_external_id,
            )
            if dup is not None and dup.id != source.id:
                add_flash(request, "error", "Источник с таким ID уже существует")
                return RedirectResponse("/app/sources", status_code=302)

        # Selection is not a source property; saving drops the removed key.
        params = dict(source.params or {})
        params = _clean_params(params)

        # Mode: optional — keep existing if not posted.
        if mode:
            if mode not in dict(COLLECTION_MODES):
                add_flash(request, "error", f"Неизвестный режим сбора: {mode}")
                return RedirectResponse("/app/sources", status_code=302)
            params["mode"] = mode

        # An empty box means "fall back to the workspace owner", so the key is
        # removed rather than written as None.
        if token_owner_was_posted:
            members = await _workspace_members(tenant_id)
            member_ids = {u.id for u in members}
            owner_id, owner_error = _validate_token_owner(token_owner, member_ids)
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
    """Show source settings, source-level statistics, and linked tasks."""
    from app.core.tenant_context import tenant_scope
    from app.models.ai_analytics import AIAnalytics
    from app.web.tasks import _diagnostics_admin

    is_superuser = False
    filter_tenant_id, _tenants = await tenant_filter_context(request, is_superuser)

    with tenant_scope(bypass=True) if is_superuser else nullcontext():
        source = await Source.objects.filter(id=source_id).select_related("platform", "tenant").first()
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
        analytics_count = await (
            AIAnalytics.objects.filter(source_id=source.id).values(func.count(AIAnalytics.id)).scalar(0)
        )
        cost_cents = await (
            AIAnalytics.objects.filter(source_id=source.id)
            .values(func.coalesce(func.sum(AIAnalytics.estimated_cost), 0))
            .scalar(0)
        )

    from app.models.managers.agent_task_manager import AgentTaskManager

    can_view_tasks = perms_can(request, "agenttask", "view")
    schedules = []
    task_models = []
    effective_schedules = {}
    if can_view_tasks:
        task_manager = AgentTaskManager()
        with tenant_scope(source.tenant_id):
            task_models = await task_manager.for_source(source.id, source.tenant_id, source_is_active=source.is_active)
            workspace_active_source_ids = await task_manager.get_workspace_active_source_ids()
            effective_schedules = await task_manager.effective_active_map(task_models, workspace_active_source_ids)
        schedules = [_schedule_view(task, effective_schedules.get(task.id, False)) for task in task_models]
    # Keep detail and list deletion hints on the same batched dependency query.
    lane_tasks = {}
    if source.owner_task_id is not None:
        with tenant_scope(source.tenant_id):
            owner_task = await AgentTaskManager().get(id=source.owner_task_id, tenant_id=source.tenant_id)
        if owner_task is not None:
            lane_tasks[owner_task.id] = owner_task
    delete_blocker = (await _delete_blockers([source], lane_tasks)).get(source.id)
    is_ready, readiness_hint = await _readiness(
        source,
        task_models if can_view_tasks else None,
        effective_schedules if can_view_tasks else None,
    )

    mode_key = (source.params or {}).get("mode", "pull")
    if mode_key not in dict(COLLECTION_MODES):
        mode_key = "pull"
    recent = [_analysis_row(analysis) for analysis in analytics]
    scores = [row["sentiment"] for row in recent if row["sentiment"] is not None]
    sentiment_label = f"{sum(scores) / len(scores):.2f}" if scores else "—"

    members = await _workspace_members(source.tenant_id)
    member_map = {member.id: member for member in members}
    raw_owner_id = (source.params or {}).get("token_owner")
    owner_id = int(raw_owner_id) if str(raw_owner_id or "").isdigit() else None
    token_owner = member_map.get(owner_id)
    if token_owner:
        owner_label = f"{token_owner.username} (личный токен)"
    elif owner_id is not None:
        owner_label = f"Сохранённый участник #{owner_id}"
    else:
        owner_label = "Владелец workspace"
    owner_connection = await source_connection_status(source)

    with tenant_scope(source.tenant_id):
        pending_count = await (
            CollectedItem.objects.filter(source_id=source.id).values(func.count(CollectedItem.id)).scalar(0)
        )
        given_up = await CollectedItem.objects.exhausted_count(source.id)

    return render(
        request,
        "web/source_detail.html",
        section="sources",
        source=source,
        filter_tenant_id=filter_tenant_id,
        mode=_mode_view(mode_key),
        modes=COLLECTION_MODES,
        mode_labels=_MODE_LABELS,
        members=members,
        token_owner=token_owner,
        orphaned_owner_id=owner_id if owner_id is not None and token_owner is None else None,
        owner_label=owner_label,
        owner_connection=owner_connection,
        schedules=schedules,
        can_view_tasks=can_view_tasks,
        delete_blocker=delete_blocker,
        can_update=perms_can(request, "source", "update"),
        can_delete=perms_can(request, "source", "delete"),
        can_delete_unprocessed=_diagnostics_admin(request),
        is_superuser=is_superuser,
        is_ready=is_ready,
        readiness_hint=readiness_hint,
        pending_count=pending_count,
        given_up=given_up,
        analytics_count=analytics_count,
        recent_count=len(recent),
        sentiment_label=sentiment_label,
        cost_usd=float(cost_cents) / 100,
        last_checked_label=_last_checked_label(source.last_checked),
        platforms=await _platforms(),
        source_types=list(SourceType),
    )


@router.get("/{source_id}/analysis-progress")
async def source_analysis_progress(request: Request, source_id: int):
    """Small polling fragment for the existing recent-analyses table."""
    from fastapi.responses import HTMLResponse
    from app.core.tenant_context import tenant_scope
    from app.models.managers.job_manager import JobManager

    if not perms_can(request, "source", "view"):
        return HTMLResponse("", status_code=403)
    tenant_id = getattr(request.state, "tenant_id", None)
    source = await Source.objects.filter(id=source_id, tenant_id=tenant_id).first()
    if source is None:
        return HTMLResponse("", status_code=404)
    with tenant_scope(tenant_id):
        jobs = await JobManager().source_progress_jobs(source_id, limit=1)
    from .analysis_progress import project_job
    from .deps import templates

    latest_progress = project_job(jobs[0], source_id) if jobs else None
    # Admin-only allowlisted diagnostics are rendered only for a valid workspace
    # admin membership or a platform admin; normal fragments carry nothing.
    diagnostics = None
    if latest_progress is not None:
        from .tasks import _diagnostics_admin, _diagnostics_view

        if _diagnostics_admin(request):
            view = _diagnostics_view(jobs[0], source_id=source_id)
            diagnostics = _source_run_diagnostics(view, await _run_llm_summary(source_id, jobs[0]))
    return HTMLResponse(
        templates.env.get_template("web/source_analysis_progress.html").render(
            latest_progress=latest_progress, diagnostics=diagnostics
        )
    )


async def _source_has_running_job(source_id: int) -> dict:
    """Whether a collect or analyze job is queued or running for this source.

    Returns ``{"collect": bool, "analyze": bool}`` so the template can show
    the right label — "Сбор…" vs "Анализ…" — instead of a generic "Выполняется".
    """
    from app.models.managers.job_manager import JobManager

    return await JobManager().source_activity(source_id)


def _source_run_diagnostics(view: dict[str, Any], run: dict[str, Any] | None) -> dict[str, Any]:
    """The source panel's diagnostics dict, shaped for display.

    The shared job-modal diagnostics keep launch bookkeeping (job id, attempt,
    heartbeat, progress timestamps); the panel repeats none of it. An operator
    asked about the screenshot case wants the model that served the run and
    the survived/failed call counts — all journal-derived, allowlisted and
    bounded, with unknowns preserved as ``None``. The error code prefers the
    journal outcome and falls back to the run's own per-source code: a run
    whose dispatch died before any journal row (transport refusal) must still
    show why.
    """
    summary = run if isinstance(run, dict) else {}
    view_code = view.get("error_code")
    code = summary.get("error_code") or (view_code if isinstance(view_code, str) and view_code.isidentifier() else None)
    return {
        "selected": view.get("selected"),
        "remaining": view.get("remaining"),
        "model": summary.get("model"),
        "provider": summary.get("provider"),
        "succeeded": summary.get("succeeded"),
        "failed": summary.get("failed"),
        "error_code": code,
    }


async def _run_llm_summary(source_id: int, job: Any) -> dict[str, Any] | None:
    """Model/use counters behind one run, from the immutable attempt journal.

    The window is the run itself: ``started_at`` (fallback ``created_at`` for
    a job nobody claimed yet) through ``finished_at``, leaving an open end for
    running jobs so counters grow live between polls. Read-only and bounded;
    any failure stays ``None`` — diagnostics must never fail the page.
    """
    from datetime import datetime, timezone

    started = getattr(job, "started_at", None) or getattr(job, "created_at", None)
    if not isinstance(started, datetime):
        return None
    started = started if started.tzinfo else started.replace(tzinfo=timezone.utc)
    finished = getattr(job, "finished_at", None)
    until = finished if isinstance(finished, datetime) else None
    from app.models.managers.llm_attempt_manager import LLMAttemptManager

    try:
        return await LLMAttemptManager().run_summary(source_id, since=started, until=until)
    except Exception as error:  # noqa: BLE001 — diagnostics must never fail the page
        # Bounded kind only: exception text stays out of logs and responses.
        from app.jobs.handlers import _error_kind

        logger.warning("source_run_summary_failed source_id=%s error_kind=%s", source_id, _error_kind(error))
        return None


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
    from app.services.ai.analysis_render import render_analysis

    display = render_analysis(a.summary_data)
    cost = a.estimated_cost
    return {
        "id": a.id,
        "analysis_date": a.analysis_date.strftime("%d.%m.%Y") if a.analysis_date else "—",
        "main_topics": a.main_topics or [],
        "sentiment": display["sentiment"]["score"],
        "analysis_incomplete": display["coverage"]["incomplete"],
        "llm_model": a.llm_model,
        "cost_usd": float(cost) / 100 if cost is not None else None,
    }


def _schedule_view(task, effective_active: bool | None = None) -> dict:
    """A task as the schedule list needs it."""
    from app.tasks.cron import cron_to_human

    effective = task.is_active if effective_active is None else effective_active
    return {
        "id": task.id,
        "name": task.name,
        "is_active": task.is_active,
        "effective_active": effective,
        "cron_label": cron_to_human(task.cron_expr),
        "next_run_label": human_datetime(task.next_run_at) if effective else "не запланирован",
    }


def _window_label(source) -> str:
    """The collection window in one line; unbounded on either side reads «…»."""

    def fmt(value) -> str:
        # A window is a calendar range, so keep the plain date even when the
        # shared filter would say «сегодня» — the bounds matter more than recency.
        return value.strftime("%d.%m.%Y") if value else "…"

    return f"с {fmt(source.date_from)} по {fmt(source.date_to)}"


async def _resolve_source_window(source) -> dict:
    """The content window (cli_dates) for a forced source-page run.

    Forced runs («Собрать сейчас» / «Выполнить анализ») have no task context, so
    the window is resolved from the source's linked task payload first, then the
    source's own params — the same priority the handlers use. Returns a dict
    suitable for merging into a job payload (`{"cli_dates": {...}}` or `{}`).
    """
    from app.models.managers.agent_task_manager import AgentTaskManager

    linked = await AgentTaskManager().for_source(
        source.id,
        source.tenant_id,
        source_is_active=source.is_active,
        include_all_active=False,
    )
    selected = linked[0] if linked else None
    cli_dates = (selected.payload or {}).get("cli_dates") if selected else None
    scope_label = f"по задаче «{selected.name}»" if selected and cli_dates else "по настройкам источника"
    if not cli_dates:
        cli_dates = (source.params or {}).get("cli_dates")
    if len(linked) > 1 and selected and cli_dates:
        scope_label += " (использована наиболее новая из связанных задач)"
    return {"cli_dates": cli_dates, "scope_label": scope_label} if cli_dates else {"scope_label": scope_label}


def _last_checked_label(last_checked) -> str:
    return human_datetime(last_checked, empty="не проверялся")


def _stale_window_hours() -> int:
    """Configured freshness window for an active source, in hours."""
    from app.core.config import settings

    return int(getattr(settings, "SOURCE_STALE_HOURS", 24))


async def _stale_sources_summary(request: Request, sources: list) -> dict | None:
    """Stale subset of the rows this page renders, or None without `source.view`.

    `SourceManager.get_sources_for_monitoring` is the one staleness predicate —
    active, and never checked or checked before `SOURCE_STALE_HOURS` — so the
    page cannot drift from what the rest of the system calls "needs
    monitoring". It answers for the whole visible tenant, so its ids are
    intersected with the rendered rows: a superuser page counts the sources it
    actually lists, not every workspace's.

    A source that was never checked is counted and named separately. Filing it
    under "stale" would claim a check that never happened; calling it "fresh"
    because there is no timestamp to compare is the opposite lie.

    Task-owned query lanes (`owner_task_id`) are left out: their runs follow the
    owning task's schedule, so a paused or weekly task cannot raise a 24h alert
    here. Lanes stay visible on the page itself.
    """
    if not perms_can(request, "source", "view"):
        return None

    from app.models.managers.source_manager import SourceManager

    hours = _stale_window_hours()
    ordinary = [row for row in sources if getattr(row, "owner_task_id", None) is None]
    stale_ids = {row.id for row in await SourceManager().get_sources_for_monitoring(hours_since_check=hours)}
    rows = [row for row in ordinary if row.id in stale_ids]
    active_total = sum(1 for row in ordinary if row.is_active)
    never_ids = frozenset(row.id for row in rows if row.last_checked is None)
    return {
        "hours": hours,
        "count": len(rows),
        "active_total": active_total,
        "never": len(never_ids),
        "ids": frozenset(row.id for row in rows),
        "never_ids": never_ids,
    }


def _window_kwargs(bounds: tuple) -> dict:
    """Content-window kwargs for manager queries; Nones stay out (no bound)."""
    return {key: value for key, value in zip(("start_date", "end_date"), bounds) if value is not None}


def _window_human_label(bounds: tuple) -> str:
    """«с 01.09.2026» / «с 01.09.2026 по 17.10.2026» for the window mention."""
    start, end = bounds
    if start is None and end is None:
        return ""
    if start and end:
        return f"с {start:%d.%m.%Y} по {end:%d.%m.%Y}"
    if start:
        return f"с {start:%d.%m.%Y}"
    return f"до {end:%d.%m.%Y}"


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
    """Recent collection stages for one source, newest first, with what each yielded.

    The collect handler records a per-source breakdown in `jobs.result`
    (`per_source`), keyed by source id — that is the only place the number of
    items a *specific* source produced survives, because `result["items"]` is a
    single total for the whole run. Unified analyze jobs carry a nested
    `collection` entry; standalone analysis jobs are ignored. Jobs written
    before the breakdown existed are skipped rather than shown as zero.
    """
    from app.models.job import Job

    rows = (
        await Job.objects.filter(job_type__in=("collect", "analyze"))
        .order_by(Job.created_at.desc())
        .limit(COLLECTION_HISTORY_LIMIT * 10)
    )

    out: list[dict] = []
    for job in rows:
        result = job.result if isinstance(job.result, dict) else {}
        for entry in result.get("per_source") or []:
            if entry.get("source_id") != source_id:
                continue
            collection = entry.get("collection") if isinstance(entry.get("collection"), dict) else None
            if job.job_type == "analyze" and collection is None:
                continue
            collection = collection or entry
            out.append(
                {
                    "job_id": job.id,
                    "when": human_datetime(job.created_at),
                    # `items` is what the platform returned, `new_items` what of it
                    # was unseen. Absent on jobs written before the counter existed
                    # — None renders as "—" rather than a fabricated zero.
                    "items": int(collection.get("items") or 0),
                    "new_items": None if collection.get("new_items") is None else int(collection["new_items"]),
                    "analyzed": int(entry.get("analyzed") or collection.get("analyzed") or 0),
                    "outcome": collection.get("outcome") or entry.get("outcome") or "empty",
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


async def _readiness(source, schedules, effective_schedules=None) -> tuple[bool, str]:
    """Why nothing is coming out of this source — or True when nothing is wrong.

    The reasons are ordered as an operator would hit them, so the hint names
    the first thing to fix instead of listing candidates. A missing personal
    token comes first among the *collection* reasons: without it the source can
    only be read with the community token, which is exactly the case where it
    looks alive but silently returns nothing.
    """
    if not source.is_active:
        return False, "Источник выключен — сбор по нему не идёт."
    if schedules is None:
        pass  # Task visibility is denied; still check the source and its access below.
    elif not schedules:
        return False, "Ни одна задача не ссылается на этот источник — сбор не запускается."
    elif not any(s.is_active for s in schedules):
        return False, "Все задачи по этому источнику на паузе."
    if schedules is not None and effective_schedules is not None and not any(
        effective_schedules.get(task.id, False) for task in schedules
    ):
        return False, "Задачи не выполняются: проверьте, что назначенный сценарий активен."
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

        # Resolve the content window the same way the handlers do, so a forced
        # collect from the source page honours the task's configured dates
        # instead of draining the whole history. Priority: linked task payload
        # cli_dates → source params cli_dates.
        window = await _resolve_source_window(source)
        from app.jobs.source_activity import SourceJobBusy

        try:
            outcome = await run_job_inline(
                "collect", {"source_ids": [source.id], **({"cli_dates": window["cli_dates"]} if window.get("cli_dates") else {})}
            )
        except SourceJobBusy:
            add_flash(request, "info", "По источнику уже есть задача сбора или анализа в очереди либо в работе")
            return RedirectResponse(f"/app/sources/{source_id}", status_code=302)

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
    from app.jobs.source_activity import SourceJobBusy
    from app.models.managers.job_manager import JobManager

    with tenant_scope(tenant_id):
        source = await Source.objects.get(id=source_id, tenant_id=tenant_id)
        if source is None:
            add_flash(request, "error", "Источник не найден")
            return RedirectResponse("/app/sources", status_code=302)

        running = await _source_has_running_job(source_id)
        if running["collect"] or running["analyze"]:
            add_flash(request, "info", "По источнику уже есть задача сбора или анализа в очереди либо в работе")
            return RedirectResponse(f"/app/sources/{source_id}", status_code=302)

        # Preview, reset and worker execution must use the same configured
        # window. Do not reset unrelated historical rows or report an empty
        # queue as a successfully requested analysis.
        from app.models.managers.agent_task_manager import AgentTaskManager

        window = await _resolve_source_window(source)
        cli_dates = window.get("cli_dates") or {}
        bounds = {key: AgentTaskManager.parse_date(cli_dates.get(key)) for key in ("start_date", "end_date")}
        staged = await CollectedItem.objects.for_source(source_id, limit=1, include_exhausted=True, **bounds)
        if not staged:
            add_flash(
                request, "info", "В настроенном периоде нет сырых материалов для анализа. Проверьте период и сбор."
            )
            return RedirectResponse(f"/app/sources/{source_id}", status_code=302)

        session = new_session()
        job = None
        try:
            async with session.begin():
                await JobManager.lock_source_admission(session, tenant_id)
                reset_count = await CollectedItem.objects.reset_attempts(session, source_id, **bounds)
                job = await JobManager().admit_source_job(
                    job_type="analyze",
                    payload={"source_ids": [source.id], **({"cli_dates": window["cli_dates"]} if window.get("cli_dates") else {})},
                    tenant_id=tenant_id,
                    session=session,
                )
        except SourceJobBusy:
            add_flash(request, "info", "По источнику уже есть задача сбора или анализа в очереди либо в работе")
            return RedirectResponse(f"/app/sources/{source_id}", status_code=302)
        finally:
            await session.close()

    # Execute in this process's event loop so the job runs without app.runtime worker.
    if job:
        from app.jobs.dispatcher import launch_job_background

        launch_job_background(job.id, allow_retry=False)

    name = source.name
    if reset_count:
        add_flash(
            request,
            "info",
            f"Сброшено {reset_count} записей(ей) из «досрочного выхода» — будут проанализированы заново",
        )
    add_flash(request, "success", f"Анализ по источнику «{name}» запущен")
    return RedirectResponse(f"/app/sources/{source_id}", status_code=302)


@router.post("/{source_id}/delete-unprocessed")
async def source_delete_unprocessed(
    request: Request,
    source_id: int,
    token: str = Form("", alias="_csrf"),
    tenant_id: int | None = Form(default=None),
):
    """Remove raw staged content for one source; analytics remain unchanged."""
    back = f"/app/sources/{source_id}"
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse(back, status_code=302)

    from app.web.tasks import _diagnostics_admin

    if not _diagnostics_admin(request):
        add_flash(request, "error", "Удалять необработанный контент может только администратор")
        return RedirectResponse(back, status_code=302)

    active_tenant_id = action_tenant_id(request, tenant_id)
    if type(active_tenant_id) is not int or active_tenant_id <= 0:
        add_flash(request, "error", "Не удалось определить workspace источника")
        return RedirectResponse("/app/sources", status_code=302)

    from app.core.database import new_session
    from app.core.tenant_context import tenant_scope
    from app.models.managers.job_manager import JobManager

    session = new_session()
    try:
        with tenant_scope(active_tenant_id):
            async with session.begin():
                # Serialize with source-job admission. The active-job check and
                # delete share one transaction so queued work cannot race cleanup.
                await JobManager.lock_source_admission(session, active_tenant_id)
                source = await Source.objects.get(id=source_id, tenant_id=active_tenant_id, session=session)
                if source is None:
                    add_flash(request, "error", "Источник не найден")
                    return RedirectResponse("/app/sources", status_code=302)
                running = await _source_has_running_job(source_id)
                if running["collect"] or running["analyze"]:
                    add_flash(request, "info", "Нельзя удалить контент, пока сбор или анализ ожидает запуска или выполняется")
                    return RedirectResponse(back, status_code=302)
                deleted = await CollectedItem.objects.delete_unprocessed_for_source(
                    session, source_id, active_tenant_id
                )
    finally:
        await session.close()

    if deleted:
        add_flash(request, "success", f"Удалено необработанных записей: {deleted}")
    else:
        add_flash(request, "info", "Необработанного контента для удаления нет")
    return RedirectResponse(back, status_code=302)


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

    from app.core.database import new_session
    from app.core.tenant_context import tenant_scope
    from app.services.collection.source_deletion import SourceDeleteBlocked, SourceDeleteScopeError, delete_source

    session = new_session()
    try:
        with tenant_scope(tenant_id):
            async with session.begin():
                source = await Source.objects.get(id=source_id, tenant_id=tenant_id, session=session)
                if source is None:
                    add_flash(request, "error", "Источник не найден")
                    return RedirectResponse("/app/sources", status_code=302)

                # An orphan lane may be deleted once nothing reads it. A paused
                # task's final explicit link is not an orphan: resume would
                # otherwise widen the task to every active workspace source.
                # `sources.analytics` is delete-orphan, so the analysis rows go
                # with it. The service refuses while an active task still reads
                # the source, or while a paused task would lose its last explicit
                # one, so the scope is removed inside the task instead.
                removal = await delete_source(session, source_id=source_id, tenant_id=tenant_id)
    except SourceDeleteScopeError:
        add_flash(
            request,
            "error",
            "Не удалось проверить связи задач источника; проверьте или удалите связанную задачу перед удалением источника",
        )
        return RedirectResponse(f"/app/sources/{source_id}", status_code=302)
    finally:
        await session.close()

    if isinstance(removal, SourceDeleteBlocked):
        if removal.active_tasks:
            ids = ", ".join(f"№{task.task_id}" for task in removal.active_tasks)
            if len(removal.active_tasks) == 1:
                add_flash(request, "error", f"Источник используется активной задачей {ids} — удалите его в задаче")
            else:
                add_flash(request, "error", f"Источник используется активными задачами {ids} — удалите их в задачах")
        if removal.last_source_tasks:
            names = ", ".join(f"«{task.name}»" for task in removal.last_source_tasks)
            if len(removal.last_source_tasks) == 1:
                add_flash(
                    request,
                    "error",
                    f"Источник — единственный у задачи {names}: выберите ей другой источник "
                    "или удалите задачу, прежде чем удалять источник",
                )
            else:
                add_flash(
                    request,
                    "error",
                    f"Источник — единственный у задач {names}: выберите им другие источники "
                    "или удалите эти задачи, прежде чем удалять источник",
                )
        return RedirectResponse(f"/app/sources/{source_id}", status_code=302)

    if removal is None:
        add_flash(request, "error", "Источник не найден")
        return RedirectResponse("/app/sources", status_code=302)

    add_flash(request, "success", f"Источник '{removal.name}' удалён вместе с его аналитикой")
    return RedirectResponse("/app/sources", status_code=302)
