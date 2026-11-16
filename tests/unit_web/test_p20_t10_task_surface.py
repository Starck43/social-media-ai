"""Source-only contracts for the unified task surface read and handoff."""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio

from app.services.workspace import task_surface as surface

__source_only__ = True


class AsyncContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class FakeSession(AsyncContext):
    def begin(self):
        return AsyncContext()


class FakePermission:
    def __init__(self, allowed):
        self.allowed = set(allowed)

    def __call__(self, _actor, resource, _action):
        return resource in self.allowed


@pytest.mark.parametrize(
    ("value", "zone", "expected"),
    [
        (date(2026, 11, 1), ZoneInfo("America/New_York"), "2026-11-01"),
        (datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc), ZoneInfo("America/New_York"), "2026-11-01T01:30-04:00"),
        (datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc), ZoneInfo("America/New_York"), "2026-11-01T01:30-05:00"),
    ],
)
def test_calendar_time_preserves_all_day_and_dst_fold(value, zone, expected):
    assert surface.display_time(value, zone) == expected


def test_calendar_rejects_naive_and_missing_schedule():
    with pytest.raises(ValueError, match="naive_schedule"):
        surface.display_time(datetime(2026, 3, 8, 2, 30), ZoneInfo("America/New_York"))
    with pytest.raises(ValueError, match="missing_schedule"):
        surface.display_time(None, ZoneInfo("UTC"))


@pytest.mark.parametrize("cursor", [0, -1, True, "3", 3.0])
def test_calendar_cursor_requires_positive_strict_integer(cursor):
    with pytest.raises(ValueError, match="invalid_cursor"):
        surface._cursor(cursor)


@pytest.mark.asyncio
async def test_calendar_filters_each_kind_by_its_own_permission(monkeypatch):
    monkeypatch.setattr(surface, "has_permission", FakePermission({"agenttask"}))
    monkeypatch.setattr(surface, "get_current_user", lambda: object())
    monkeypatch.setattr(surface, "async_session_maker", FakeSession)
    planner = NS(list=AsyncMock())
    calendar = NS(events=AsyncMock())
    monkeypatch.setattr(surface, "planner_service", planner)
    monkeypatch.setattr(surface, "calendar_service", calendar)
    agent_page = AsyncMock(return_value=([], None))
    monkeypatch.setattr(surface, "_agent_page", agent_page)

    result = await surface.calendar_view(timezone_name="UTC")

    assert [provider["status"] for provider in result["providers"]] == ["denied", "denied", "available"]
    planner.list.assert_not_awaited()
    calendar.events.assert_not_awaited()
    agent_page.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["source_unavailable", "reauth", "sync_required"])
async def test_unavailable_calendar_is_not_projected_as_empty(monkeypatch, state):
    monkeypatch.setattr(surface, "has_permission", FakePermission({"plannertask", "agenttask"}))
    monkeypatch.setattr(surface, "get_current_user", lambda: object())
    monkeypatch.setattr(surface, "async_session_maker", FakeSession)
    planner = NS(list=AsyncMock(return_value={"items": [], "next_before_id": None}))
    calendar = NS(events=AsyncMock(return_value={"state": state, "items": None, "next_before_id": None}))
    monkeypatch.setattr(surface, "planner_service", planner)
    monkeypatch.setattr(surface, "calendar_service", calendar)
    monkeypatch.setattr(surface, "_agent_page", AsyncMock(return_value=([], None)))

    result = await surface.calendar_view()

    event_provider = next(row for row in result["providers"] if row["kind"] == "event")
    assert event_provider["status"] == state
    assert event_provider["items"] == []
    assert next(row for row in result["providers"] if row["kind"] == "planner")["status"] == "available"


@pytest.mark.asyncio
async def test_calendar_source_exception_discards_partial_provider_items(monkeypatch):
    monkeypatch.setattr(surface, "has_permission", FakePermission({"plannertask", "agenttask"}))
    monkeypatch.setattr(surface, "get_current_user", lambda: object())
    monkeypatch.setattr(surface, "async_session_maker", FakeSession)
    planner = NS(list=AsyncMock(return_value={"items": [], "next_before_id": None}))
    calendar = NS(events=AsyncMock(side_effect=RuntimeError("provider down")))
    monkeypatch.setattr(surface, "planner_service", planner)
    monkeypatch.setattr(surface, "calendar_service", calendar)
    monkeypatch.setattr(surface, "_agent_page", AsyncMock(return_value=([], None)))

    result = await surface.calendar_view()

    event_provider = next(row for row in result["providers"] if row["kind"] == "event")
    assert event_provider == {"kind": "event", "status": "source_unavailable", "items": [], "next_before_id": None}


