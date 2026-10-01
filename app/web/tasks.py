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

from .deps import add_flash, ensure_csrf, render, tenant_filter_context

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


async def enqueue_task_now(task: "AgentTask") -> "Job":
    """Enqueue the task's job immediately; a one-time task is completed in the process.

    Thin wrapper over `app.jobs.enqueue.enqueue_task_run` — the shared
    implementation is also what the CLI and the sqladmin action call, so the
    three surfaces cannot drift apart. Returns the created Job so the caller can
    track its result (e.g. show a run-now notification once the worker finishes).
    """
    from app.jobs.enqueue import enqueue_task_run

    return await enqueue_task_run(task)


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

    raw_job_id = request.query_params.get("job_id")
    job_id = int(raw_job_id) if raw_job_id and raw_job_id.isdigit() else None

    return render(
        request,
        "web/tasks.html",
        section="tasks",
        tasks=tasks,
        sources=sources,
        scenarios=scenarios,
        job_types=JobType.choices(),
        cron_to_human=cron_to_human,
        job_id=job_id,
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
):
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse("/app/tasks", status_code=302)

    tenant_id = request.state.tenant_id

    cron_expr = cron_custom.strip()

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
    from app.tasks.cron import next_run_at

    if cron_expr == "@once":
        next_run = datetime.now(timezone.utc) + timedelta(minutes=1)
    else:
        next_run = next_run_at(cron_expr, "Europe/Moscow")

    try:
        await _check_task_sources(parsed_source_ids, tenant_id)
    except ValueError as e:
        add_flash(request, "error", str(e))
        return RedirectResponse("/app/tasks", status_code=302)

    with tenant_scope(tenant_id):
        task = await AgentTask.objects.create(
            name=name.strip()[:100],
            job_type=job_type,
            cron_expr=cron_expr,
            timezone="Europe/Moscow",
            payload=payload,
            agent_scenario_id=scenario_id,
            is_active=True,
            next_run_at=next_run,
        )
        if parsed_source_ids:
            await _replace_task_sources(task.id, parsed_source_ids, tenant_id)

    if run_now:
        job = await enqueue_task_now(task)
        add_flash(request, "success", f"Задача «{name}» создана и запущена")
        return RedirectResponse(f"/app/tasks?job_id={job.id}", status_code=302)
    else:
        add_flash(request, "success", f"Задача «{name}» создана")
    return RedirectResponse("/app/tasks", status_code=302)


@router.post("/{task_id}/toggle")
async def task_toggle(
    request: Request,
    task_id: int,
    token: str = Form("", alias="_csrf"),
):
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse("/app/tasks", status_code=302)

    tenant_id = request.state.tenant_id

    task = await AgentTask.objects.get(id=task_id, tenant_id=tenant_id)
    if task is None:
        add_flash(request, "error", "Задача не найдена")
        return RedirectResponse("/app/tasks", status_code=302)

    await AgentTask.objects.update_by_id(task.id, is_active=not task.is_active)
    action = "активирована" if not task.is_active else "деактивирована"
    add_flash(request, "success", f"Задача «{task.name}» {action}")
    return RedirectResponse("/app/tasks", status_code=302)


