"""Tasks `/app/tasks` — create and manage scheduled agent tasks.

Flow:
- sources are linked via the m2m `agent_task_sources` table (not payload)
- scenario is set via `agent_scenario_id` FK (not payload)
- payload keeps flat keys: period, monitored_users, excluded_users, ...
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.models import AgentScenario, AgentTask, Job, Source
from app.tasks.cron import cron_to_human
from app.types import JobType

from .deps import action_tenant_id, add_flash, ensure_csrf, guard_web, perms_can, render, safe_next, tenant_filter_context

router = APIRouter(prefix="/tasks")


def _split_names(raw: str) -> list[str]:
    """Split a comma/space separated username string into a clean list."""
    return [n.strip().lstrip("@") for n in raw.replace(",", " ").split() if n.strip()]


async def _check_task_sources(source_ids: list[int], tenant_id: int) -> None:
    """Reject source ids that are missing or belong to another workspace.

    The m2m link table has no tenant column, so a raw POST could otherwise
    attach somebody else's source to this task and make the job read it.
    """
    from app.models import Source

    if not source_ids:
        return
    sources = {s.id: s for s in await Source.objects.filter(id__in=source_ids)}
    foreign = sorted(sid for sid, s in sources.items() if s.tenant_id != tenant_id)
    if foreign:
        raise ValueError(f"Источник(и) {', '.join(map(str, foreign))} принадлежат другому воркспейсу")


async def _replace_task_sources(task_id: int, source_ids: list[int], tenant_id: int) -> None:
    """Replace the task's m2m source links with the given set."""
    from app.models.managers.agent_task_manager import AgentTaskManager

    await _check_task_sources(source_ids, tenant_id)
    await AgentTaskManager().set_sources(task_id, source_ids)


def _edit_payload(task: AgentTask, effective_active: set[int]) -> dict[str, Any]:
    """Everything the edit modal binds, as the plain object Alpine edits.

    Built here rather than in the template because it has two callers: the row's
    name button and the `?task_id=` deep link, and the editor must not fill
    differently depending on how it was opened. `is_active` is the *effective*
    flag — the one the list column shows — so reopening a task that is active on
    paper but blocked by a deactivated source does not silently activate it.
    """
    payload = task.payload if isinstance(task.payload, dict) else {}
    return {
        "id": task.id,
        "name": task.name,
        "job_type": task.job_type,
        "cron_expr": task.cron_expr,
        "is_active": task.id in effective_active,
        "source_ids": [s.id for s in task.sources] if task.sources else [],
        "scenario_id": task.agent_scenario_id,
        "monitored_users": ", ".join(payload.get("monitored_users") or []),
        "excluded_users": ", ".join(payload.get("excluded_users") or []),
    }


async def _can_activate(tenant_id: int, job_type: str, source_ids: list[int], scenario_id: int | None) -> str | None:
    """Return why the task cannot be activated, or None if it can.

    A task is only activatable when its scenario (if one is chosen) is active
    and, for a source-based job type (collect/analyze), it has at least one
    active source to operate on: an explicit active linked source, or (with no
    linked sources, which works over "all active sources") any active source in
    the workspace. Workspace/memory-level types need no source.
    """
    from app.core.tenant_context import tenant_scope
    from app.models.managers.agent_task_manager import AgentTaskManager

    with tenant_scope(tenant_id):
        if scenario_id is not None:
            scenario = await AgentScenario.objects.get(id=scenario_id, tenant_id=tenant_id)
            if scenario is None or not scenario.is_active:
                return "выбранный сценарий деактивирован"

        if not AgentTaskManager.requires_sources(job_type):
            return None

        if source_ids:
            sources = await Source.objects.filter(id__in=source_ids)
            if not any(s.is_active for s in sources):
                return "все привязанные источники деактивированы"
            return None

        active = await Source.objects.filter(is_active=True).values(Source.id).rows()
        if not active:
            return "в воркспейсе нет активных источников"
        return None