@pytest.mark.asyncio
async def test_calendar_keeps_only_owned_planner_items_and_preserves_independent_cursors(monkeypatch):
    planner_items = [
        NS(
            id=1,
            title="Due",
            due_at=datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc),
            due_date=None,
            recurrence_source="planner",
            status="todo",
        ),
        NS(
            id=2,
            title="Calendar linked",
            due_at=None,
            due_date=date(2026, 11, 1),
            recurrence_source="calendar",
            status="todo",
        ),
        NS(id=3, title="Closed", due_at=None, due_date=None, recurrence_source="planner", status="done"),
    ]
    planner_list = AsyncMock(return_value={"items": planner_items, "next_before_id": 11})
    calendar_events = AsyncMock(return_value={"state": "available", "items": [], "next_before_id": 22})
    agent_page = AsyncMock(
        return_value=([NS(id=7, name="Run", next_run_at=datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc))], 33)
    )
    monkeypatch.setattr(surface, "has_permission", FakePermission({"plannertask", "agenttask"}))
    monkeypatch.setattr(surface, "get_current_user", lambda: object())
    monkeypatch.setattr(surface, "async_session_maker", FakeSession)
    monkeypatch.setattr(surface, "planner_service", NS(list=planner_list))
    monkeypatch.setattr(surface, "calendar_service", NS(events=calendar_events))
    monkeypatch.setattr(surface, "_agent_page", agent_page)

    result = await surface.calendar_view(
        timezone_name="America/New_York", before_planner=10, before_event=20, before_agent=30
    )

    planner = next(row for row in result["providers"] if row["kind"] == "planner")
    assert [item["key"] for item in planner["items"]] == ["planner:1"]
    assert planner["items"][0]["when"] == "2026-11-01T01:30-05:00"
    assert len({item["key"] for row in result["providers"] for item in row["items"]}) == 2
    assert [planner_list.await_args.kwargs["before_id"], calendar_events.await_args.kwargs["before_id"]] == [10, 20]
    assert agent_page.await_args.args[1] == 30
    assert [row["next_before_id"] for row in result["providers"]] == [11, 22, 33]


@pytest.mark.asyncio
async def test_linked_assignment_stores_only_scoped_reference_and_rejects_stale_task(monkeypatch):
    session = FakeSession()
    monkeypatch.setattr(surface, "async_session_maker", lambda: session)
    monkeypatch.setattr(surface, "actor_identity", AsyncMock(return_value=(4, 8, "member")))
    monkeypatch.setattr(surface.planner_service, "_actor", AsyncMock(return_value=(4, 8, "member")))
    monkeypatch.setattr(surface.planner_service, "_task", AsyncMock(return_value=NS(id=12, version=3)))
    persisted = AsyncMock(return_value=NS(id=90))
    monkeypatch.setattr(surface, "persist_task_create", persisted)
    task_values = {"name": "Research", "payload": {"goal": "Find sources"}}

    await surface.create_linked_assignment(
        task_values=task_values, source_ids=[], tenant_id=4, planner_id=12, expected_version=3
    )

    payload = persisted.await_args.kwargs["task_values"]["payload"]
    assert payload == {"goal": "Find sources", "planner_reference": {"tenant_id": 4, "task_id": 12}}
    assert task_values["payload"] == {"goal": "Find sources"}

    surface.planner_service._task.return_value.version = 4
    with pytest.raises(surface.PlannerConflict):
        await surface.create_linked_assignment(
            task_values=task_values, source_ids=[], tenant_id=4, planner_id=12, expected_version=3
        )
    assert persisted.await_count == 1


@pytest.mark.asyncio
async def test_linked_assignment_rejects_invalid_reference_before_writing(monkeypatch):
    persisted = AsyncMock()
    monkeypatch.setattr(surface, "persist_task_create", persisted)
    with pytest.raises(ValueError, match="invalid_planner_reference"):
        await surface.create_linked_assignment(
            task_values={}, source_ids=[], tenant_id=4, planner_id=0, expected_version=1
        )
    persisted.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["agent", "job", "planner"])
