"""Tasks `/app/tasks` — create and manage scheduled agent tasks.

Flow:
- sources are linked via the m2m `agent_task_sources` table (not payload)
- scenario is set via `agent_scenario_id` FK (not payload)
- payload keeps flat keys: period, monitored_users, excluded_users, ...
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app.models import AgentTask, Source, AgentScenario
from app.types import JobType
from app.tasks.cron import cron_to_human

from .deps import add_flash, ensure_csrf, render

router = APIRouter(prefix="/tasks")


def _split_names(raw: str) -> list[str]:
    """Split a comma/space separated username string into a clean list."""
    return [n.strip().lstrip("@") for n in raw.replace(",", " ").split() if n.strip()]


async def _check_task_sources(source_ids: list[int], tenant_id: int) -> None:
    """Reject source ids that are missing or belong to another workspace.

    from app.models.agent_task import agent_task_sources

    async with async_session_maker() as session:
        await session.execute(delete(agent_task_sources).where(agent_task_sources.c.agent_task_id == task_id))
        if source_ids:
            stmt = pg_insert(agent_task_sources).values(
                [{"agent_task_id": task_id, "source_id": sid} for sid in source_ids]
            )
            await session.execute(stmt.on_conflict_do_nothing())
        await session.commit()


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

    tasks = await (
        AgentTask.objects.filter(tenant_id=tenant_id)
        .prefetch_related("sources", "agent_scenario")
        .order_by(AgentTask.created_at.desc())
    )

    sources = await (
        Source.objects.filter(tenant_id=tenant_id)
        .order_by(Source.name)
    )

    scenarios = await (
        AgentScenario.objects.filter(tenant_id=tenant_id, is_active=True)
        .order_by(AgentScenario.name)
    )

    return render(
        request,
        "web/tasks.html",
        section="tasks",
        tasks=tasks,
        sources=sources,
        scenarios=scenarios,
        job_types=JobType.choices(),
        cron_to_human=cron_to_human,
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
    try:
        await _check_task_sources(parsed_source_ids, tenant_id)
    except ValueError as e:
        add_flash(request, "error", str(e))
        return RedirectResponse("/app/tasks", status_code=302)

        add_flash(request, "success", f"Задача «{name}» создана и запущена")
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
    try:
        await _check_task_sources(parsed_source_ids, tenant_id)
    except ValueError as e:
        add_flash(request, "error", str(e))
        return RedirectResponse("/app/tasks", status_code=302)

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