async def run_task_now(task: "AgentTask") -> dict[str, Any]:
    """Run the task's job right now, in this process — it never sits in the queue.

    "Собрать сейчас" has to feel immediate, and a queued job is not: it waits for a
    worker, and if the worker is down or busy the user just sees a spinner that
    times out. So the job row is written and executed in the same call — the row
    still exists (it is the audit trail the result modal and `/app/jobs` read),
    but it is stamped `running` immediately, so the worker never picks it up.

    Returns the dispatcher's outcome dict plus `job_id` (the callers redirect to
    `/app/tasks?job_id=…` so the modal can render it).
    """
    from app.jobs.dispatcher import run_task_directly

    return await run_task_directly(task)


@router.get("")
@router.get("/")
async def tasks_list(request: Request):
    tenant_id = request.state.tenant_id
    user = getattr(request.state, "web_user", None)
    is_superuser = bool(user and user.is_superuser)
    filter_tenant_id, tenants = await tenant_filter_context(request, is_superuser)

    if is_superuser:
        from app.core.tenant_context import tenant_scope

        with tenant_scope(bypass=True):
            query = AgentTask.objects.prefetch_related("sources", "agent_scenario", "tenant")
            if filter_tenant_id is not None:
                query = query.filter(tenant_id=filter_tenant_id)
            tasks = await query.order_by(AgentTask.created_at.desc())
    else:
        tasks = await (
            AgentTask.objects.filter(tenant_id=tenant_id)
            .prefetch_related("sources", "agent_scenario")
            .order_by(AgentTask.created_at.desc())
        )

    sources = await Source.objects.filter(tenant_id=tenant_id).order_by(Source.name)

    scenarios = await AgentScenario.objects.filter(tenant_id=tenant_id, is_active=True).order_by(AgentScenario.name)

    # Effective activity: a task bound to a deactivated scenario or with no
    # active source to operate on is not "active", however its flag is set.
    from app.models.managers.agent_task_manager import AgentTaskManager

    active_source_ids = {s.id for s in sources if s.is_active}
    effective = await AgentTaskManager().effective_active_map(tasks, active_source_ids)
    effective_active = {tid for tid, ok in effective.items() if ok}

    raw_job_id = request.query_params.get("job_id")
    job_id = int(raw_job_id) if raw_job_id and raw_job_id.isdigit() else None

    # The editor is addressed by `?task_id=` so its URL can be shared and survives
    # a reload. The map is built from the rows this page actually shows, which is
    # what makes the deep link safe: an id outside the current workspace — or a
    # filtered-out one for a superuser — is simply not in it, so nothing opens.
    edit_tasks = {t.id: _edit_payload(t, effective_active) for t in tasks}

    raw_task_id = request.query_params.get("task_id")
    open_task_id = int(raw_task_id) if raw_task_id and raw_task_id.isdigit() else None
    if open_task_id not in edit_tasks:
        open_task_id = None

    return render(
        request,
        "web/tasks.html",
        section="tasks",
        tasks=tasks,
        sources=sources,
        scenarios=scenarios,
        effective_active=effective_active,
        job_types=JobType.choices(),
        cron_to_human=cron_to_human,
        job_id=job_id,
        edit_tasks=edit_tasks,
        open_task_id=open_task_id,
        is_superuser=is_superuser,
        tenants=tenants,
        filter_tenant_id=filter_tenant_id,
    )


