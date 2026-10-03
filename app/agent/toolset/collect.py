"""Tool: run content collection right now (executes the job inline)."""

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
    """Collect now and report what happened.

    This used to enqueue and answer `{"status": "queued"}` while the tool's own
    description promised "прямо сейчас" — the agent then told the user it had run
    when nothing had happened yet. It executes inline now, so the result is a
    fact rather than a promise.
    """
    from app.jobs.dispatcher import run_job_inline

    outcome = await run_job_inline("collect", {"source_ids": list(source_ids or [])})
    if not outcome:
        return {"status": "failed", "error": "не удалось запустить сбор"}

    result = outcome.get("result") or {}
    return {
        "status": outcome.get("status"),
        "job_id": outcome.get("job_id"),
        "items": result.get("items", 0),
        "sources": result.get("sources", 0),
        "collected": result.get("collected", 0),
        "empty": result.get("empty", 0),
        "errors": result.get("error_sources", []),
        "error": outcome.get("error"),
    }