async def test_result_preview_fails_closed_when_either_linked_record_is_missing(monkeypatch, missing):
    session = FakeSession()
    monkeypatch.setattr(surface, "async_session_maker", lambda: session)
    monkeypatch.setattr(surface.planner_service, "_actor", AsyncMock(return_value=(4, 8, "member")))
    monkeypatch.setattr(surface, "actor_identity", AsyncMock())
    agent = None if missing == "agent" else NS(payload={"planner_reference": {"tenant_id": 4, "task_id": 12}})
    job = None if missing == "job" else NS(id=9)
    session.scalar = AsyncMock(side_effect=[agent, job])
    planner_task = (
        AsyncMock(side_effect=surface.PlannerUnavailable("invisible"))
        if missing == "planner"
        else AsyncMock(return_value=NS(id=12))
    )
    monkeypatch.setattr(surface.planner_service, "_task", planner_task)
    monkeypatch.setattr(surface.planner_service, "get", AsyncMock(return_value=NS(id=12)))

    with pytest.raises(surface.PlannerUnavailable):
        await surface._result(session, 90, 9)


@pytest.mark.asyncio
async def test_result_preview_checks_tenant_and_job_task_status_in_query(monkeypatch):
    session = FakeSession()
    statements = []

    async def scalar(statement):
        statements.append(str(statement.compile(compile_kwargs={"literal_binds": True})))
        if len(statements) == 1:
            return NS(payload={"planner_reference": {"tenant_id": 4, "task_id": 12}})
        return NS(id=9)

    session.scalar = scalar
    monkeypatch.setattr(surface.planner_service, "_actor", AsyncMock(return_value=(4, 8, "member")))
    monkeypatch.setattr(surface, "actor_identity", AsyncMock())
    monkeypatch.setattr(surface.planner_service, "_task", AsyncMock(return_value=NS(id=12)))
    task = NS(id=12, title="Plan")
    planner_get = AsyncMock(return_value=task)
    monkeypatch.setattr(surface.planner_service, "get", planner_get)

    assert await surface._result(session, 90, 9) is task
    assert "agent_tasks.tenant_id = 4" in statements[0]
    assert "agent_tasks.id = 90" in statements[0]
    assert "jobs.tenant_id = 4" in statements[1]
    assert "jobs.id = 9" in statements[1]
    assert "jobs.agent_task_id = 90" in statements[1]
    assert "jobs.status = 'done'" in statements[1]
    planner_get.assert_awaited_once_with(12, session=session)


@pytest.mark.asyncio
async def test_result_application_requires_explicit_confirmation_and_current_version(monkeypatch):
    session = FakeSession()
    monkeypatch.setattr(surface, "async_session_maker", lambda: session)
    task = NS(id=12, version=3, status="todo", description="Existing")
    monkeypatch.setattr(surface, "_result", AsyncMock(return_value=task))
    planner_update = AsyncMock(return_value="updated")
    monkeypatch.setattr(surface.planner_service, "update", planner_update)
    planner_complete = AsyncMock(return_value=NS(task="completed"))
    monkeypatch.setattr(surface.planner_service, "complete", planner_complete)

    with pytest.raises(ValueError, match="invalid_result_decision"):
        await surface.apply_result(90, 9, 3, operation="ignore")
    with pytest.raises(ValueError, match="annotation_required"):
        await surface.apply_result(90, 9, 3, operation="annotate", annotation="  ")
    monkeypatch.setattr(surface, "_result", AsyncMock(return_value=NS(**{**task.__dict__, "version": 4})))
    with pytest.raises(surface.PlannerConflict):
        await surface.apply_result(90, 9, 3, operation="complete")
    planner_update.assert_not_awaited()
    planner_complete.assert_not_awaited()

    monkeypatch.setattr(surface, "_result", AsyncMock(return_value=task))
    updated = await surface.apply_result(90, 9, 3, operation="annotate", annotation=" Reviewed ")
    assert updated == "updated"
    request = planner_update.await_args.args[1]
    assert request.description == "Existing\n\nReviewed\n/app/tasks/90 (Job #9)"
    assert request.scope == "occurrence"

    monkeypatch.setattr(surface, "_result", AsyncMock(return_value=task))
    completed = await surface.apply_result(90, 9, 3, operation="complete")
    assert completed == "completed"
    planner_complete.assert_awaited_once_with(12, 3, session=session)