@router.post("")
async def task_create(
    request: Request,
    name: str = Form(...),
    job_type: str = Form(...),
    cron_custom: str = Form(...),
    source_ids: list[str] = Form([]),
    scenario_id: int = Form(default=None),
    monitored_users: str = Form(""),
    excluded_users: str = Form(""),
    run_now: str = Form(""),
    token: str = Form("", alias="_csrf"),
    tenant_id: int | None = Form(default=None),
    # Where to land after creating — the onboarding wizard posts here too, and
    # every failure below has to come back to the page that was filled in.
    next: str = Form(""),
):
    back = safe_next(next) or "/app/tasks"
    # `None` when the caller stayed on /app/tasks; the run-status modal only
    # exists there, so an external caller (onboarding) takes the plain flash.
    return_to = safe_next(next)

    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse(back, status_code=302)

    tenant_id = action_tenant_id(request, tenant_id)

    denied = guard_web(request, "agenttask", "create", back=back)
    if denied is not None:
        return denied

    cron_expr = cron_custom.strip()

    from app.models.managers.agent_task_manager import AgentTaskManager

    tasks_mgr = AgentTaskManager()
    if not tasks_mgr.validate_cron(cron_expr):
        add_flash(request, "error", f"Некорректное cron-выражение: {cron_expr}")
        return RedirectResponse(back, status_code=302)

    parsed_source_ids: list[int] = []
    for s in source_ids:
        s = s.strip()
        if s.isdigit():
            parsed_source_ids.append(int(s))

    payload: dict = {}
    if monitored_users:
        payload["monitored_users"] = _split_names(monitored_users)
    if excluded_users:
        payload["excluded_users"] = _split_names(excluded_users)

    from app.core.tenant_context import tenant_scope
    from app.models.managers.tenant_manager import tenants
    from app.tasks.cron import next_run_at, resolve_tz

    # The workspace zone wins; the global setting is only the fallback. Must
    # agree with the runner, which re-advances the schedule in the same zone.
    tenant = await tenants.get(id=tenant_id)
    tz = resolve_tz(tenant)

    if cron_expr == "@once":
        next_run = datetime.now(timezone.utc) + timedelta(minutes=1)
    else:
        next_run = next_run_at(cron_expr, tz)

    try:
        await _check_task_sources(parsed_source_ids, tenant_id)
    except ValueError as e:
        add_flash(request, "error", str(e))
        return RedirectResponse(back, status_code=302)

    # A task can only be created active when it has an active scenario and, for
    # a source-based type, at least one active source; otherwise it starts
    # inactive.
    activation_blocked = await _can_activate(tenant_id, job_type, parsed_source_ids, scenario_id)
    if activation_blocked is not None:
        add_flash(
            request,
            "warning",
            f"Задача «{name}» создана неактивной: {activation_blocked}",
        )
    is_active = activation_blocked is None

    with tenant_scope(tenant_id):
        task = await AgentTask.objects.create(
            name=name.strip()[:100],
            job_type=job_type,
            cron_expr=cron_expr,
            payload=payload,
            agent_scenario_id=scenario_id,
            is_active=is_active,
            next_run_at=next_run,
        )
        if parsed_source_ids:
            await _replace_task_sources(task.id, parsed_source_ids, tenant_id)

    if run_now and is_active:
        outcome = await run_task_now(task)
        add_flash(request, "success", f"Задача «{name}» создана и запущена")
        # The run-status modal lives on the tasks page; from onboarding there is
        # nothing to poll, so land on the caller's page with the flash instead.
        job_id = (outcome or {}).get("job_id")
        target = f"/app/tasks?job_id={job_id}" if job_id else "/app/tasks"
        return RedirectResponse(back if return_to else target, status_code=302)
    else:
        add_flash(request, "success", f"Задача «{name}» создана")
    return RedirectResponse(back, status_code=302)


