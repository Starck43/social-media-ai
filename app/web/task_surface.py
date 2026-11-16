"""Unified task hub routes; domain changes stay in planner/calendar/task services."""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from app.core.permissions import get_current_user, has_permission
from app.services.planner.tasks import PlannerConflict, PlannerUnavailable
from app.types import ActionType
from app.web.deps import ensure_csrf, render

router = APIRouter()
TABS = (
    ("planner", "Мои дела", "/app/planner"),
    ("tasks", "Поручения агенту", "/app/tasks"),
    ("calendar", "Календарь", "/app/task-hub/calendar"),
    ("sources", "Задачи из сервисов", "/app/task-hub/sources"),
)


def _can(request: Request, resource: str) -> bool:
    return bool(has_permission(get_current_user(), resource, ActionType.VIEW))


def _can_task_sources(request: Request) -> bool:
    return _can(request, "plannertask") and _can(request, "source")


def task_tabs(request: Request):
    """Tabs available to the current actor; used by existing task route pages too."""
    return [
        tab
        for tab in TABS
        if (tab[0] != "planner" or _can(request, "plannertask"))
        and (tab[0] != "tasks" or _can(request, "agenttask"))
        and (tab[0] != "calendar" or _can(request, "plannertask") or _can(request, "agenttask"))
        and (tab[0] != "sources" or _can_task_sources(request))
    ]


@router.get("/task-hub")
async def task_hub(request: Request):
    tabs = task_tabs(request)
    if not tabs:
        raise HTTPException(403, "Tasks unavailable")
    return RedirectResponse(tabs[0][2], status_code=303)


@router.get("/task-hub/calendar")
async def task_calendar(
    request: Request,
    timezone_name: str = Query("UTC", alias="timezone", max_length=100),
    before_planner: int | None = Query(None, gt=0),
    before_event: int | None = Query(None, gt=0),
    before_agent: int | None = Query(None, gt=0),
):
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, "Invalid timezone") from None
    from app.services.workspace.task_surface import calendar_view

    data = await calendar_view(
        timezone_name=timezone_name, before_planner=before_planner, before_event=before_event, before_agent=before_agent
    )
    return render(
        request, "web/task_calendar.html", section="tasks", tabs=task_tabs(request), active_tab="calendar", **data
    )


@router.get("/task-hub/sources")
async def task_sources_page(
    request: Request,
    cursor: str | None = Query(None, max_length=2000),
):
    if not _can_task_sources(request):
        raise HTTPException(403, "Task source access denied")
    from app.services.task_sources import task_source_service

    try:
        sources = await task_source_service.connections()
    except PermissionError:
        raise HTTPException(403, "Task source access denied") from None
    except LookupError:
        raise HTTPException(404, "Task source unavailable") from None
    projection = {"state": "not_configured", "items": [], "next_cursor": None}
    selected_id = sources.get("selected_connection_id")
    if selected_id is not None:
        try:
            projection = await task_source_service.projection(connection_id=selected_id, cursor=cursor, limit=20)
        except PermissionError:
            raise HTTPException(403, "Task source access denied") from None
        except LookupError:
            raise HTTPException(404, "Task source unavailable") from None
        except ValueError:
            raise HTTPException(422, "Invalid task source cursor") from None
        if isinstance(projection.get("items"), list):
            from app.services.workspace.today import bitrix_task_status_label

            projection = {
                **projection,
                "items": [
                    {**item, "status_label": bitrix_task_status_label(item.get("status"))}
                    for item in projection["items"]
                    if isinstance(item, dict)
                ],
            }
    return render(
        request,
        "web/task_sources.html",
        section="tasks",
        tabs=task_tabs(request),
        active_tab="sources",
        sources=sources,
        projection=projection,
        can_configure=has_permission(get_current_user(), "source", ActionType.CREATE),
    )