@pytest.mark.asyncio
async def test_quick_create_validates_origin_through_owner_before_planner_write(monkeypatch):
    session = FakeSession()
    monkeypatch.setattr(surface, "async_session_maker", lambda: session)
    monkeypatch.setattr(surface.planner_service, "_actor", AsyncMock(return_value=(4, 8, "member")))
    origin = AsyncMock(return_value={"kind": "document", "id": 6, "href": "/app/documents/manage/6?boundary=cloud"})
    monkeypatch.setattr(surface, "quick_origin", origin)
    create = AsyncMock(return_value="created")
    monkeypatch.setattr(surface.planner_service, "create", create)

    result = await surface.quick_create("Call supplier", "Ask about delivery", origin="document", origin_id=6)

    assert result == "created"
    request = create.await_args.args[0]
    assert isinstance(request, surface.TaskCreate)
    assert request.description == "Ask about delivery\n\n/app/documents/manage/6?boundary=cloud"
    origin.assert_awaited_once_with(origin="document", origin_id=6, boundary="cloud", thread_id=None, session=session)


@pytest.mark.asyncio
async def test_quick_origin_rejects_unknown_kind_and_nonpositive_ids():
    with pytest.raises(ValueError, match="invalid_origin"):
        await surface.quick_origin(origin="calendar", origin_id=4, session=FakeSession())
    with pytest.raises(ValueError, match="invalid_origin"):
        await surface.quick_origin(origin="message", origin_id=0, session=FakeSession())


@pytest.mark.asyncio
async def test_quick_origin_document_is_resolved_by_document_owner(monkeypatch):
    owner_get = AsyncMock(return_value=NS(id=6))
    monkeypatch.setattr("app.services.knowledge.documents.document_service.get", owner_get)
    session = FakeSession()

    result = await surface.quick_origin(origin="document", origin_id=6, boundary="local_only", session=session)

    assert result == {"kind": "document", "id": 6, "href": "/app/documents/manage/6?boundary=local_only"}
    owner_get.assert_awaited_once_with(6, boundary="local_only", session=session)


@pytest.mark.asyncio
async def test_quick_origin_message_checks_fresh_permission_and_visible_thread(monkeypatch):
    from app.models.communications.message import UnifiedMessage

    session = FakeSession()
    session.scalar = AsyncMock(return_value=NS(id=15, is_unavailable=False, is_hidden=False))
    actor_check = AsyncMock()
    monkeypatch.setattr(surface, "actor_identity", actor_check)
    thread_check = AsyncMock()
    monkeypatch.setattr("app.services.communications.inbox.get_thread_detail", thread_check)

    result = await surface.quick_origin(origin="message", origin_id=15, thread_id=7, session=session)

    assert result == {"kind": "message", "id": 15, "href": "/app/communications?thread_id=7"}
    actor_check.assert_awaited_once_with(session, "unifiedmessage")
    thread_check.assert_awaited_once_with(session, 7)
    statement = session.scalar.await_args.args[0]
    sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
    assert "unified_messages.tenant_id" in sql
    assert "unified_messages.thread_id = 7" in sql
    assert "unified_messages.id = 15" in sql
    assert "unified_messages.is_hidden IS false" in sql


@pytest.mark.asyncio
async def test_quick_origin_unavailable_message_fails_closed(monkeypatch):
    session = FakeSession()
    session.scalar = AsyncMock(return_value=None)
    monkeypatch.setattr(surface, "actor_identity", AsyncMock())
    monkeypatch.setattr("app.services.communications.inbox.get_thread_detail", AsyncMock())

    with pytest.raises(surface.PlannerUnavailable):
        await surface.quick_origin(origin="message", origin_id=15, thread_id=7, session=session)


def _task_surface_templates():
    from pathlib import Path
    from urllib.parse import quote

    from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, StrictUndefined

    root = Path(__file__).resolve().parents[2] / "app/web/templates"
    env = Environment(
        loader=ChoiceLoader(
            [DictLoader({"web/base.html": "{% block content %}{% endblock %}"}), FileSystemLoader(root)]
        ),
        undefined=StrictUndefined,
        autoescape=True,
    )
    env.filters["urlencode"] = lambda value: quote(str(value), safe="")
    return env