@router.get("/{task_id}")
async def task_detail(request: Request, task_id: int):
    """One task: what it does, what sources it touches, and its recent runs."""
    from contextlib import nullcontext

    from app.core.tenant_context import tenant_scope
    from app.models.agent_task import AgentTask
    from app.models.agent_scenario import AgentScenario
    from app.models.job import Job
    from app.models.source import Source

    user = getattr(request.state, "web_user", None)
    is_superuser = bool(user and user.is_superuser)
    filter_tenant_id, _tenants = await tenant_filter_context(request, is_superuser)

    source = None
    with tenant_scope(bypass=True) if is_superuser else nullcontext():
        source = (
            await AgentTask.objects.filter(id=task_id).select_related("tenant").first()
        )
        if source is not None and not is_superuser and source.tenant_id != request.state.tenant_id:
            source = None
        if source is None:
            return render(
                request,
                "web/task_detail.html",
                section="tasks",
                task=None,
                filter_tenant_id=filter_tenant_id,
            )

        # Recent jobs for this task.
        recent_jobs = await (
            Job.objects.filter(agent_task_id=task_id)
            .order_by(Job.created_at.desc())
            .limit(10)
        )

        # Linked sources.
        linked_sources = await (
            Source.objects.filter(id__in=await _task_source_ids(task_id))
            .order_by(Source.name)
        )

        # Scenario.
        scenario = None
        if source.agent_scenario_id is not None:
            scenario = await AgentScenario.objects.get(id=source.agent_scenario_id, tenant_id=source.tenant_id)

    return render(
        request,
        "web/task_detail.html",
        section="tasks",
        task=source,
        filter_tenant_id=filter_tenant_id,
        recent_jobs=recent_jobs,
        sources=linked_sources,
        scenario=scenario,
        cron_to_human=cron_to_human,
        JOB_TYPE_TITLES=JOB_TYPE_TITLES,
        perms_can=perms_can,
    )


async def _task_source_ids(task_id: int) -> list[int]:
    """Return the source ids linked to a task via the m2m table."""
    from app.models.agent_task import agent_task_sources

    rows = await agent_task_sources.select().where(
        agent_task_sources.c.task_id == task_id
    ).fetchall()
    return [r.source_id for r in rows]


@router.post("/{task_id}/toggle")
async def task_toggle(
    request: Request,
    task_id: int,
    token: str = Form("", alias="_csrf"),
    tenant_id: int | None = Form(default=None),
):
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse("/app/tasks", status_code=302)

    tenant_id = action_tenant_id(request, tenant_id)

    denied = guard_web(request, "agenttask", "update", back="/app/tasks")
    if denied is not None:
        return denied

    task = await AgentTask.objects.get(id=task_id, tenant_id=tenant_id)
    if task is None:
        add_flash(request, "error", "Задача не найдена")
        return RedirectResponse("/app/tasks", status_code=302)

    new_active = not task.is_active
    if new_active:
        from app.models.managers.agent_task_manager import AgentTaskManager

        linked = await AgentTaskManager().get_sources(task.id)
        blocked = await _can_activate(tenant_id, task.job_type, linked, task.agent_scenario_id)
        if blocked is not None:
            add_flash(request, "error", f"Задача «{task.name}» не активирована: {blocked}")
            return RedirectResponse("/app/tasks", status_code=302)

    await AgentTask.objects.update_by_id(task.id, is_active=new_active)
    action = "активирована" if new_active else "деактивирована"
    add_flash(request, "success", f"Задача «{task.name}» {action}")
    return RedirectResponse("/app/tasks", status_code=302)


@router.post("/{task_id}/run-now")
async def task_run_now(
    request: Request,
    task_id: int,
    token: str = Form("", alias="_csrf"),
    tenant_id: int | None = Form(default=None),
):
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse("/app/tasks", status_code=302)

    tenant_id = action_tenant_id(request, tenant_id)

    # Enqueues a job — the same right the sqladmin run-now action declares.
    denied = guard_web(request, "agenttask", "update", back="/app/tasks")
    if denied is not None:
        return denied

    task = await AgentTask.objects.get(id=task_id, tenant_id=tenant_id)
    if task is None:
        add_flash(request, "error", "Задача не найдена")
        return RedirectResponse("/app/tasks", status_code=302)

    outcome = await run_task_now(task)
    if outcome and outcome.get("status") == "failed":
        add_flash(request, "error", f"Задача «{task.name}» завершилась с ошибкой: {outcome.get('error', '?')}")
    else:
        add_flash(request, "success", f"Задача «{task.name}» выполнена")
    job_id = (outcome or {}).get("job_id")
    if not job_id:
        return RedirectResponse("/app/tasks", status_code=302)
    return RedirectResponse(f"/app/tasks?job_id={job_id}", status_code=302)


