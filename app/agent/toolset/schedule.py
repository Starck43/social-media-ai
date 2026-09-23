"""Tools: manage cron schedules (list / add / remove / pause)."""

from __future__ import annotations

from typing import Any

from app.agent.tools import tool

_JOB_TYPES = ("collect", "digest", "prune")


@tool(
    name="schedule_list",
    description="Показать все расписания (cron): имя, выражение, тип задачи, активность, время следующего запуска.",
    parameters={"type": "object", "properties": {}, "required": []},
)
async def schedule_list() -> list[dict[str, Any]]:
    from app.models import Schedule

    rows = await Schedule.objects.order_by(Schedule.id)
    return [
        {
            "name": s.name,
            "cron": s.cron_expr,
            "job_type": s.job_type,
            "active": s.is_active,
            "next_run_at": s.next_run_at.isoformat() if s.next_run_at else None,
            "last_status": s.last_status,
        }
        for s in rows
    ]


@tool(
    name="schedule_add",
    description=(
        "Создать cron-расписание. Примеры: «каждый день в 9:00» → '0 9 * * *', "
        "«каждый час» → '0 * * * *'. Тип задачи: collect (сбор), digest (сводка), prune (очистка)."
    ),
    confirm=True,
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Уникальное имя расписания"},
            "cron": {"type": "string", "description": "Cron-выражение из 5 полей (мин час день месяц день-недели)"},
            "job_type": {"type": "string", "enum": list(_JOB_TYPES), "description": "Что запускать"},
            "payload": {
                "type": "object",
                "description": 'Параметры задачи, например {"period": "day"} для digest',
            },
        },
        "required": ["name", "cron", "job_type"],
    },
)
async def schedule_add(
    name: str,
    cron: str,
    job_type: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from app.core.config import settings
    from app.models import Schedule
    from app.models.managers.schedule_manager import ScheduleManager
    from app.scheduler.cron import next_run_at

    if job_type not in _JOB_TYPES:
        return {"error": f"Unknown job_type: {job_type!r}. Use one of: {', '.join(_JOB_TYPES)}"}
    if not ScheduleManager.validate_cron(cron):
        return {"error": f"Invalid cron expression: {cron!r}"}
    if await Schedule.objects.get(name=name):
        return {"error": f"Schedule {name!r} already exists"}

    schedule = await Schedule.objects.create(
        name=name,
        cron_expr=cron,
        timezone=settings.SCHEDULER_TIMEZONE,
        job_type=job_type,
        payload=payload or {},
        is_active=True,
        next_run_at=next_run_at(cron, settings.SCHEDULER_TIMEZONE),
    )
    return {"status": "created", "name": schedule.name, "next_run_at": schedule.next_run_at.isoformat()}


@tool(
    name="schedule_remove",
    description="Удалить расписание по имени.",
    confirm=True,
    parameters={
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Имя расписания"}},
        "required": ["name"],
    },
)
async def schedule_remove(name: str) -> dict[str, Any]:
    from app.models import Schedule

    deleted = await Schedule.objects.delete(name=name)
    return {"status": "deleted" if deleted else "not_found", "name": name}


@tool(
    name="schedule_pause",
    description="Поставить расписание на паузу или возобновить его.",
    confirm=True,
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Имя расписания"},
            "pause": {
                "type": "boolean",
                "description": "true = поставить на паузу (по умолчанию), false = возобновить",
            },
        },
        "required": ["name"],
    },
)
async def schedule_pause(name: str, pause: bool = True) -> dict[str, Any]:
    from app.models import Schedule

    schedule = await Schedule.objects.get(name=name)
    if schedule is None:
        return {"error": f"Schedule {name!r} not found"}
    await Schedule.objects.update_by_id(schedule.id, is_active=not pause)
    return {"status": "paused" if pause else "resumed", "name": name}
