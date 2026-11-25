"""Read-time task hub and confirmed handoff between the two owning aggregates.

Only opaque planner references live in agent payloads. No copied task/calendar
state, provider content, automatic completion, or additional persistence schema.
"""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.access_catalog import workspace_grants
from app.core.database import async_session_maker
from app.core.permissions import get_current_user, has_permission
from app.core.tenant_context import current_tenant_id, is_bypass
from app.models import AgentTask, Job, Tenant, TenantUser, User, WorkspaceRole
from app.services.calendar.service import calendar_service
from app.services.planner.contracts import TaskCreate, TaskUpdate
from app.services.planner.legacy_migration import compatibility_writer
from app.services.planner.tasks import PlannerConflict, PlannerUnavailable, planner_service
from app.services.task_creation import persist_task_create
from app.types import ActionType

PAGE_SIZE = 50
REFERENCE_KEY = "planner_reference"


async def actor_identity(session, resource, action=ActionType.VIEW, *, write_lock=False):
    """Fresh membership/grants, in the same lock order as the owning planner."""
    tenant, actor = current_tenant_id(), get_current_user()
    if is_bypass() or type(tenant) is not int or tenant <= 0 or actor is None or not actor.is_active:
        raise PermissionError("task_surface_denied")
    user = await session.scalar(
        select(User.id).where(User.id == actor.id, User.is_active.is_(True)).with_for_update(read=True)
    )
    workspace = await session.scalar(
        select(Tenant.id).where(Tenant.id == tenant, Tenant.is_active.is_(True)).with_for_update(read=not write_lock)
    )
    member = await session.scalar(
        select(TenantUser)
        .where(TenantUser.tenant_id == tenant, TenantUser.user_id == actor.id, TenantUser.is_active.is_(True))
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    role = (
        None
        if member is None
        else await session.scalar(
            select(WorkspaceRole.codename).where(WorkspaceRole.id == member.role_id).with_for_update(read=True)
        )
    )
    if user is None or workspace is None or (resource, action) not in workspace_grants(role):
        raise PermissionError("task_surface_denied")
    return tenant, actor.id, role


def _positive(value):
    return type(value) is int and value > 0


def _cursor(value):
    if value is not None and not _positive(value):
        raise ValueError("invalid_cursor")
    return value


def display_time(value, zone):
    """Dates stay all-day dates; aware instants retain their DST offset."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value) if "T" in value else date.fromisoformat(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("naive_schedule")
        return value.astimezone(zone).isoformat(timespec="minutes")
    if isinstance(value, date):
        return value.isoformat()
    raise ValueError("missing_schedule")


def calendar_schedule(value, zone):
    """Precompute the source list value plus grid day and clock in the tenant's zone.

    The page renders these values as-is: no browser-side timezone conversion.
    """
    displayed = display_time(value, zone)
    if "T" in displayed:
        day, clock = displayed.split("T", 1)
        return {"when": displayed, "date": day, "time": clock[:5]}
    return {"when": displayed, "date": displayed, "time": ""}


def event_schedule(row):
    """Project source-owned start only; never expand provider recurrence here."""
    payload = row["source"]["payload"]
    if "icalendar" in payload:
        from app.integrations.calendar.caldav import validate_ical

        resource = validate_ical(payload["icalendar"])
        event = next(e for e in resource.walk("VEVENT") if not e.get("RECURRENCE-ID"))
        return str(event.get("SUMMARY", "Событие")), event.decoded("DTSTART")
    start = payload["start"]
    return payload.get("summary") or "Событие", start.get("dateTime") or start["date"]


async def _agent_page(session, start_utc, end_utc, after=None):
    """Month schedules share the scheduler's eligibility and source visibility."""
    from app.models.managers.agent_task_manager import AgentTaskManager

    identity = await actor_identity(session, "agenttask")
    grants = workspace_grants(identity[2])
    include_sources = ("source", ActionType.VIEW) in grants and has_permission(
        get_current_user(), "source", ActionType.VIEW
    )
    page = await AgentTaskManager().upcoming_effective_range(
        start=start_utc,
        end=end_utc,
        include_source_based=include_sources,
        limit=PAGE_SIZE,
        after=after,
        include_prune=("job", ActionType.UPDATE) in grants,
    )
    rows = page["items"]
    if ("job", ActionType.UPDATE) not in grants:
        rows = [task for task in rows if task.job_type != "prune"]
    return rows, page["complete"]


async def calendar_view(
    *,
    timezone_name="UTC",
    year=None,
    month=None,
    before_planner=None,
    before_event=None,
    before_agent=None,
    after_agent_at=None,
    after_agent_id=None,
):
    zone = ZoneInfo(timezone_name)
    now = datetime.now(zone)
    year = now.year if year is None else year
    month = now.month if month is None else month
    if type(year) is not int or not 1900 <= year <= 2099 or type(month) is not int or not 1 <= month <= 12:
        raise ValueError("invalid_month")
    first_day = date(year, month, 1)
    next_day = date(year + (month == 12), month % 12 + 1, 1)
    start_utc = datetime(year, month, 1, tzinfo=zone).astimezone(timezone.utc)
    end_utc = datetime(next_day.year, next_day.month, 1, tzinfo=zone).astimezone(timezone.utc)
    if before_agent is not None:
        raise ValueError("agent_cursor_unsupported")
    if (after_agent_at is None) != (after_agent_id is None):
        raise ValueError("invalid_agent_cursor")
    agent_cursor = None
    if after_agent_at is not None:
        try:
            cursor_time = datetime.fromisoformat(after_agent_at)
            if cursor_time.tzinfo is None or cursor_time.utcoffset() is None:
                raise ValueError
            cursor_time = cursor_time.astimezone(timezone.utc)
            if not start_utc <= cursor_time < end_utc or not _positive(after_agent_id):
                raise ValueError
            agent_cursor = (cursor_time, after_agent_id)
        except (ValueError, TypeError, OverflowError):
            raise ValueError("invalid_agent_cursor") from None
    cursors = {"planner": _cursor(before_planner), "event": _cursor(before_event)}
    providers = []
    for kind, permission in (("planner", "plannertask"), ("event", "plannertask"), ("agent", "agenttask")):
        provider = {"kind": kind, "status": "denied", "items": [], "next_before_id": None}
        providers.append(provider)
        if not has_permission(get_current_user(), permission, ActionType.VIEW):
            continue
        try:
            async with async_session_maker() as session, session.begin():
                items = []
                if kind == "planner":
                    page = await planner_service.calendar_range(
                        session=session,
                        limit=PAGE_SIZE,
                        before_id=cursors[kind],
                        first_day=first_day,
                        next_day=next_day,
                        start_utc=start_utc,
                        end_utc=end_utc,
                    )
                    provider["next_before_id"] = page["next_before_id"]
                    for task in page["items"]:
                        # A calendar-owned planner occurrence is represented by its
                        # source event, never twice in the same calendar inventory.
                        if task.status not in {"todo", "in_progress"} or task.recurrence_source == "calendar":
                            continue
                        when = task.due_at or task.due_date
                        if when is not None:
                            items.append(
                                {
                                    "key": f"planner:{task.id}",
                                    "title": task.title,
                                    "href": f"/app/planner/{task.id}/edit",
                                    **calendar_schedule(when, zone),
                                }
                            )
                elif kind == "event":
                    page = await calendar_service.events_range(session=session, start=start_utc, end=end_utc)
                    if page["state"] == "range_unavailable":
                        # Preserve the local inventory as a separate list only.
                        # It is not a month range and must not enter the grid.
                        page = await calendar_service.events(session=session, limit=PAGE_SIZE, before_id=cursors[kind])
                        if page["state"] != "available":
                            provider["status"] = page["state"]
                            continue
                        provider["range_complete"] = False
                        provider["grid_eligible"] = False
                        provider["inventory_only"] = True
                        provider["next_before_id"] = page["next_before_id"]
                        for row in page["items"]:
                            if row["source"].get("deleted"):
                                continue
                            title, when = event_schedule(row)
                            items.append(
                                {
                                    "key": f"event:{row['id']}",
                                    "title": title,
                                    "href": None,
                                    "settings_href": "/app/settings?tab=connections",
                                    **calendar_schedule(when, zone),
                                }
                            )
                    elif page["state"] != "available":
                        provider["status"] = page["state"]
                        continue
                    else:
                        provider["range_complete"] = page["complete"]
                        if not page["complete"]:
                            provider["grid_eligible"] = False
                        for index, row in enumerate(page["items"]):
                            items.append(
                                {
                                    "key": f"event-range:{index}",
                                    "title": row["title"],
                                    "href": None,
                                    "settings_href": "/app/settings?tab=connections",
                                    **calendar_schedule(row["when"], zone),
                                }
                            )
                else:
                    rows, provider["range_complete"] = (
                        await _agent_page(session, start_utc, end_utc, agent_cursor)
                        if agent_cursor
                        else await _agent_page(session, start_utc, end_utc)
                    )
                    if not provider["range_complete"] and rows:
                        provider["next_agent_at"] = rows[-1].next_run_at.astimezone(timezone.utc).isoformat()
                        provider["next_agent_id"] = rows[-1].id
                    for task in rows:
                        if task.next_run_at is not None:
                            items.append(
                                {
                                    "key": f"agent:{task.id}",
                                    "title": task.name,
                                    "href": f"/app/tasks/{task.id}",
                                    **calendar_schedule(task.next_run_at, zone),
                                }
                            )
                provider.update(status="available", items=list({item["key"]: item for item in items}.values()))
        except PermissionError:
            provider["status"] = "denied"
        except PlannerConflict as exc:
            provider["status"] = "not_configured" if str(exc) == "calendar_choice_required" else "source_unavailable"
        except Exception:
            # Discard partial output; failures must never look like an empty source.
            provider.update(status="source_unavailable", items=[], next_before_id=None)
    return {"today": now.date().isoformat(), "year": year, "month": month, "providers": providers}


async def create_linked_assignment(*, task_values, source_ids, tenant_id, planner_id, expected_version):
    if not _positive(planner_id) or not _positive(expected_version):
        raise ValueError("invalid_planner_reference")
    async with async_session_maker() as session, session.begin():
        # Own the workspace write lock before source admission/task locks.
        identity = await actor_identity(session, "plannertask", write_lock=True)
        await actor_identity(session, "agenttask", ActionType.CREATE)
        task = await planner_service._task(session, planner_id, identity, lock=True)
        if tenant_id != identity[0] or task.version != expected_version:
            raise PlannerConflict("planner_changed")
        canonical_planner = await compatibility_writer.planner(session, task)
        payload = dict(task_values.get("payload") or {})
        payload[REFERENCE_KEY] = {"tenant_id": identity[0], "task_id": task.id}
        return await persist_task_create(
            session,
            task_values={**task_values, "payload": payload},
            source_ids=source_ids,
            tenant_id=tenant_id,
            canonical_owner_id=identity[1],
            attach_to_task_id=canonical_planner.id,
        )


async def _result(session, agent_id, job_id, *, write=False):
    if not _positive(agent_id) or not _positive(job_id):
        raise PlannerUnavailable("result_unavailable")
    # Planner write lock first prevents a read->write workspace lock upgrade.
    identity = await planner_service._actor(session, ActionType.UPDATE if write else ActionType.VIEW)
    await actor_identity(session, "agenttask")
    await actor_identity(session, "job")
    agent = await session.scalar(
        select(AgentTask).where(AgentTask.tenant_id == identity[0], AgentTask.id == agent_id).with_for_update(read=True)
    )
    job = await session.scalar(
        select(Job)
        .where(Job.tenant_id == identity[0], Job.id == job_id, Job.agent_task_id == agent_id, Job.status == "done")
        .with_for_update(read=True)
    )
    reference = (agent.payload or {}).get(REFERENCE_KEY) if agent is not None else None
    if (
        job is None
        or not isinstance(reference, dict)
        or reference.get("tenant_id") != identity[0]
        or not _positive(reference.get("task_id"))
    ):
        raise PlannerUnavailable("result_unavailable")
    task = await planner_service._task(session, reference["task_id"], identity, lock=write)
    return await planner_service.get(task.id, session=session)


async def result_preview(agent_id, job_id):
    async with async_session_maker() as session, session.begin():
        task = await _result(session, agent_id, job_id)
        return {"task": task, "agent_id": agent_id, "job_id": job_id}


async def apply_result(agent_id, job_id, expected_version, *, operation, annotation=""):
    if operation not in {"annotate", "complete"} or not _positive(expected_version):
        raise ValueError("invalid_result_decision")
    async with async_session_maker() as session, session.begin():
        task = await _result(session, agent_id, job_id, write=True)
        if task.version != expected_version or task.status in {"done", "cancelled"}:
            raise PlannerConflict("planner_changed")
        if operation == "complete":
            return (await planner_service.complete(task.id, expected_version, session=session)).task
        note = annotation.strip()
        if not note:
            raise ValueError("annotation_required")
        description = f"{task.description}\n\n{note}\n/app/tasks/{agent_id} (Job #{job_id})".strip()
        return await planner_service.update(
            task.id,
            TaskUpdate(expected_version=expected_version, scope="occurrence", description=description),
            session=session,
        )


async def quick_origin(*, origin=None, origin_id=None, boundary="cloud", thread_id=None, session=None):
    if origin is None and origin_id is None:
        return None
    if origin not in {"message", "document"} or not _positive(origin_id):
        raise ValueError("invalid_origin")
    if session is None:
        async with async_session_maker() as owned, owned.begin():
            return await quick_origin(
                origin=origin, origin_id=origin_id, boundary=boundary, thread_id=thread_id, session=owned
            )
    if origin == "document":
        from app.services.knowledge.documents import document_service

        await document_service.get(origin_id, boundary=boundary, session=session)
        href = f"/app/documents/manage/{origin_id}?boundary={boundary}"
    else:
        from app.models.communications.message import UnifiedMessage
        from app.services.communications.inbox import get_thread_detail

        if not _positive(thread_id):
            raise ValueError("thread_required")
        await actor_identity(session, "unifiedmessage")
        await get_thread_detail(session, thread_id)
        message = await session.scalar(
            select(UnifiedMessage).where(
                UnifiedMessage.tenant_id == current_tenant_id(),
                UnifiedMessage.thread_id == thread_id,
                UnifiedMessage.id == origin_id,
                UnifiedMessage.is_hidden.is_(False),
            )
        )
        if message is None or message.is_unavailable:
            raise PlannerUnavailable("origin_unavailable")
        href = f"/app/communications?thread_id={thread_id}"
    return {"kind": origin, "id": origin_id, "href": href}


async def quick_create(title, description="", *, origin=None, origin_id=None, boundary="cloud", thread_id=None):
    async with async_session_maker() as session, session.begin():
        # Lock planner first, then inspect the referenced source in the same UoW.
        await planner_service._actor(session, ActionType.CREATE)
        reference = await quick_origin(
            origin=origin, origin_id=origin_id, boundary=boundary, thread_id=thread_id, session=session
        )
        if reference:
            description = f"{description}\n\n{reference['href']}".strip()
        return await planner_service.create(TaskCreate(title=title, description=description), session=session)


async def linked_planner(agent):
    """Resolve a reference under current visibility; never show stale task text."""
    reference = (agent.payload or {}).get(REFERENCE_KEY)
    if reference is None:
        return None
    if (
        not isinstance(reference, dict)
        or reference.get("tenant_id") != current_tenant_id()
        or agent.tenant_id != current_tenant_id()
        or not _positive(reference.get("task_id"))
        or not has_permission(get_current_user(), "plannertask", ActionType.VIEW)
    ):
        return {"status": "unavailable"}
    try:
        task = await planner_service.get(reference["task_id"])
    except (LookupError, ValueError, PermissionError):
        return {"status": "unavailable"}
    return {"status": "available", "task": task}


async def canonical_task_actions(task_id, *, perms=None, boundary="cloud"):
    """Actor-specific projection for task detail, agent tools and the task API."""
    from app.models.planner import TaskRun, TaskTrigger
    from app.services.commands.task_decisions import task_decision_service
    from app.services.execution_plan_contracts import ExecutionPlanError
    from app.services.planner.canonical_tasks import task_service
    from app.services.task_execution_plans import execution_plan_service
    from app.services.task_run_admission import TaskRunAdmissionError, authorize_task_run, validate_admissible_plan

    async with async_session_maker() as session, session.begin():
        identity = await task_service.action_actor(session, ActionType.VIEW)
        task = await task_service._task(session, task_id, identity)
        latest = await session.scalar(
            select(TaskRun)
            .where(TaskRun.tenant_id == task.tenant_id, TaskRun.task_id == task.id)
            .order_by(TaskRun.id.desc())
            .limit(1)
        )
        active_run = await session.scalar(
            select(TaskRun.id).where(
                TaskRun.tenant_id == task.tenant_id,
                TaskRun.task_id == task.id,
                TaskRun.status.in_(("pending", "running")),
            )
        )
        next_due_at = await session.scalar(
            select(TaskTrigger.next_due_at).where(
                TaskTrigger.tenant_id == task.tenant_id, TaskTrigger.task_id == task.id, TaskTrigger.enabled
            )
        )
        trigger = await session.scalar(
            select(TaskTrigger)
            .where(TaskTrigger.tenant_id == task.tenant_id, TaskTrigger.task_id == task.id)
            .execution_options(populate_existing=True)
        )
        closed = task.status in {"done", "cancelled"}
        editable = (
            task.recurrence_source == "planner"
            and task_service.action_allowed(identity, ActionType.UPDATE)
            and await task_service._can_edit(session, task, identity)
        )
        actions = []
        if not closed and editable:
            actions.append({"reference": "edit", "label": "Редактировать"})
            if trigger is None:
                # A scheduled task is closed explicitly; a human occurrence keeps
                # its accepted one-click completion with the usual successor.
                actions.append({"reference": "complete", "label": "Завершить"})
            actions.append({"reference": "close", "label": "Закрыть", "confirmation_required": True})
            if active_run is None:
                try:
                    await authorize_task_run(session, task.id)
                    reference = await execution_plan_service.get_reference(task.id, session=session)
                    if reference is not None:
                        admitted = await execution_plan_service.load_for_admission(
                            task.id, reference.revision, session=session
                        )
                        validate_admissible_plan(admitted.plan)
                        actions.append({"reference": "run", "label": "Запустить", "revision": reference.revision})
                except (ExecutionPlanError, TaskRunAdmissionError, PermissionError):
                    pass
            if trigger is not None and (trigger.principal_id == identity[1] or not trigger.enabled):
                actions.append(
                    {
                        "reference": "schedule",
                        "label": "Расписание",
                        "revision": trigger.revision,
                        "enabled": bool(trigger.enabled),
                    }
                )
        if closed and editable:
            actions.append({"reference": "reopen", "label": "Открыть заново", "confirmation_required": True})
        if (
            task.recurrence_source == "planner"
            and task_service.action_allowed(identity, ActionType.DELETE)
            and task_service._can_share(task, identity)
        ):
            actions.append({"reference": "delete", "label": "Удалить", "confirmation_required": True})
        decisions = await task_decision_service.pending(task_id, perms=perms, boundary=boundary, session=session)
        return {
            "task_id": task.id,
            "version": task.version,
            "work_status": task.status,
            "latest_run": {"run_id": latest.id, "status": latest.status} if latest is not None else None,
            "next_due_at": next_due_at,
            "actions": actions,
            "pending_decisions": decisions,
        }
