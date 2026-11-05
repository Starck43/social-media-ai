"""Typed planner handlers. Explicit registration is deferred with the shared catalog.

Importing this module never registers a fallback permission or enables a tool
before the planner resource is registered in the post-#391 integration commit.
"""

from typing import Any

from pydantic import ValidationError

from app.services.planner.contracts import TaskCreate
from app.services.planner.tasks import PlannerConflict, PlannerUnavailable, planner_service


async def planner_create(task: dict[str, Any]) -> dict[str, Any]:
    try:
        return (await planner_service.create(TaskCreate.model_validate(task))).model_dump(mode="json")
    except (ValidationError, ValueError):
        return {"error": "Invalid planner request", "code": "planner_invalid_request"}
    except (PermissionError, PlannerUnavailable):
        return {"error": "Planner unavailable", "code": "planner_unavailable"}


async def planner_list(limit: int = 50, before_id: int | None = None) -> dict[str, Any]:
    try:
        page = await planner_service.list(limit=limit, before_id=before_id)
        return {**page, "items": [item.model_dump(mode="json") for item in page["items"]]}
    except (ValueError, PermissionError, PlannerUnavailable, PlannerConflict):
        return {"error": "Planner unavailable", "code": "planner_unavailable"}


def register_planner_tools():
    from app.agent.tools import TOOL_REGISTRY, tool
    from app.core.access_catalog import resolve_permission_key

    if any(resolve_permission_key(f"planner.task.{action}") is None for action in ("create", "view")):
        raise ValueError("planner_catalog_not_registered")
    declarations = (
        (
            "planner_create",
            planner_create,
            "Создать личную или командную задачу планировщика, не cron-задание агента.",
            "create",
            True,
            {
                "task": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Required task title; up to 500 characters."},
                        "description": {"type": "string"},
                        **{
                            name: {"type": ["integer", "null"], "minimum": 1}
                            for name in ("assignee_id", "contact_id", "parent_id")
                        },
                        **{name: {"type": ["string", "null"]} for name in ("due_at", "due_date", "recurrence")},
                        "timezone": {"type": "string", "description": "IANA zone; due_at requires an ISO 8601 offset."},
                        "priority": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                        "category": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["title"],
                    "additionalProperties": False,
                }
            },
            ["task"],
        ),
        (
            "planner_list",
            planner_list,
            "Показать доступные задачи планировщика текущего пространства.",
            "view",
            False,
            {"limit": {"type": "integer", "minimum": 1, "maximum": 100}, "before_id": {"type": ["integer", "null"]}},
            [],
        ),
    )
    for name, handler, description, action, confirm, properties, required in declarations:
        if name not in TOOL_REGISTRY:
            tool(
                name=name,
                description=description,
                parameters={
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
                required_permission=f"planner.task.{action}",
                confirm=confirm,
            )(handler)
