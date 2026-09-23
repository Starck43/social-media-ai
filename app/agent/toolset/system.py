"""Tools: system status and durable memory."""

from __future__ import annotations

from typing import Any

from app.agent.tools import tool


@tool(
    name="system_status",
    description="Состояние системы: очередь задач, расписания, расходы на LLM за сегодня.",
    parameters={"type": "object", "properties": {}, "required": []},
)
async def system_status() -> dict[str, Any]:
    from app.core.config import settings
    from app.models import Job, Schedule
    from app.models.managers.agent_message_manager import agent_messages

    pending = await Job.objects.filter(status="pending").count()
    running = await Job.objects.filter(status="running").count()
    failed = await Job.objects.filter(status="failed").count()
    active_schedules = await Schedule.objects.filter(is_active=True).count()

    daily_cost = await agent_messages.cost_today()

    return {
        "jobs": {"pending": pending, "running": running, "failed": failed},
        "schedules_active": active_schedules,
        "llm_cost_today_usd": round(daily_cost, 4),
        "llm_cost_cap_usd": settings.AGENT_DAILY_COST_LIMIT,
    }


@tool(
    name="memory_set",
    description="Сохранить факт в постоянную память агента (например, «уровень внимания: высокий»).",
    parameters={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Короткий ключ факта"},
            "value": {"type": "string", "description": "Значение; пустая строка = удалить"},
        },
        "required": ["key", "value"],
    },
)
async def memory_set(key: str, value: str) -> dict[str, Any]:
    from app.models.managers.agent_memory_manager import agent_memory

    value = (value or "").strip()
    await agent_memory.write(key.strip(), value or None)
    return {"status": "saved" if value else "deleted", "key": key.strip()}


@tool(
    name="memory_get",
    description="Прочитать постоянную память агента (все факты или один по ключу).",
    parameters={
        "type": "object",
        "properties": {"key": {"type": "string", "description": "Ключ факта; пусто = все факты"}},
        "required": [],
    },
)
async def memory_get(key: str | None = None) -> dict[str, Any]:
    from app.models.managers.agent_memory_manager import agent_memory

    if key:
        value = await agent_memory.read(key.strip())
        return {"key": key.strip(), "value": value}
    return {"facts": await agent_memory.as_dict()}
