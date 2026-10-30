"""Read-only workspace panel for chat; shared readers own plan/result access."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from .deps import perms_can

router = APIRouter(prefix="/workspace")
PAGE_SIZE = 20


def workspace_denied(request: Request):
    """Fail before any model imports or reads when identity/grants are absent."""
    user = getattr(request.state, "web_user", None)
    tenant_id = getattr(request.state, "tenant_id", None)
    perms = getattr(request.state, "web_perms", None)
    if (
        user is None
        or type(getattr(user, "id", None)) is not int
        or type(tenant_id) is not int
        or perms is None
        or not getattr(perms, "can_open_chat", False)
        or not perms_can(request, "agenttask", "view")
    ):
        return JSONResponse({"error": "Задачи недоступны в текущем пространстве"}, status_code=403)
    return None


async def selected_task(request: Request, task_id: int) -> dict | None:
    """Revalidate selected context on every read/send, using the canonical reader."""
    from app.core.tenant_context import tenant_scope
    from app.services.task_results import task_configuration

    with tenant_scope(request.state.tenant_id):
        definition = await task_configuration(task_id)
    # The panel deliberately exposes only the social task lane, matching its
    # picker. Retention/operator tasks keep their existing management gate.
    if definition is None or definition.get("job_type") not in {"collect", "analyze"}:
        return None
    return definition


@router.get("/tasks")
async def workspace_tasks(
    request: Request,
    search: str = Query("", max_length=100),
    offset: int = Query(0, ge=0, le=10000),
):
    denied = workspace_denied(request)
    if denied is not None:
        return denied
    from app.core.tenant_context import tenant_scope
    from app.models import AgentTask

    with tenant_scope(request.state.tenant_id):
        query = AgentTask.objects.filter(tenant_id=request.state.tenant_id, job_type__in=["collect", "analyze"])
        if search.strip():
            # Escape SQL LIKE metacharacters: the picker searches literal names.
            escaped = search.strip().replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
            query = query.filter(AgentTask.name.ilike(f"%{escaped}%", escape="\\"))
        rows = await query.order_by(AgentTask.id.desc()).offset(offset).limit(PAGE_SIZE + 1)
    return JSONResponse(
        {
            "tasks": [{"id": row.id, "name": row.name[:160]} for row in rows[:PAGE_SIZE]],
            "next_offset": offset + PAGE_SIZE if len(rows) > PAGE_SIZE and offset < 10000 else None,
        },
        headers={"Cache-Control": "no-store"},
    )


def task_snapshot(definition: dict, result: dict | None, *, can_view_results: bool) -> dict:
    """Project only panel fields; never forward credentials, raw errors or payloads."""
    task_id = definition["id"]
    plan = definition.get("plan") if definition.get("plan_status") == "available" else None
    safe_plan = None
    if isinstance(plan, dict):
        safe_plan = {
            "goal": (plan.get("goal") or definition["name"])[:1000],
            "platforms": plan.get("platforms", []),
            "source_scope": plan.get("source_scope"),
            "source_count": len(plan.get("source_ids", [])),
            "window": plan.get("window"),
            "trigger": plan.get("trigger"),
            "stages": plan.get("pipeline", {}).get("stages", []),
            "limit": plan.get("collection", {}).get("limit"),
        }
    latest = None
    if result is not None:
        latest = {
            "status": result.get("job_status", "unavailable"),
            "coverage": result.get("coverage"),
            "progress": None,
            "analyses": [],
        }
        progress = result.get("progress")
        if isinstance(progress, dict):
            latest["progress"] = {key: progress.get(key) for key in ("phase", "total", "completed", "updated_at")}
        if can_view_results:
            latest["analyses"] = [
                {"title": row["title"], "url": f"/app/analytics/{row['id']}"} for row in result.get("analyses", [])[:3]
            ]
    return {
        "id": task_id,
        "name": definition["name"][:160],
        "task_url": f"/app/tasks/{task_id}",
        "plan_status": definition.get("plan_status", "unavailable"),
        "plan": safe_plan,
        "latest": latest,
    }


@router.get("/tasks/{task_id}")
async def workspace_task(request: Request, task_id: int):
    denied = workspace_denied(request)
    if denied is not None:
        return denied
    if task_id <= 0:
        return JSONResponse({"error": "Задача недоступна"}, status_code=404)
    try:
        definition = await selected_task(request, task_id)
        if definition is None:
            return JSONResponse({"error": "Задача недоступна"}, status_code=404)
        from app.core.tenant_context import tenant_scope
        from app.services.task_results import task_result

        with tenant_scope(request.state.tenant_id):
            result = await task_result(task_id, limit=3)
    except PermissionError:
        return JSONResponse({"error": "Доступ к задаче изменился"}, status_code=403)
    return JSONResponse(
        task_snapshot(
            definition,
            result,
            can_view_results=perms_can(request, "source", "view") and perms_can(request, "aianalytics", "view"),
        ),
        headers={"Cache-Control": "no-store"},
    )