@router.post("/{task_id}")
async def task_update(
    request: Request,
    task_id: int,
    name: str = Form(...),
    job_type: str = Form(...),
    cron_expr: str = Form(...),
    is_active: str = Form(""),
    source_ids: list[str] = Form([]),
    scenario_id: int = Form(default=None),
    monitored_users: str = Form(""),
    excluded_users: str = Form(""),
    run_now: str = Form(""),
    token: str = Form("", alias="_csrf"),
    tenant_id: int | None = Form(default=None),
):
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse("/app/tasks", status_code=302)

    tenant_id = action_tenant_id(request, tenant_id)

    denied = guard_web(request, "agenttask", "update", back="/app/tasks")
    if denied is not None:
        return denied

    task = await AgentTask.objects.get(id=task_id, tenant_id=tenant_id)
    if task is None:
        add_flash(request, "error", "Задача не найдена")
        return RedirectResponse("/app/tasks", status_code=302)

    cron_expr = cron_expr.strip()
    from app.models.managers.agent_task_manager import AgentTaskManager

    tasks_mgr = AgentTaskManager()
    if not tasks_mgr.validate_cron(cron_expr):
        add_flash(request, "error", f"Некорректное cron-выражение: {cron_expr}")
        return RedirectResponse("/app/tasks", status_code=302)

    parsed_source_ids: list[int] = []
    for s in source_ids:
        s = s.strip()
        if s.isdigit():
            parsed_source_ids.append(int(s))

    payload: dict = {}
    if monitored_users:
        payload["monitored_users"] = _split_names(monitored_users)
    if excluded_users:
        payload["excluded_users"] = _split_names(excluded_users)

    from app.core.tenant_context import tenant_scope
    from app.models.managers.tenant_manager import tenants
    from app.tasks.cron import next_run_at, resolve_tz

    # Same rule as create: the workspace zone, so an edit does not silently
    # re-schedule the task in the global zone the runner would keep using.
    tenant = await tenants.get(id=tenant_id)
    tz = resolve_tz(tenant)

    if cron_expr == "@once":
        next_run = datetime.now(timezone.utc) + timedelta(minutes=1)
    else:
        next_run = next_run_at(cron_expr, tz)

    try:
        await _check_task_sources(parsed_source_ids, tenant_id)
    except ValueError as e:
        add_flash(request, "error", str(e))
        return RedirectResponse("/app/tasks", status_code=302)

    # Activation is allowed only when the task has an active scenario and, for a
    # source-based type, at least one active source; otherwise it stays inactive.
    requested_active = is_active == "on"
    activation_blocked = None
    if requested_active:
        activation_blocked = await _can_activate(tenant_id, job_type, parsed_source_ids, scenario_id)
        if activation_blocked is not None:
            add_flash(
                request,
                "warning",
                f"Задача «{name}» не активирована: {activation_blocked}",
            )

    with tenant_scope(tenant_id):
        await AgentTask.objects.update_by_id(
            task.id,
            name=name.strip()[:100],
            job_type=job_type,
            cron_expr=cron_expr,
            payload=payload,
            agent_scenario_id=scenario_id,
            is_active=requested_active and activation_blocked is None,
            next_run_at=next_run,
        )
        await _replace_task_sources(task.id, parsed_source_ids, tenant_id)

    if run_now and activation_blocked is None:
        updated = await AgentTask.objects.get(id=task.id, tenant_id=tenant_id)
        outcome = await run_task_now(updated)
        add_flash(request, "success", f"Задача «{name}» обновлена и запущена")
        job_id = (outcome or {}).get("job_id")
        if not job_id:
            return RedirectResponse("/app/tasks", status_code=302)
        return RedirectResponse(f"/app/tasks?job_id={job_id}", status_code=302)
    else:
        add_flash(request, "success", f"Задача «{name}» обновлена")
    return RedirectResponse("/app/tasks", status_code=302)