@router.post("/task-hub/sources/{connection_id}")
async def task_source_configure(request: Request, connection_id: int):
    if not _can_task_sources(request) or not has_permission(get_current_user(), "source", ActionType.CREATE):
        raise HTTPException(403, "Task source access denied")
    form = await request.form()
    if (
        not ensure_csrf(request, form.get("_csrf", ""))
        or set(form.keys()) != {"_csrf", "enabled"}
        or len(list(form.keys())) != len(list(form.multi_items()))
    ):
        raise HTTPException(403, "Invalid form")
    enabled_value = form.get("enabled")
    if enabled_value not in {"yes", "no"}:
        raise HTTPException(422, "Invalid task source consent")
    from app.services.task_sources import task_source_service

    try:
        await task_source_service.configure(
            connection_id, enabled=enabled_value == "yes", selected=enabled_value == "yes"
        )
    except PermissionError:
        raise HTTPException(403, "Task source unavailable") from None
    except LookupError:
        raise HTTPException(404, "Task source unavailable") from None
    except ValueError:
        raise HTTPException(422, "Invalid task source consent") from None
    return RedirectResponse("/app/task-hub/sources", status_code=303)


@router.get("/task-hub/new")
async def task_quick_create(
    request: Request,
    origin: str | None = None,
    origin_id: int | None = Query(None, gt=0),
    boundary: str = "cloud",
    thread_id: int | None = Query(None, gt=0),
):
    from app.services.workspace.task_surface import quick_origin

    if not has_permission(get_current_user(), "plannertask", ActionType.CREATE):
        raise HTTPException(403, "Planner create access required")
    try:
        context = await quick_origin(origin=origin, origin_id=origin_id, boundary=boundary, thread_id=thread_id)
    except PermissionError:
        raise HTTPException(403, "Task source unavailable") from None
    except LookupError:
        raise HTTPException(404, "Task source unavailable") from None
    except ValueError:
        raise HTTPException(422, "Invalid task source") from None
    return render(
        request,
        "web/task_quick_create.html",
        section="tasks",
        tabs=task_tabs(request),
        context=context,
        origin=context["kind"] if context else None,
        origin_id=context["id"] if context else None,
        boundary=boundary,
        thread_id=thread_id,
    )


@router.post("/task-hub/new")
async def task_quick_create_post(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    origin: str | None = Form(None),
    origin_id: int | None = Form(None),
    boundary: str = Form("cloud"),
    thread_id: int | None = Form(None),
):
    form = await request.form()
    if not ensure_csrf(request, form.get("_csrf", "")) or len(list(form.keys())) != len(list(form.multi_items())):
        raise HTTPException(403, "Invalid form")
    from app.services.workspace.task_surface import quick_create

    try:
        task = await quick_create(
            title=title,
            description=description,
            origin=origin,
            origin_id=origin_id,
            boundary=boundary,
            thread_id=thread_id,
        )
    except PermissionError:
        raise HTTPException(403, "Task source unavailable") from None
    except (PlannerConflict, PlannerUnavailable, LookupError):
        raise HTTPException(404, "Task source unavailable") from None
    except ValueError:
        raise HTTPException(422, "Invalid task") from None
    return RedirectResponse(f"/app/planner/{task.id}/edit", status_code=303)


@router.get("/task-hub/assignments/{agent_id}/result")
async def assignment_result(request: Request, agent_id: int, job_id: int = Query(..., gt=0)):
    from app.services.workspace.task_surface import result_preview

    try:
        result = await result_preview(agent_id, job_id)
    except PermissionError:
        raise HTTPException(403, "Result unavailable") from None
    except (PlannerUnavailable, PlannerConflict, ValueError):
        raise HTTPException(404, "Result unavailable") from None
    return render(request, "web/task_result_confirm.html", section="tasks", tabs=task_tabs(request), **result)


@router.post("/task-hub/assignments/{agent_id}/result")
async def assignment_result_apply(
    request: Request,
    agent_id: int,
    job_id: int = Form(...),
    expected_version: int = Form(...),
    operation: str = Form(...),
    annotation: str = Form(""),
    confirm: str = Form(""),
):
    form = await request.form()
    if not ensure_csrf(request, form.get("_csrf", "")) or len(list(form.keys())) != len(list(form.multi_items())):
        raise HTTPException(403, "Invalid form")
    if confirm != "yes" or operation not in {"annotate", "complete"}:
        raise HTTPException(422, "Confirm the task update")
    from app.services.workspace.task_surface import apply_result

    try:
        await apply_result(agent_id, job_id, expected_version, operation=operation, annotation=annotation)
    except PermissionError:
        raise HTTPException(403, "Result unavailable") from None
    except (PlannerUnavailable, PlannerConflict, ValueError):
        raise HTTPException(409, "Task changed or result unavailable") from None
    return RedirectResponse("/app/planner", status_code=303)