@router.post("/{task_id}/run-now")
async def task_run_now(
    request: Request,
    task_id: int,
    token: str = Form("", alias="_csrf"),
):
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse("/app/tasks", status_code=302)

    tenant_id = request.state.tenant_id

    task = await AgentTask.objects.get(id=task_id, tenant_id=tenant_id)
    if task is None:
        add_flash(request, "error", "Задача не найдена")
        return RedirectResponse("/app/tasks", status_code=302)

    job = await enqueue_task_now(task)
    from app.jobs.dispatcher import run_job_now

    result = await run_job_now(job.id)
    if result and result.get("status") == "failed":
        add_flash(request, "error", f"Задача «{task.name}» завершилась с ошибкой: {result.get('error', '?')}")
    else:
        add_flash(request, "success", f"Задача «{task.name}» выполнена")
    return RedirectResponse(f"/app/tasks?job_id={job.id}", status_code=302)


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
):
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse("/app/tasks", status_code=302)

    tenant_id = request.state.tenant_id

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
    from app.tasks.cron import next_run_at

    if cron_expr == "@once":
        next_run = datetime.now(timezone.utc) + timedelta(minutes=1)
    else:
        next_run = next_run_at(cron_expr, "Europe/Moscow")

    try:
        await _check_task_sources(parsed_source_ids, tenant_id)
    except ValueError as e:
        add_flash(request, "error", str(e))
        return RedirectResponse("/app/tasks", status_code=302)

    with tenant_scope(tenant_id):
        await AgentTask.objects.update_by_id(
            task.id,
            name=name.strip()[:100],
            job_type=job_type,
            cron_expr=cron_expr,
            timezone="Europe/Moscow",
            payload=payload,
            agent_scenario_id=scenario_id,
            is_active=is_active == "on",
            next_run_at=next_run,
        )
        await _replace_task_sources(task.id, parsed_source_ids, tenant_id)

    if run_now:
        updated = await AgentTask.objects.get(id=task.id, tenant_id=tenant_id)
        job = await enqueue_task_now(updated)
        add_flash(request, "success", f"Задача «{name}» обновлена и запущена")
        return RedirectResponse(f"/app/tasks?job_id={job.id}", status_code=302)
    else:
        add_flash(request, "success", f"Задача «{name}» обновлена")
    return RedirectResponse("/app/tasks", status_code=302)


@router.post("/{task_id}/delete")
async def task_delete(
    request: Request,
    task_id: int,
    token: str = Form("", alias="_csrf"),
):
    if not ensure_csrf(request, token):
        add_flash(request, "error", "Сессия истекла, попробуйте ещё раз")
        return RedirectResponse("/app/tasks", status_code=302)

    tenant_id = request.state.tenant_id

    task = await AgentTask.objects.get(id=task_id, tenant_id=tenant_id)
    if task is None:
        add_flash(request, "error", "Задача не найдена")
        return RedirectResponse("/app/tasks", status_code=302)

    task_name = task.name
    await AgentTask.objects.delete(id=task_id)
    add_flash(request, "success", f"Задача «{task_name}» удалена")
    return RedirectResponse("/app/tasks", status_code=302)


def _job_summary(job: "Job", task_name: str | None = None) -> dict[str, Any]:
    """Minimal human-readable result summary for a finished job (run-now modal)."""
    status = job.status
    if status == "pending" or status == "running":
        label = f"Выполнение задачи «{task_name}»" if task_name else "Выполнение задачи"
        return {"status": status, "label": label}
    if status == "failed":
        return {"status": "failed", "label": "Ошибка", "error": (job.error or "Неизвестная ошибка")[:300]}

    result = job.result or {}
    job_type = job.job_type

    if job_type == "collect":
        return {
            "status": "done",
            "label": "Сбор завершён",
            "detail": (
                f"источников: {result.get('sources', 0)}, "
                f"собрано: {result.get('collected', 0)}, "
                f"элементов: {result.get('items', 0)}, "
                f"ошибок: {result.get('failed', 0)}"
            ),
        }
    if job_type == "digest":
        return {
            "status": "done",
            "label": "Дайджест сформирован",
            "detail": f"период: {result.get('period', 'day')}, сообщений: {result.get('messages_sent', 0)}",
        }
    if job_type == "analyze":
        return {
            "status": "done",
            "label": "Анализ завершён",
            "detail": (
                f"источников: {result.get('sources', 0)}, "
                f"проанализировано: {result.get('analyzed', 0)}, "
                f"действий: {result.get('actions_created', 0)}"
            ),
        }
    if job_type == "prune":
        return {
            "status": "done",
            "label": "Очистка завершена",
            "detail": f"удалено записей: {result.get('deleted', 0)}",
        }
    return {"status": "done", "label": "Завершено", "detail": str(result)[:300]}


@router.get("/job/{job_id}/status")
async def job_status(request: Request, job_id: int):
    """Poll job status (run-now modal). Returns JSON; tenant-scoped."""
    from fastapi.responses import JSONResponse

    tenant_id = request.state.tenant_id
    job = await Job.objects.filter(id=job_id, tenant_id=tenant_id).first()
    if job is None:
        return JSONResponse({"status": "not_found", "label": "Задача не найдена"})
    task_name = None
    if job.agent_task_id is not None:
        task = await AgentTask.objects.get(id=job.agent_task_id, tenant_id=tenant_id)
        task_name = task.name if task else None
    return JSONResponse(_job_summary(job, task_name))