@router.post("/{task_id}/delete")
async def task_delete(
    request: Request,
    task_id: int,
    token: str = Form("", alias="_csrf"),
    tenant_id: int | None = Form(default=None),
):
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse("/app/tasks", status_code=302)

    tenant_id = action_tenant_id(request, tenant_id)

    denied = guard_web(request, "agenttask", "delete", back="/app/tasks")
    if denied is not None:
        return denied

    task = await AgentTask.objects.get(id=task_id, tenant_id=tenant_id)
    if task is None:
        add_flash(request, "error", "Задача не найдена")
        return RedirectResponse("/app/tasks", status_code=302)

    task_name = task.name
    await AgentTask.objects.delete(id=task_id)
    add_flash(request, "success", f"Задача «{task_name}» удалена")
    return RedirectResponse("/app/tasks", status_code=302)


JOB_TYPE_TITLES = {
    "collect": "Сбор данных",
    "analyze": "Анализ данных",
    "digest": "Дайджест",
    "prune": "Очистка",
    "learn": "Обучение",
    "reflect": "Рефлексия",
}

OUTCOME_HEADLINES = {
    # `collect`'s headline is the count of *new* items, not the platform's response
    # size: the platform re-serves the same posts on every run, so "32 collected"
    # twice in a row said nothing about whether the second run found anything.
    # The second tuple is the fallback for jobs recorded before `new_items`
    # existed — better an honest total than a "0 новых" nobody measured.
    "collect": ("Новых записей", "new_items", ("Записей собрано", "items")),
    "analyze": ("Проанализировано", "analyzed"),
    "digest": ("Отправлено сообщений", "messages_sent"),
    "prune": ("Записей удалено", "deleted"),
}