def _tab_context():
    return {
        "tabs": (
            ("planner", "Мои дела", "/app/planner"),
            ("tasks", "Поручения агенту", "/app/tasks"),
            ("calendar", "Календарь", "/app/task-hub/calendar"),
        ),
        "active_tab": "calendar",
        "perms": NS(can=lambda _resource, _action: True),
        "csrf": "token",
    }


@pytest.mark.parametrize("status", ["source_unavailable", "reauth", "sync_required", "not_configured"])
def test_calendar_template_exposes_unavailable_provider_state(status):
    env = _task_surface_templates()
    context = _tab_context()
    context.update(
        timezone="America/New_York",
        providers=[
            {"kind": "planner", "status": "available", "items": [], "next_before_id": None},
            {"kind": "event", "status": status, "items": [], "next_before_id": None},
            {"kind": "agent", "status": "denied", "items": [], "next_before_id": None},
        ],
    )

    html = env.get_template("web/task_calendar.html").render(**context)

    event_section = html.split('aria-labelledby="calendar-event"', 1)[1].split("</section>", 1)[0]
    assert "На этой странице элементов нет" not in event_section
    assert (
        "сейчас недоступен" in event_section
        or "Выберите календарь" in event_section
        or "Обновите подключённый календарь" in event_section
        or "Подключите календарь заново" in event_section
    )
    assert "На этой странице элементов нет" in html  # planner has an independent, available empty state
    assert "Нет доступа к этому типу элементов" in html
    assert 'aria-label="Разделы задач"' in html
    assert 'aria-labelledby="calendar-event"' in html
    assert "America/New_York" in html


def test_quick_create_empty_context_has_accessible_csrf_form_and_no_nested_navigation():
    env = _task_surface_templates()

    html = env.get_template("web/task_quick_create.html").render(
        **_tab_context(), context=None, origin=None, origin_id=None, boundary="cloud", thread_id=None
    )

    assert html.count("<nav") == 1
    assert 'aria-label="Разделы задач"' in html
    assert 'action="/app/task-hub/new"' in html
    assert 'name="_csrf" value="token"' in html
    assert 'name="title"' in html and "required" in html
    assert "Сообщение" not in html and "своём разделе" in html


def test_result_confirmation_template_shows_task_version_and_explicit_consent():
    env = _task_surface_templates()
    context = _tab_context()
    context.update(task=NS(title="Согласовать договор", version=7), agent_id=4, job_id=9)

    html = env.get_template("web/task_result_confirm.html").render(**context)

    assert 'name="expected_version" value="7"' in html
    assert 'name="confirm" value="yes" required' in html
    assert 'name="operation"' in html
    assert "Подтвердить и применить" in html
    assert html.count("<nav") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_row", "workspace_row", "member_row", "role", "grants"),
    [
        (None, 4, NS(role_id=2), "member", {("plannertask", surface.ActionType.VIEW)}),
        (8, 4, None, None, set()),
        (8, 4, NS(role_id=2), "revoked", set()),
    ],
)
async def test_actor_identity_fails_closed_for_revoked_user_membership_or_role(
    monkeypatch, user_row, workspace_row, member_row, role, grants
):
    actor = NS(id=8, is_active=True)
    session = FakeSession()
    session.scalar = AsyncMock(side_effect=[user_row, workspace_row, member_row, role])
    monkeypatch.setattr(surface, "current_tenant_id", lambda: 4)
    monkeypatch.setattr(surface, "get_current_user", lambda: actor)
    monkeypatch.setattr(surface, "is_bypass", lambda: False)
    monkeypatch.setattr(surface, "workspace_grants", lambda actual_role: grants if actual_role == role else set())

    with pytest.raises(PermissionError, match="task_surface_denied"):
        await surface.actor_identity(session, "plannertask")


