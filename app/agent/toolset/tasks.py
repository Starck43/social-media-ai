"""Tools: manage cron agent tasks (list / add / remove / pause)."""

from __future__ import annotations

from typing import Any

from app.agent.tools import tool


def _get_job_types() -> tuple[str, ...]:
    from app.jobs.handlers import HANDLERS

    return tuple(HANDLERS.keys())


@tool(
    name="task_list",
    description="Показать все задачи (cron): имя, выражение, тип задачи, активность, время следующего запуска.",
    parameters={"type": "object", "properties": {}, "required": []},
)
async def task_list() -> list[dict[str, Any]]:
    from app.models import AgentTask

    rows = await AgentTask.objects.order_by(AgentTask.id)
    return [
        {
            "name": t.name,
            "cron": t.cron_expr,
            "job_type": t.job_type,
            "active": t.is_active,
            "next_run_at": t.next_run_at.isoformat() if t.next_run_at else None,
            "last_status": t.last_status,
        }
        for t in rows
    ]


@tool(
    name="task_add",
    description=(
        "Создать cron-задачу. Примеры: «каждый день в 9:00» → '0 9 * * *', "
        "«каждый час» → '0 * * * *'. Тип задачи: collect, digest, prune, analyze, learn, reflect."
        " Источники задаются списком source_ids (связываются с задачей); пустой список = все активные. "
        "Для collect можно задать monitored_users (кого отслеживать) и excluded_users (кого игнорировать). "
        "Для collect/analyze ОБЯЗАТЕЛЬНО указать start_date (с какой даты собирать контент, YYYY-MM-DD) — "
        "иначе первый запуск вытянет всё с первого поста."
    ),
    confirm=True,
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Уникальное имя задачи"},
            "cron": {"type": "string", "description": "Cron-выражение из 5 полей (мин час день месяц день-недели) или @once"},
            "job_type": {"type": "string", "enum": list(_get_job_types()), "description": "Что запускать"},
            "source_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "ID источников для задачи (связываются через m2m). Пусто = все активные",
            },
            "monitored_users": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Опционально: список username для отслеживания (только collect)",
            },
            "excluded_users": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Опционально: список username для исключения (collect/analyze)",
            },
            "scenario_id": {"type": "integer", "description": "Опционально: ID сценария агента"},
            "period": {"type": "string", "description": "Период для digest: day | week (default day)"},
            "start_date": {
                "type": "string",
                "description": "Обязательно для collect/analyze: с какой даты собирать контент (YYYY-MM-DD)",
            },
            "end_date": {"type": "string", "description": "Опционально: до какой даты собирать контент (YYYY-MM-DD)"},
            "force_refresh": {
                "type": "boolean",
                "description": "Перетирать данные за период на каждом запуске (collect)",
            },
            "force_reanalyze": {
                "type": "boolean",
                "description": "Принудительно повторно анализировать уже обработанные записи (analyze)",
            },
        },
        "required": ["name", "cron", "job_type"],
    },
)
async def task_add(
    name: str,
    cron: str,
    job_type: str,
    source_ids: list[int] | None = None,
    monitored_users: list[str] | None = None,
    excluded_users: list[str] | None = None,
    scenario_id: int | None = None,
    period: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    force_refresh: bool = False,
    force_reanalyze: bool = False,
) -> dict[str, Any]:
    from app.core.tenant_context import current_tenant_id
    from app.models import AgentTask, Tenant
    from app.models.managers.agent_task_manager import AgentTaskManager
    from app.tasks.cron import next_run_at, resolve_tz

    job_types = _get_job_types()
    if job_type not in job_types:
        return {"error": f"Unknown job_type: {job_type!r}. Use one of: {', '.join(job_types)}"}
    if not AgentTaskManager.validate_cron(cron):
        return {"error": f"Invalid cron expression: {cron!r}"}
    if await AgentTask.objects.get(name=name):
        return {"error": f"Task {name!r} already exists"}

    payload: dict[str, Any] = {}
    if monitored_users:
        payload["monitored_users"] = list(monitored_users)
    if excluded_users:
        payload["excluded_users"] = list(excluded_users)
    if period:
        payload["period"] = period

    # collect/analyze need a content start date — without it a fresh source
    # drains the whole history from the first post (same rule as the web form).
    if AgentTaskManager.requires_content_dates(job_type):
        start = AgentTaskManager.parse_date(start_date)
        if start is None:
            return {"error": "Для задачи этого типа укажите start_date (с какой даты собирать контент, YYYY-MM-DD)"}
        end = AgentTaskManager.parse_date(end_date)
        payload.update(AgentTaskManager.build_dates_payload(start, end, force_refresh=bool(force_refresh)))
        if force_reanalyze:
            payload["force_reanalyze"] = True

    # The workspace zone, same rule as the web form and the runner: a task
    # created here in the global zone would shift by the offset on its first fire.
    tenant = await Tenant.objects.get(id=current_tenant_id())
    task = await AgentTask.objects.create(
        name=name,
        cron_expr=cron,
        job_type=job_type,
        payload=payload,
        agent_scenario_id=scenario_id,
        is_active=True,
        next_run_at=next_run_at(cron, resolve_tz(tenant)),
    )

    if source_ids:
        from app.models.managers.agent_task_manager import AgentTaskManager

        # The m2m table has no tenant column, so a link written here is the
        # only thing keeping a task out of another workspace's sources. Verify
        # them against the task's own workspace, like the CLI and web do.
        await _check_task_sources(source_ids, task.tenant_id)
        await AgentTaskManager().add_sources(task.id, source_ids)

    return {"status": "created", "name": task.name, "next_run_at": task.next_run_at.isoformat()}


async def _check_task_sources(source_ids: list[int], tenant_id: int) -> None:
    """Reject source ids that are missing or belong to another workspace."""
    from app.models import Source

    if not source_ids:
        return
    sources = {s.id: s for s in await Source.objects.filter(id__in=source_ids)}
    foreign = sorted(sid for sid, s in sources.items() if s.tenant_id != tenant_id)
    if foreign:
        raise ValueError(f"Source(s) {', '.join(map(str, foreign))} belong to another workspace")


@tool(
    name="task_remove",
    description="Удалить задачу по имени.",
    confirm=True,
    parameters={
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Имя задачи"}},
        "required": ["name"],
    },
)
async def task_remove(name: str) -> dict[str, Any]:
    from app.models import AgentTask

    deleted = await AgentTask.objects.delete(name=name)
    return {"status": "deleted" if deleted else "not_found", "name": name}


@tool(
    name="task_pause",
    description="Поставить задачу на паузу или возобновить её.",
    confirm=True,
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Имя задачи"},
            "pause": {
                "type": "boolean",
                "description": "true = поставить на паузу (по умолчанию), false = возобновить",
            },
        },
        "required": ["name"],
    },
)
async def task_pause(name: str, pause: bool = True) -> dict[str, Any]:
    from app.models import AgentTask

    task = await AgentTask.objects.get(name=name)
    if task is None:
        return {"error": f"Task {name!r} not found"}
    await AgentTask.objects.update_by_id(task.id, is_active=not pause)
    return {"status": "paused" if pause else "resumed", "name": name}