def _plural(n: int, one: str, few: str, many: str) -> str:
    """Russian count form: 1 запись / 2 записи / 5 записей."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _stat(value: Any, label: str) -> dict[str, Any]:
    """One stat tile: the number and the word that explains it.

    The old modal showed `источников: 2, собрано: 1, элементов: 12` — labels
    that sound like keys of a config, and `собрано` next to `элементов` reads as
    two versions of the same count. Each tile now carries its own noun so the
    number is readable without knowing the handler's dict.
    """
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = 0
    return {"value": number, "label": label}


def _job_summary(job: "Job", task_name: str | None = None) -> dict[str, Any]:
    """Structured result summary for a finished job (run-now modal).

    Three parts the modal renders: what ran (`title`), the headline number
    (`headline`) and the supporting stats (`stats`) — task type and outcome only,
    no per-item detail. Source links are added separately by `job_status`, which
    has to resolve them from the database.
    """
    status = job.status
    job_type = job.job_type

    if status in ("pending", "running"):
        label = f"Выполнение задачи «{task_name}»" if task_name else "Выполнение задачи"
        return {"status": status, "label": label, "title": JOB_TYPE_TITLES.get(job_type, job_type)}
    if status == "failed":
        return {
            "status": "failed",
            "label": "Ошибка",
            "title": JOB_TYPE_TITLES.get(job_type, job_type),
            "error": (job.error or "Неизвестная ошибка")[:300],
        }

    result = job.result or {}
    title = JOB_TYPE_TITLES.get(job_type, job_type)

    # The headline is the number that answers "did it work?" — the one the user
    # pressed the button for. Everything else is context.
    headline = None
    if job_type in OUTCOME_HEADLINES:
        spec = OUTCOME_HEADLINES[job_type]
        word, key = spec[0], spec[1]
        if len(spec) > 2 and result.get(key) is None:
            # The counter did not exist for this job — report what *was* recorded
            # under its own name instead of inventing a zero.
            word, key = spec[2]
        try:
            count = int(result.get(key, 0) or 0)
        except (TypeError, ValueError):
            count = 0
        headline = {"value": count, "label": word, "noun": _plural(count, "запись", "записи", "записей")}

    stats: list[dict[str, Any]] = []
    if job_type == "collect":
        stats = [
            _stat(result.get("sources", 0), "источников опрошено"),
            _stat(result.get("collected", 0), "ответили данными"),
            _stat(result.get("items", 0), "получено записей"),
            _stat(result.get("empty", 0), "без содержимого"),
            # `error`, not `failed`: the handler writes `error`, so this counter
            # used to read 0 for every run that actually had failures.
            _stat(result.get("error", 0), "ошибок"),
        ]
    elif job_type == "analyze":
        stats = [
            _stat(result.get("sources", 0), "источников проверено"),
            _stat(result.get("actions_created", 0), "действий создано"),
            _stat(result.get("skipped", 0), "пропущено"),
        ]
    elif job_type == "digest":
        stats = [_stat(result.get("period", "—"), "период")]
    elif job_type == "prune":
        stats = [_stat(result.get("deleted", 0), "записей удалено")]

    summary: dict[str, Any] = {"status": "done", "label": "Готово", "title": title, "stats": stats}
    if headline is not None:
        summary["headline"] = headline
    if task_name:
        summary["task_name"] = task_name
    return summary


def _source_links(job: "Job", task_name: str | None = None) -> list[dict[str, Any]]:
    """Sources to jump to from the run-now modal, with what this run got from each.

    The modal is a summary; it is not where you read the data. Each entry is a
    mini-link to the source page, which shows the collected rows and — for an
    `analyze` run — the agent's per-item analysis.

    Prefer the run's own `per_source` breakdown (it is what actually ran, with
    per-source counts). Jobs written before that breakdown existed have none, so
    fall back to the task's sources with no counts: a link is still useful, a
    fabricated zero is not.
    """
    result = job.result if isinstance(job.result, dict) else {}
    rows = result.get("per_source") or []

    if not rows:
        return []

    links = []
    for entry in rows:
        source_id = entry.get("source_id")
        if not source_id:
            continue
        outcome = entry.get("outcome") or "empty"
        if outcome == "error":
            note = "ошибка"
        elif outcome == "collected":
            items = int(entry.get("items") or 0)
            # Prefer the new count: "32 records" for a source that re-serves the
            # same 32 every hour reads as 32 new records, which is not what
            # happened. Older jobs have no counter — fall back to the total.
            if entry.get("new_items") is not None:
                new_items = int(entry["new_items"])
                if new_items:
                    note = f"{new_items} {_plural(new_items, 'новая', 'новых', 'новых')}"
                else:
                    note = "новых нет"
            else:
                note = f"{items} {_plural(items, 'запись', 'записи', 'записей')}"
        else:
            note = "без новых данных"
        link = {
            "source_id": source_id,
            "name": entry.get("name") or f"Источник {source_id}",
            "note": note,
            "error": outcome == "error",
        }
        # `analyze` records what the agent looked at rather than rows collected,
        # so the link carries that count instead of the collection wording.
        analyzed = entry.get("analyzed")
        if outcome != "error" and analyzed:
            count = int(analyzed)
            link["note"] = f"{count} {_plural(count, 'анализ', 'анализа', 'анализов')}"
        links.append(link)
    return links


@router.get("/job/{job_id}/status")
async def job_status(request: Request, job_id: int):
    """Poll job status (run-now modal). Returns JSON; tenant-scoped."""
    from fastapi.responses import JSONResponse

    tenant_id = request.state.tenant_id
    job = await Job.objects.filter(id=job_id, tenant_id=tenant_id).first()
    if job is None:
        return JSONResponse({"status": "not_found", "label": "Задача не найдена"})
    task_name = None
    task = None
    if job.agent_task_id is not None:
        task = await AgentTask.objects.get(id=job.agent_task_id, tenant_id=tenant_id)
        task_name = task.name if task else None
    summary = _job_summary(job, task_name)
    # Mini-links to the sources this run touched; they need the DB, so they are
    # resolved here rather than in the pure `_job_summary`.
    summary["sources"] = _source_links(job)
    return JSONResponse(summary)