@pytest.mark.asyncio
async def test_linked_assignment_locks_workspace_before_reading_planner(monkeypatch):
    session = FakeSession()
    calls = []
    monkeypatch.setattr(surface, "async_session_maker", lambda: session)

    async def actor(_session, resource, action=surface.ActionType.VIEW, *, write_lock=False):
        calls.append(("identity", resource, action, write_lock))
        return (4, 8, "member")

    async def task(_session, planner_id, _identity, *, lock):
        calls.append(("planner", planner_id, lock))
        return NS(id=planner_id, version=3)

    monkeypatch.setattr(surface, "actor_identity", actor)
    monkeypatch.setattr(surface.planner_service, "_task", task)
    monkeypatch.setattr(surface, "persist_task_create", AsyncMock(return_value=NS(id=90)))

    await surface.create_linked_assignment(
        task_values={}, source_ids=[], tenant_id=4, planner_id=12, expected_version=3
    )

    assert calls[:3] == [
        ("identity", "plannertask", surface.ActionType.VIEW, True),
        ("identity", "agenttask", surface.ActionType.CREATE, False),
        ("planner", 12, True),
    ]


@pytest.mark.parametrize(
    ("payload", "title", "when"),
    [
        ({"summary": "Call", "start": {"date": "2026-11-10"}}, "Call", "2026-11-10"),
        ({"summary": "Review", "start": {"dateTime": "2026-11-10T01:30:00-05:00"}}, "Review", "2026-11-10T01:30-05:00"),
    ],
)
def test_calendar_google_projection_keeps_provider_owned_all_day_and_timed_values(payload, title, when):
    projected_title, projected_when = surface.event_schedule({"source": {"payload": payload}})
    assert projected_title == title
    assert surface.display_time(projected_when, ZoneInfo("America/New_York")) == when


def test_calendar_caldav_projection_reads_master_event_without_expanding_recurrence():
    ical = """BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nUID:task@example\nSUMMARY:Weekly review\nDTSTART;TZID=America/New_York:20261101T013000\nRRULE:FREQ=WEEKLY\nEND:VEVENT\nEND:VCALENDAR\n"""

    title, when = surface.event_schedule({"source": {"payload": {"icalendar": ical}}})

    assert title == "Weekly review"
    assert surface.display_time(when, ZoneInfo("America/New_York")) == "2026-11-01T01:30-04:00"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("agent_tenant", "reference", "permitted", "lookup_result"),
    [
        (4, {"tenant_id": 5, "task_id": 12}, True, NS(id=12)),
        (5, {"tenant_id": 4, "task_id": 12}, True, NS(id=12)),
        (4, {"tenant_id": 4, "task_id": 12}, False, NS(id=12)),
        (4, {"tenant_id": 4, "task_id": 12}, True, None),
    ],
)
async def test_linked_planner_hides_foreign_deleted_or_inaccessible_references(
    monkeypatch, agent_tenant, reference, permitted, lookup_result
):
    monkeypatch.setattr(surface, "current_tenant_id", lambda: 4)
    monkeypatch.setattr(surface, "get_current_user", lambda: object())
    monkeypatch.setattr(surface, "has_permission", lambda *_args: permitted)
    lookup = (
        AsyncMock(side_effect=surface.PlannerUnavailable("opaque"))
        if lookup_result is None
        else AsyncMock(return_value=lookup_result)
    )
    monkeypatch.setattr(surface.planner_service, "get", lookup)
    agent = NS(tenant_id=agent_tenant, payload={"planner_reference": reference})

    result = await surface.linked_planner(agent)

    assert result == {"status": "unavailable"}
    if permitted and agent_tenant == 4 and reference["tenant_id"] == 4:
        lookup.assert_awaited_once_with(12)
    else:
        lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_linked_planner_returns_live_task_only_after_permission_check(monkeypatch):
    task = NS(id=12, title="Current title")
    lookup = AsyncMock(return_value=task)
    monkeypatch.setattr(surface, "current_tenant_id", lambda: 4)
    monkeypatch.setattr(surface, "get_current_user", lambda: object())
    monkeypatch.setattr(
        surface,
        "has_permission",
        lambda _actor, resource, action: (resource, action) == ("plannertask", surface.ActionType.VIEW),
    )
    monkeypatch.setattr(surface.planner_service, "get", lookup)

    result = await surface.linked_planner(
        NS(tenant_id=4, payload={"planner_reference": {"tenant_id": 4, "task_id": 12}})
    )

    assert result == {"status": "available", "task": task}
    lookup.assert_awaited_once_with(12)


@pytest.mark.asyncio
async def test_linked_planner_legacy_unlinked_agent_task_has_no_copy():
    assert await surface.linked_planner(NS(tenant_id=4, payload={})) is None


