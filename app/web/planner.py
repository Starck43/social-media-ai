"""Authenticated, CSRF-protected planner list/board and explicit edits."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError

from app.core.database import async_session_maker
from app.core.permissions import get_current_user, has_permission
from app.integrations.bitrix24.client import Bitrix24Error
from app.services.planner.contracts import TaskCreate, TaskReschedule, TaskUpdate, TaskVersion
from app.services.planner.events import EventPlanningUnavailable, event_planning_service
from app.services.planner.reminder_contracts import (
    ReminderCreate,
    ReminderReschedule,
    ReminderSnooze,
    ReminderVersion,
)
from app.services.planner.reminders import reminder_service
from app.services.planner.tasks import PlannerConflict, PlannerUnavailable, planner_service
from app.types import ActionType
from app.web.deps import ensure_csrf, render

router = APIRouter()
STATUS_LABELS = {"todo": "К выполнению", "in_progress": "В работе", "done": "Готово", "cancelled": "Отменено"}
PRIORITY_LABELS = {"low": "Низкий", "medium": "Средний", "high": "Высокий", "critical": "Критический"}
REMINDER_ERROR_LABELS = {
    "recipient_unavailable": "Получатель или его доступ к задаче недоступен",
    "task_closed": "Задача закрыта; напоминание не отправлено",
    "quiet_hours": "Перенесено до окончания тихих часов",
    "tasks_disabled": "В настройках отключены уведомления о задачах",
    "invalid_preferences": "Не удалось прочитать настройки уведомлений",
}
DELIVERY_ERROR_LABELS = {
    "outcome_unconfirmed": "Результат отправки не подтверждён; повторной отправки не будет",
    "recipient_or_task_unavailable": "Получатель или его доступ к задаче недоступен",
    "channel_not_implemented": "Канал пока недоступен; напоминание сохранено во входящих",
    "personal_telegram_unavailable": "Личный Telegram не подключён",
    "quiet_hours": "Отправка перенесена до окончания тихих часов",
    "tasks_disabled": "В настройках отключены уведомления о задачах",
    "invalid_preferences": "Не удалось прочитать настройки уведомлений",
}


async def invoke(operation):
    try:
        return await operation
    except PlannerUnavailable:
        raise HTTPException(404, "Task unavailable") from None
    except PlannerConflict:
        raise HTTPException(409, "Task changed; refresh before editing") from None
    except PermissionError:
        raise HTTPException(403, "Planner access denied") from None
    except ValueError:
        raise HTTPException(422, "Invalid planner fields") from None


@router.get("/planner")
@router.get("/planner/{task_id}/edit")
async def planner_page(
    request: Request,
    task_id: int | None = None,
    before_id: int | None = Query(None, gt=0),
    view: Literal["list", "board"] = "list",
):
    page = await invoke(planner_service.list(limit=50, before_id=before_id))
    editing = await invoke(planner_service.get(task_id)) if task_id is not None else None
    actor = get_current_user()
    async with async_session_maker() as session, session.begin():
        event_settings = await invoke(event_planning_service.is_enabled(session=session))
        greeting_ids = await invoke(
            event_planning_service.greeting_task_ids([item.id for item in page["items"]], session=session)
        )
    reminders = await invoke(reminder_service.list(task_id)) if task_id is not None and can_remind(actor) else []
    return render(
        request,
        "web/planner.html",
        section="tasks",
        tabs=[
            ("planner", "Мои дела", "/app/planner"),
            ("tasks", "Поручения агенту", "/app/tasks"),
            ("calendar", "Календарь", "/app/task-hub/calendar"),
            ("sources", "Задачи из сервисов", "/app/task-hub/sources"),
        ],
        active_tab="planner",
        view=view,
        editing=editing,
        status_labels=STATUS_LABELS,
        priority_labels=PRIORITY_LABELS,
        can_create=has_permission(actor, "plannertask", ActionType.CREATE),
        can_update=has_permission(actor, "plannertask", ActionType.UPDATE),
        can_remind=can_remind(actor),
        reminders=reminders,
        reminder_error_labels=REMINDER_ERROR_LABELS,
        delivery_error_labels=DELIVERY_ERROR_LABELS,
        can_delete=has_permission(actor, "plannertask", ActionType.DELETE),
        can_draft_greeting=has_permission(actor, "source", ActionType.CREATE),
        event_settings=event_settings,
        greeting_ids=greeting_ids,
        **page,
    )


def can_remind(actor):
    return has_permission(actor, "plannertask", ActionType.UPDATE)


async def form_values(request):
    form = await request.form()
    if not ensure_csrf(request, form.get("_csrf", "")):
        raise HTTPException(403, "Invalid CSRF token")
    # Reject duplicate keys instead of silently choosing a different reviewed value.
    if len(list(form.keys())) != len(list(form.multi_items())):
        raise HTTPException(422, "Duplicate planner fields")
    values = {key: value for key, value in form.items() if key != "_csrf"}
    for key in ("assignee_id", "parent_id", "contact_id", "expected_version"):
        if key in values:
            raw = values[key]
            if raw == "" and key != "expected_version":
                values[key] = None
            elif isinstance(raw, str) and raw.isascii() and raw.isdecimal() and not raw.startswith("0"):
                values[key] = int(raw)
            else:
                raise HTTPException(422, "Invalid identifier")
    for key in ("due_at", "due_date", "recurrence", "remind_at"):
        if values.get(key) == "":
            values[key] = None
    if "tags" in values:
        values["tags"] = [tag.strip() for tag in values["tags"].split(",") if tag.strip()]
    return values


@router.post("/planner")
async def create_task(request: Request):
    values = await form_values(request)
    try:
        data = TaskCreate.model_validate(values)
    except ValidationError:
        raise HTTPException(422, "Invalid planner fields") from None
    await invoke(planner_service.create(data))
    return RedirectResponse("/app/planner", status_code=303)


@router.post("/planner/events/settings")
async def set_event_settings(request: Request):
    values = await form_values(request)
    if set(values) != {"enabled"} or values["enabled"] not in {"on", "off"}:
        raise HTTPException(422, "Invalid event planning setting")
    async with async_session_maker() as session, session.begin():
        await invoke(event_planning_service.set_enabled(values["enabled"] == "on", session=session))
    return RedirectResponse("/app/planner", status_code=303)


@router.post("/planner/events/{task_id}/draft")
async def create_event_draft(request: Request, task_id: int = Path(gt=0)):
    values = await form_values(request)
    if values:
        raise HTTPException(422, "Unexpected fields")
    try:
        async with async_session_maker() as session, session.begin():
            draft = await event_planning_service.draft_for_task(task_id, session=session)
    except (PlannerUnavailable, EventPlanningUnavailable):
        raise HTTPException(404, "Event unavailable") from None
    except PermissionError:
        raise HTTPException(403, "Greeting draft access denied") from None
    except Bitrix24Error as exc:
        raise HTTPException(exc.status_code, exc.code) from None
    return render(request, "web/planner_event_draft.html", section="tasks", draft=draft)


@router.post("/planner/{task_id}/{operation}")
async def change_task(
    request: Request, task_id: int = Path(gt=0), operation: Literal["edit", "complete", "reschedule", "delete"] = "edit"
):
    values = await form_values(request)
    try:
        if operation == "edit":
            work = planner_service.update(task_id, TaskUpdate.model_validate(values))
        elif operation == "reschedule":
            work = planner_service.reschedule(task_id, TaskReschedule.model_validate(values))
        else:
            version = TaskVersion.model_validate(values)
            work = getattr(planner_service, operation)(task_id, version.expected_version)
    except ValidationError:
        raise HTTPException(422, "Invalid planner fields") from None
    await invoke(work)
    return RedirectResponse("/app/planner", status_code=303)


@router.post("/planner-reminders/{task_id}/create")
async def create_reminder(request: Request, task_id: int = Path(gt=0)):
    actor = get_current_user()
    if not can_remind(actor):
        raise HTTPException(403, "Planner access denied")
    values = await form_values(request)
    try:
        data = ReminderCreate.model_validate(values)
    except ValidationError:
        raise HTTPException(422, "Invalid reminder fields") from None
    await invoke(reminder_service.create(task_id, data))
    return RedirectResponse(f"/app/planner/{task_id}/edit#reminders", status_code=303)


@router.post("/planner-reminders/{reminder_id}/{operation}")
async def change_reminder(
    request: Request,
    reminder_id: int = Path(gt=0),
    operation: Literal["snooze", "reschedule", "cancel"] = "cancel",
):
    actor = get_current_user()
    if not can_remind(actor):
        raise HTTPException(403, "Planner access denied")
    values = await form_values(request)
    try:
        if operation == "snooze":
            work = reminder_service.snooze(reminder_id, ReminderSnooze.model_validate(values))
        elif operation == "reschedule":
            work = reminder_service.reschedule(reminder_id, ReminderReschedule.model_validate(values))
        else:
            data = ReminderVersion.model_validate(values)
            work = reminder_service.cancel(reminder_id, data.expected_version)
    except ValidationError:
        raise HTTPException(422, "Invalid reminder fields") from None
    result = await invoke(work)
    return RedirectResponse(f"/app/planner/{result.task_id}/edit#reminders", status_code=303)
