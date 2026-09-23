"""Tool: run content collection right now (enqueues a job)."""

from __future__ import annotations

from app.agent.tools import tool


@tool(
    name="collect_now",
    description="Запустить сбор контента из соцсетей прямо сейчас (для всех активных источников или указанных id).",
    parameters={
        "type": "object",
        "properties": {
            "source_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "ID конкретных источников; пусто = все активные",
            }
        },
        "required": [],
    },
)
async def collect_now(source_ids: list[int] | None = None) -> dict:
    from app.models.managers.job_manager import JobManager

    job = await JobManager().enqueue("collect", {"source_ids": list(source_ids or [])})
    return {"status": "queued", "job_id": job.id}