@pytest_asyncio.fixture
async def task_surface_client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse
    from httpx import ASGITransport, AsyncClient

    from app.web import task_surface as routes

    grants = {
        ("plannertask", surface.ActionType.VIEW),
        ("agenttask", surface.ActionType.VIEW),
        ("plannertask", surface.ActionType.CREATE),
    }
    monkeypatch.setattr(routes, "get_current_user", lambda: NS(id=8))
    monkeypatch.setattr(routes, "has_permission", lambda _user, resource, action: (resource, action) in grants)
    monkeypatch.setattr(routes, "render", lambda _request, _template, **_context: HTMLResponse("<main>ok</main>"))
    app = FastAPI()
    app.include_router(routes.router)
    app.state.csrf_manager = NS(verify_token=lambda token: token == "valid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield NS(client=client, routes=routes, grants=grants)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("csrf", "confirm", "duplicate", "expected_status"),
    [("bad", "yes", False, 403), ("valid", "", False, 422), ("valid", "no", False, 422), ("valid", "yes", True, 403)],
)
async def test_result_post_rejects_csrf_missing_confirmation_and_duplicate_fields_without_mutation(
    task_surface_client, monkeypatch, csrf, confirm, duplicate, expected_status
):
    client = task_surface_client.client
    apply = AsyncMock()
    monkeypatch.setattr(surface, "apply_result", apply)
    fields = [
        ("_csrf", csrf),
        ("job_id", "9"),
        ("expected_version", "3"),
        ("operation", "complete"),
        ("confirm", confirm),
    ]
    if duplicate:
        fields.append(("confirm", "yes"))
    response = await client.post(
        "/task-hub/assignments/90/result",
        content=__import__("urllib.parse").parse.urlencode(fields),
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == expected_status
    apply.assert_not_awaited()


@pytest.mark.asyncio
async def test_result_get_previews_without_mutation_and_confirmed_post_passes_exact_decision(
    task_surface_client, monkeypatch
):
    client = task_surface_client.client
    preview = AsyncMock(return_value={"task": NS(title="Task", version=3), "agent_id": 90, "job_id": 9})
    apply = AsyncMock(return_value=NS(id=12))
    monkeypatch.setattr(surface, "result_preview", preview)
    monkeypatch.setattr(surface, "apply_result", apply)

    response = await client.get("/task-hub/assignments/90/result?job_id=9")

    assert response.status_code == 200
    preview.assert_awaited_once_with(90, 9)
    apply.assert_not_awaited()
    response = await client.post(
        "/task-hub/assignments/90/result",
        data={
            "_csrf": "valid",
            "job_id": "9",
            "expected_version": "3",
            "operation": "annotate",
            "annotation": "Done",
            "confirm": "yes",
        },
    )
    assert response.status_code == 303
    apply.assert_awaited_once_with(90, 9, 3, operation="annotate", annotation="Done")


@pytest.mark.asyncio
async def test_quick_create_get_requires_create_permission_before_source_lookup(task_surface_client, monkeypatch):
    client = task_surface_client.client
    task_surface_client.grants.discard(("plannertask", surface.ActionType.CREATE))
    lookup = AsyncMock()
    monkeypatch.setattr(surface, "quick_origin", lookup)

    response = await client.get("/task-hub/new?origin=document&origin_id=8")

    assert response.status_code == 403
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_quick_create_post_reports_missing_source_without_creating_planner_item(task_surface_client, monkeypatch):
    client = task_surface_client.client
    create = AsyncMock(side_effect=surface.PlannerUnavailable("source unavailable"))
    monkeypatch.setattr(surface, "quick_create", create)

    response = await client.post(
        "/task-hub/new",
        data={"_csrf": "valid", "title": "Call", "origin": "message", "origin_id": "8", "thread_id": "3"},
    )

    assert response.status_code == 404
    create.assert_awaited_once_with(
        title="Call", description="", origin="message", origin_id=8, boundary="cloud", thread_id=3
    )


@pytest.mark.asyncio
async def test_task_hub_routes_planner_only_actor_and_assignment_tab_stays_hidden(task_surface_client):
    client = task_surface_client.client
    task_surface_client.grants.discard(("agenttask", surface.ActionType.VIEW))

    response = await client.get("/task-hub", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/app/planner"
    tabs = task_surface_client.routes.task_tabs(NS())
    assert [tab[0] for tab in tabs] == ["planner", "calendar"]
    assert all(tab[0] != "tasks" for tab in tabs)
