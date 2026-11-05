"""Remote no-DB route only: explicit transaction/authority/socket boundaries."""

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError
from sqlalchemy import event
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.schema import CreateTable

from app.core.tenant_context import tenant_scope
from app.models.planner import PlannerTask
from app.services.planner import tasks
from app.services.planner.contracts import TaskCreate, TaskReschedule, TaskUpdate, TaskVersion
from app.services.planner.recurrence import next_occurrence, occurrence_key, parse_rule
from app.services.planner.tasks import PlannerConflict, PlannerService, PlannerUnavailable
from app.types import ActionType

__source_only__ = True
UTC = timezone.utc
ANCHOR = date(2099, 1, 31)


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    import app.core.database as database

    def forbidden(*args, **kwargs):
        raise AssertionError("real DB forbidden in planner unit tests")

    event.listen(Dialect, "do_connect", forbidden)
    monkeypatch.setattr(database, "async_session_maker", forbidden)
    monkeypatch.setattr(tasks, "get_current_user", lambda: SimpleNamespace(id=3, is_active=True))
    monkeypatch.setattr(
        tasks,
        "workspace_grants",
        lambda role: (
            ({("plannertask", action) for action in ActionType} | {("contact", ActionType.VIEW)})
            if role in {"owner", "manager", "member"}
            else set()
        ),
    )
    monkeypatch.setattr(tasks, "has_permission", lambda *args: True)
    with tenant_scope(7):
        yield
    event.remove(Dialect, "do_connect", forbidden)


def create_request(**changes):
    return TaskCreate.model_validate({"title": "Synthetic task", "due_date": ANCHOR, **changes})


def row(**changes):
    request = create_request()
    values = {
        **tasks._values(request),
        "id": 1,
        "tenant_id": 7,
        "owner_user_id": 3,
        "status": "todo",
        "version": 1,
        "series_id": 1,
        "previous_occurrence_id": None,
        "occurrence_index": 0,
        "occurrence_key": occurrence_key(request.recurrence, ANCHOR, ANCHOR),
        "recurrence_source": "planner",
        "series_template_encrypted": request.model_dump_json(),
        "completed_at": None,
    }
    values.update(changes)
    return PlannerTask(**values)


class Session:
    def __init__(self, values=(), rows=()):
        self.values, self.rows = list(values), list(rows)
        self.queries, self.added, self.deleted = [], [], []
        self.flushes = 0

    async def scalar(self, query):
        self.queries.append(query)
        return self.values.pop(0) if self.values else None

    async def scalars(self, query):
        self.queries.append(query)
        return SimpleNamespace(all=lambda: self.rows)

    def add(self, item):
        if item.id is None:
            item.id = len(self.added) + 20
        self.added.append(item)

    async def flush(self):
        self.flushes += 1

    async def delete(self, item):
        self.deleted.append(item)


@pytest.fixture
def service(monkeypatch):
    service = PlannerService()
    monkeypatch.setattr(service, "_actor", AsyncMock(return_value=(7, 3, "owner")))
    return service


def query_text(query):
    return str(query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


@pytest.mark.parametrize(
    "text",
    [
        "",
        "FREQ=YEARLY",
        "FREQ=DAILY;FREQ=WEEKLY",
        "FREQ=DAILY;BYDAY=MO",
        "FREQ=DAILY;INTERVAL=0",
        "FREQ=DAILY;INTERVAL=367",
        "FREQ=DAILY;COUNT=0",
        "FREQ=DAILY;COUNT=10001",
        "FREQ=DAILY;COUNT=2;UNTIL=20990101",
        "FREQ=DAILY;UNTIL=bad",
        " FREQ=DAILY",
        "FREQ=DAILY;",
        "FREQ=DAILY;COUNT=+2",
        "INTERVAL=1",
        "FREQ=DAILY;COUNT=2=3",
    ],
)
def test_strict_rrule_subset(text):
    with pytest.raises(ValueError):
        parse_rule(text, timed=False)


@pytest.mark.parametrize(
    "text,timed",
    [
        ("FREQ=DAILY;UNTIL=20990101", True),
        ("FREQ=DAILY;UNTIL=20990101T000000Z", False),
        ("FREQ=DAILY;UNTIL=20990230", False),
    ],
)
def test_until_type_and_validity(text, timed):
    with pytest.raises(ValueError):
        parse_rule(text, timed=timed)


def test_rule_normalization_and_count():
    assert parse_rule("COUNT=3;INTERVAL=2;FREQ=WEEKLY", timed=False).text == "FREQ=WEEKLY;INTERVAL=2;COUNT=3"
    assert next_occurrence(ANCHOR, "FREQ=DAILY;COUNT=1", after=ANCHOR, tz="UTC") is None
    assert next_occurrence(ANCHOR, "FREQ=WEEKLY;INTERVAL=2;COUNT=2", after=ANCHOR, tz="UTC") == (date(2099, 2, 14), 1)
    assert next_occurrence(ANCHOR, "FREQ=DAILY;UNTIL=20990131", after=ANCHOR, tz="UTC") is None


def test_monthly_skips_invalid_days_without_counting_them():
    rule = "FREQ=MONTHLY;COUNT=3"
    assert next_occurrence(date(2024, 1, 31), rule, after=date(2024, 1, 31), tz="UTC") == (date(2024, 3, 31), 1)
    assert next_occurrence(date(2024, 1, 31), rule, after=date(2024, 3, 31), tz="UTC") == (date(2024, 5, 31), 2)
    assert next_occurrence(date(2024, 1, 31), rule, after=date(2024, 5, 31), tz="UTC") is None
    assert next_occurrence(
        date(2024, 2, 29), "FREQ=MONTHLY;INTERVAL=12;COUNT=2", after=date(2024, 2, 29), tz="UTC"
    ) == (date(2028, 2, 29), 1)


def test_dst_spring_gap_is_skipped_not_shifted():
    zone = ZoneInfo("America/New_York")
    start = datetime(2026, 3, 7, 2, 30, tzinfo=zone)
    result, index = next_occurrence(start, "FREQ=DAILY;COUNT=2", after=start, tz=zone.key)
    assert result == datetime(2026, 3, 9, 6, 30, tzinfo=UTC) and index == 1


def test_dst_fall_uses_first_fold_and_local_wall_clock():
    zone = ZoneInfo("America/New_York")
    start = datetime(2026, 10, 31, 1, 30, tzinfo=zone)
    first, _ = next_occurrence(start, "FREQ=DAILY", after=start, tz=zone.key)
    assert first == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    second, _ = next_occurrence(start, "FREQ=DAILY", after=first, tz=zone.key)
    assert second == datetime(2026, 11, 2, 6, 30, tzinfo=UTC)


def test_timed_until_inclusive_and_identity_not_now():
    start = datetime(2026, 11, 5, 9, tzinfo=UTC)
    result = next_occurrence(start, "FREQ=DAILY;UNTIL=20261106T090000Z", after=start, tz="UTC")
    assert result == (datetime(2026, 11, 6, 9, tzinfo=UTC), 1)
    assert next_occurrence(start, "FREQ=DAILY;UNTIL=20261106T090000Z", after=result[0], tz="UTC") is None
    key = occurrence_key("FREQ=DAILY", start, result[0])
    assert key == occurrence_key("FREQ=DAILY", start.astimezone(ZoneInfo("Europe/Moscow")), result[0])
    assert len(key) == 64 and key != occurrence_key("FREQ=WEEKLY", start, result[0])


@pytest.mark.parametrize(
    "anchor,after", [(date(2026, 1, 1), datetime(2026, 1, 1, tzinfo=UTC)), (datetime(2026, 1, 1), datetime(2026, 1, 2))]
)
def test_incompatible_recurrence_inputs(anchor, after):
    with pytest.raises(ValueError):
        next_occurrence(anchor, "FREQ=DAILY", after=after, tz="UTC")


def test_search_is_bounded_and_date_overflow_stops():
    assert next_occurrence(date(9999, 12, 31), "FREQ=DAILY", after=date(9999, 12, 31), tz="UTC") is None
    assert next_occurrence(date(9999, 12, 31), "FREQ=MONTHLY", after=date(9999, 12, 31), tz="UTC") is None
    with pytest.raises(ValueError, match="search_limit"):
        next_occurrence(date(2000, 1, 1), "FREQ=DAILY", after=date(2100, 1, 1), tz="UTC")


@pytest.mark.parametrize(
    "changes",
    [
        {"title": " "},
        {"title": "x\x00"},
        {"description": "x\x00"},
        {"category": "x\x00"},
        {"due_at": "2026-11-10T09:00:00"},
        {"due_at": "2026-11-10T09:00:00Z"},
        {"timezone": "unknown/zone"},
        {"tags": ["one", "one"]},
        {"tags": [" "]},
        {"tags": ["x" * 61]},
        {"assignee_id": True},
        {"contact_id": 0},
        {"parent_id": -1},
        {"tenant_id": 8},
        {"owner_user_id": 4},
        {"priority": "urgent"},
        {"recurrence_source": "external"},
        {"recurrence": "FREQ=DAILY;UNTIL=20000101"},
    ],
)
def test_create_contract_fails_closed(changes):
    with pytest.raises(ValidationError):
        create_request(**changes)


def test_undated_recurrence_forbidden_and_update_scope_explicit():
    with pytest.raises(ValidationError):
        TaskCreate(title="one", recurrence="FREQ=DAILY")
    for payload in ({"expected_version": 1, "title": "one"}, {"scope": "series", "expected_version": True}):
        with pytest.raises(ValidationError):
            TaskUpdate.model_validate(payload)
    assert TaskCreate(title="undated").due_at is None


@pytest.mark.parametrize("tenant,bypass", [(None, False), (7, True), (True, False)])
async def test_actor_requires_explicit_non_bypass_scope(tenant, bypass):
    with tenant_scope(tenant, bypass=bypass), pytest.raises(PermissionError):
        await PlannerService()._actor(Session(), ActionType.VIEW)


@pytest.mark.parametrize(
    "values",
    [
        [None, 7, SimpleNamespace(role_id=2), "owner"],
        [3, None, SimpleNamespace(role_id=2), "owner"],
        [3, 7, None],
        [3, 7, SimpleNamespace(role_id=2), "viewer"],
    ],
)
async def test_actor_checks_fresh_user_workspace_member_role(values):
    with pytest.raises(PermissionError):
        await PlannerService()._actor(Session(values), ActionType.VIEW)


async def test_inactive_actor_refused_before_any_query(monkeypatch):
    monkeypatch.setattr(tasks, "get_current_user", lambda: SimpleNamespace(id=3, is_active=False))
    session = Session()
    with pytest.raises(PermissionError):
        await PlannerService()._actor(session, ActionType.CREATE)
    assert session.queries == []


@pytest.mark.parametrize("action,exclusive", [(ActionType.VIEW, False), (ActionType.UPDATE, True)])
async def test_fresh_authority_locks_and_workspace_write_serialization(action, exclusive):
    session = Session([3, 7, SimpleNamespace(role_id=2), "owner"])
    assert await PlannerService()._actor(session, action) == (7, 3, "owner")
    assert session.queries[1]._for_update_arg.read is not exclusive
    assert all(query._for_update_arg is not None for query in session.queries)
    assert "tenant_users.tenant_id = 7" in query_text(session.queries[2])


@pytest.mark.parametrize("task_id", [0, -1, True, "1"])
async def test_invalid_task_identifier_is_opaque(task_id):
    with pytest.raises(PlannerUnavailable):
        await PlannerService()._task(Session(), task_id, (7, 3, "owner"))


async def test_visibility_is_same_for_detail_and_locked_writes():
    session = Session([None])
    with pytest.raises(PlannerUnavailable):
        await PlannerService()._task(session, 55, (7, 3, "member"), lock=True)
    sql = query_text(session.queries[0])
    assert "tenant_id = 7" in sql and "owner_user_id = 3 OR" in sql and "assignee_id = 3" in sql
    assert session.queries[0]._for_update_arg is not None


async def test_create_owns_task_and_creates_no_future_rows(service):
    session = Session()
    request = create_request(recurrence="FREQ=MONTHLY;COUNT=3")
    result = await service.create(request, session=session)
    assert len(session.added) == 1 and result.owner_user_id == 3 and result.series_id == result.id
    assert result.recurrence_source == "planner" and result.recurrence == "FREQ=MONTHLY;INTERVAL=1;COUNT=3"
    assert session.added[0].tenant_id == 7 and session.flushes == 2
    assert result.title == request.title and "encrypted" not in result.model_dump()
    assert (await service.create(TaskCreate(title="Undated"), session=session)).occurrence_key == "once"


@pytest.mark.parametrize("role,private", [("owner", False), ("member", True)])
async def test_list_pagination_filters_and_visibility(service, monkeypatch, role, private):
    monkeypatch.setattr(service, "_actor", AsyncMock(return_value=(7, 3, role)))
    session = Session(rows=[row(id=9), row(id=8), row(id=7)])
    page = await service.list(session=session, limit=2, before_id=10, status="todo")
    assert [item.id for item in page["items"]] == [9, 8] and page["next_before_id"] == 8
    sql = query_text(session.queries[0])
    assert "tenant_id = 7" in sql and "id < 10" in sql and "status = 'todo'" in sql
    assert ("owner_user_id = 3" in sql) is private
    assert (await service.list(session=Session(), limit=2))["next_before_id"] is None


@pytest.mark.parametrize(
    "kwargs", [{"limit": True}, {"limit": 0}, {"limit": 101}, {"before_id": 0}, {"status": "secret"}]
)
async def test_list_validates_bounds(service, kwargs):
    with pytest.raises(ValueError):
        await service.list(session=Session(), **kwargs)


async def test_links_recheck_membership_contacts_and_parent(service, monkeypatch):
    for request, values in ((create_request(assignee_id=4), [None]), (create_request(contact_id=5), [None])):
        with pytest.raises(PlannerUnavailable):
            await service._references(Session(values), request, (7, 3, "owner"))
    session = Session([4, 5])
    await service._references(session, create_request(assignee_id=4, contact_id=5), (7, 3, "owner"))
    assert all("tenant_id = 7" in query_text(query) for query in session.queries)
    monkeypatch.setattr(tasks, "has_permission", lambda *args: False)
    with pytest.raises(PermissionError):
        await service._references(Session(), create_request(contact_id=5), (7, 3, "owner"))


async def test_parent_cycle_and_depth_refused_under_serialized_writer(service, monkeypatch):
    monkeypatch.setattr(service, "_task", AsyncMock(return_value=row(parent_id=1)))
    with pytest.raises(PlannerConflict, match="cycle"):
        await service._references(Session(), create_request(parent_id=1), (7, 3, "owner"), own_id=1)
    monkeypatch.setattr(service, "_task", AsyncMock(side_effect=[row(id=i, parent_id=i + 1) for i in range(1, 101)]))
    with pytest.raises(PlannerConflict, match="depth"):
        await service._references(Session(), create_request(parent_id=1), (7, 3, "owner"), own_id=200)
    monkeypatch.setattr(service, "_task", AsyncMock(return_value=row(parent_id=None)))
    await service._references(Session(), create_request(parent_id=1), (7, 3, "owner"), own_id=2)


@pytest.mark.parametrize(
    "changes,version",
    [
        ({"version": 2}, 1),
        ({"status": "done"}, 1),
        ({"status": "cancelled"}, 1),
        ({}, True),
        ({"recurrence_source": "calendar:other"}, 1),
    ],
)
def test_edit_stops_on_stale_closed_or_external_source(changes, version):
    with pytest.raises(PlannerConflict):
        PlannerService._editable(row(**changes), version)


async def test_update_occurrence_vs_series(service, monkeypatch):
    item = row()
    monkeypatch.setattr(service, "_task", AsyncMock(return_value=item))
    session = Session()
    result = await service.update(
        1,
        TaskUpdate(expected_version=1, scope="occurrence", title="Only this one", status="in_progress"),
        session=session,
    )
    assert result.title == "Only this one" and result.status == "in_progress" and result.version == 2
    assert TaskCreate.model_validate_json(item.series_template_encrypted).title == "Synthetic task"
    result = await service.update(1, TaskUpdate(expected_version=2, scope="series", title="Next too"), session=session)
    assert (
        result.title == "Next too"
        and TaskCreate.model_validate_json(item.series_template_encrypted).title == "Next too"
    )
    assert not session.added
    assert (await service.get(1, session=session)).title == "Next too"


async def test_reschedule_explicit_series_rule_or_occurrence_only(service, monkeypatch):
    request = create_request(recurrence="FREQ=MONTHLY;COUNT=3")
    item = row(recurrence=request.recurrence, series_template_encrypted=request.model_dump_json())
    monkeypatch.setattr(service, "_task", AsyncMock(return_value=item))
    change = dict(expected_version=1, scope="occurrence", due_date="2099-02-02", timezone="UTC")
    result = await service.reschedule(1, TaskReschedule(**change), session=Session())
    assert (
        result.due_date == date(2099, 2, 2)
        and TaskCreate.model_validate_json(item.series_template_encrypted).due_date == ANCHOR
    )
    with pytest.raises(PlannerConflict, match="series_rule"):
        await service.reschedule(
            1, TaskReschedule(**{**change, "expected_version": 2, "recurrence": "FREQ=DAILY"}), session=Session()
        )
    with pytest.raises(PlannerConflict, match="deadline_kind"):
        await service.reschedule(
            1,
            TaskReschedule(expected_version=2, scope="occurrence", due_at="2099-02-02T09:00:00Z", timezone="UTC"),
            session=Session(),
        )
    result = await service.reschedule(
        1,
        TaskReschedule(
            expected_version=2, scope="series", due_date="2099-02-02", timezone="UTC", recurrence="FREQ=WEEKLY"
        ),
        session=Session(),
    )
    assert result.recurrence == "FREQ=WEEKLY;INTERVAL=1"
    assert TaskCreate.model_validate_json(item.series_template_encrypted).due_date == date(2099, 2, 2)


async def test_complete_creates_single_deterministic_successor_and_retry_reads_it(service, monkeypatch):
    request = create_request(recurrence="FREQ=MONTHLY;COUNT=3")
    item = row(
        recurrence=request.recurrence,
        series_template_encrypted=request.model_dump_json(),
        title_encrypted="Occurrence-only title",
    )
    monkeypatch.setattr(service, "_task", AsyncMock(return_value=item))
    session = Session()
    result = await service.complete(1, 1, session=session)
    assert result.task.status == "done" and result.task.completed_at and result.task.version == 2
    assert result.next_task.due_date == date(2099, 3, 31) and result.next_task.title == request.title
    assert len(session.added) == 1 and session.added[0].previous_occurrence_id == 1
    assert result.next_task.occurrence_key == occurrence_key(request.recurrence, ANCHOR, date(2099, 3, 31))
    session.values = [session.added[0]]
    replay = await service.complete(1, 1, session=session)
    assert replay == result and len(session.added) == 1


async def test_nonrecurring_and_exhausted_complete_no_successor(service, monkeypatch):
    for rule in (None, "FREQ=DAILY;COUNT=1"):
        request = create_request(recurrence=rule)
        item = row(recurrence=request.recurrence, series_template_encrypted=request.model_dump_json())
        monkeypatch.setattr(service, "_task", AsyncMock(return_value=item))
        assert (await service.complete(1, 1, session=Session())).next_task is None


async def test_timed_complete_preserves_local_clock(service, monkeypatch):
    request = create_request(
        due_date=None, due_at="2099-01-31T09:00:00+03:00", timezone="Europe/Moscow", recurrence="FREQ=DAILY;COUNT=2"
    )
    item = row(**tasks._values(request), series_template_encrypted=request.model_dump_json())
    monkeypatch.setattr(service, "_task", AsyncMock(return_value=item))
    result = await service.complete(1, 1, session=Session())
    assert result.next_task.due_at == datetime(2099, 2, 1, 6, tzinfo=UTC)


async def test_delete_owner_manager_only_with_current_version(service, monkeypatch):
    item = row(owner_user_id=99, assignee_id=3)
    monkeypatch.setattr(service, "_task", AsyncMock(return_value=item))
    monkeypatch.setattr(service, "_actor", AsyncMock(return_value=(7, 3, "member")))
    with pytest.raises(PermissionError):
        await service.delete(1, 1, session=Session())
    with pytest.raises(PlannerConflict):
        await service.delete(1, 2, session=Session())
    monkeypatch.setattr(service, "_actor", AsyncMock(return_value=(7, 3, "manager")))
    session = Session()
    assert await service.delete(1, 1, session=session) == {"deleted": True} and session.deleted == [item]


def test_model_has_composite_tenant_fences_and_single_successor_uniqueness():
    sql = str(CreateTable(PlannerTask.__table__).compile(dialect=postgresql.dialect()))
    assert "UNIQUE (tenant_id, series_id, occurrence_key)" in sql
    assert "UNIQUE (tenant_id, previous_occurrence_id)" in sql
    for name in ("parent_id", "series_id", "contact_id", "previous_occurrence_id"):
        assert f"FOREIGN KEY(tenant_id, {name})" in sql
    assert "recurrence_source" in sql and "DEFAULT 'planner'" in sql
    assert "series_template_encrypted" in sql


async def test_api_router_typed_crud_and_safe_errors(monkeypatch):
    from app.api.v1.conventions import validate_business_routes
    from app.api.v1.endpoints import planner as api

    validate_business_routes(api.router)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    for route in api.router.routes:
        for dependency in route.dependant.dependencies:
            app.dependency_overrides[dependency.call] = lambda: None
    item = tasks._projection(row())
    monkeypatch.setattr(api.planner_service, "create", AsyncMock(return_value=item))
    monkeypatch.setattr(api.planner_service, "get", AsyncMock(return_value=item))
    monkeypatch.setattr(
        api.planner_service, "list", AsyncMock(return_value={"items": [item], "limit": 50, "next_before_id": None})
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://unit.invalid") as client:
        assert (await client.post("/api/v1/planner/tasks", json={"title": "New"})).status_code == 201
        assert (await client.get("/api/v1/planner/tasks")).json()["items"][0]["id"] == 1
        assert (await client.get("/api/v1/planner/tasks/1")).status_code == 200
        bad = await client.post("/api/v1/planner/tasks", json={"title": "private-value", "tenant_id": 8})
        assert bad.status_code == 422 and "private-value" not in bad.text
        for error, status in (
            (PlannerUnavailable("secret"), 404),
            (PlannerConflict("secret"), 409),
            (PermissionError("secret"), 403),
            (ValueError("secret"), 422),
        ):
            monkeypatch.setattr(api.planner_service, "get", AsyncMock(side_effect=error))
            response = await client.get("/api/v1/planner/tasks/1")
            assert response.status_code == status and "secret" not in response.text


async def test_api_mutation_handlers(monkeypatch):
    from app.api.v1.endpoints import planner as api

    expected = tasks._projection(row())
    for method in ("update", "reschedule"):
        monkeypatch.setattr(api.planner_service, method, AsyncMock(return_value=expected))
    monkeypatch.setattr(api.planner_service, "complete", AsyncMock(return_value={"task": expected, "next_task": None}))
    monkeypatch.setattr(api.planner_service, "delete", AsyncMock(return_value={"deleted": True}))
    assert await api.update_task(TaskUpdate(expected_version=1, scope="occurrence", title="one"), 1) == expected
    assert await api.reschedule_task(TaskReschedule(expected_version=1, scope="series", timezone="UTC"), 1) == expected
    assert (await api.complete_task(TaskVersion(expected_version=1), 1))["task"] == expected
    assert await api.delete_task(TaskVersion(expected_version=1), 1) == {"deleted": True}


async def test_completion_stops_before_mutation_on_preexisting_slot(service, monkeypatch):
    request = create_request(recurrence="FREQ=DAILY")
    item = row(recurrence=request.recurrence, series_template_encrypted=request.model_dump_json())
    monkeypatch.setattr(service, "_task", AsyncMock(return_value=item))
    session = Session(values=[77])
    with pytest.raises(PlannerConflict, match="already_exists"):
        await service.complete(1, 1, session=session)
    assert item.status == "todo" and not session.added and session.flushes == 0


async def test_next_task_is_not_exposed_to_previous_occurrence_assignee(service, monkeypatch):
    request = create_request(recurrence="FREQ=DAILY")
    item = row(
        owner_user_id=99,
        assignee_id=3,
        recurrence=request.recurrence,
        series_template_encrypted=request.model_dump_json(),
    )
    monkeypatch.setattr(service, "_task", AsyncMock(return_value=item))
    monkeypatch.setattr(service, "_actor", AsyncMock(return_value=(7, 3, "member")))
    session = Session()
    result = await service.complete(1, 1, session=session)
    assert result.next_task is None and len(session.added) == 1
    assert session.added[0].owner_user_id == 99 and session.added[0].assignee_id is None
    replay = await service.complete(1, 1, session=session)
    assert replay.next_task is None
    sql = query_text(session.queries[-1])
    assert "owner_user_id = 3" in sql and "assignee_id = 3" in sql


def test_previous_occurrence_fk_removes_suffix_on_delete():
    foreign_key = next(
        fk for fk in PlannerTask.__table__.foreign_key_constraints if "previous_occurrence_id" in fk.column_keys
    )
    assert foreign_key.ondelete == "CASCADE"


@pytest.mark.parametrize(
    "error,code",
    [
        (PlannerUnavailable("private"), 404),
        (PlannerConflict("private"), 409),
        (PermissionError("private"), 403),
        (ValueError("private"), 422),
    ],
)
async def test_web_errors_are_opaque(error, code):
    from app.web.planner import invoke

    async def failure():
        raise error

    with pytest.raises(HTTPException) as captured:
        await invoke(failure())
    assert captured.value.status_code == code and "private" not in captured.value.detail


async def test_web_forms_csrf_validation_and_mutations(monkeypatch):
    from app.web import planner as web

    app = FastAPI()
    app.include_router(web.router, prefix="/app")
    app.state.csrf_manager = SimpleNamespace(verify_token=lambda value: value == "synthetic-csrf")
    for method in ("create", "update", "complete", "reschedule", "delete"):
        monkeypatch.setattr(web.planner_service, method, AsyncMock(return_value={}))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://unit.invalid") as client:
        for raw in ("", "bad", "synthetic-csrf-incorrect"):
            assert (await client.post("/app/planner", data={"title": "private", "_csrf": raw})).status_code == 403
        valid = {
            "title": "Test",
            "_csrf": "synthetic-csrf",
            "assignee_id": "",
            "due_at": "",
            "due_date": "",
            "recurrence": "",
            "tags": "one, two",
        }
        assert (await client.post("/app/planner", data=valid)).status_code == 303
        request = web.planner_service.create.await_args.args[0]
        assert request.tags == ["one", "two"] and request.assignee_id is None and request.due_at is None
        for field in ("assignee_id", "parent_id", "contact_id"):
            for bad in ("0", "01", "-1", "١", "1.0"):
                assert (await client.post("/app/planner", data={**valid, field: bad})).status_code == 422
        duplicate = await client.post(
            "/app/planner",
            content="_csrf=synthetic-csrf&title=one&title=two",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert duplicate.status_code == 422
        assert (await client.post("/app/planner", data={**valid, "tenant_id": "8"})).status_code == 422
        for op, extra in (
            ("complete", {}),
            ("delete", {}),
            ("edit", {"scope": "occurrence", "title": "edit"}),
            ("reschedule", {"scope": "occurrence", "timezone": "UTC", "due_date": "2099-01-01"}),
        ):
            response = await client.post(
                f"/app/planner/1/{op}", data={"_csrf": "synthetic-csrf", "expected_version": "1", **extra}
            )
            assert response.status_code == 303 and response.headers["location"] == "/app/planner"
        assert (
            await client.post("/app/planner/1/edit", data={"_csrf": "synthetic-csrf", "expected_version": "1"})
        ).status_code == 422
        assert (
            await client.post("/app/planner/1/delete", data={"_csrf": "synthetic-csrf", "expected_version": "true"})
        ).status_code == 422


async def test_web_page_permissions_and_context(monkeypatch):
    from app.web import planner as web

    app = FastAPI()
    app.include_router(web.router, prefix="/app")
    monkeypatch.setattr(
        web.planner_service, "list", AsyncMock(return_value={"items": [], "limit": 50, "next_before_id": None})
    )
    monkeypatch.setattr(web.planner_service, "get", AsyncMock(return_value=tasks._projection(row())))
    monkeypatch.setattr(web, "has_permission", lambda *args: False)
    monkeypatch.setattr(web, "get_current_user", lambda: None)
    contexts = []

    def render(request, name, **context):
        contexts.append((name, context))
        return {"view": context["view"]}

    monkeypatch.setattr(web, "render", render)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://unit.invalid") as client:
        assert (await client.get("/app/planner?view=board")).json() == {"view": "board"}
        assert (await client.get("/app/planner/1/edit")).status_code == 200
    assert not contexts[0][1]["can_create"] and contexts[1][1]["editing"].id == 1


def test_template_escapes_private_text_and_exposes_explicit_scopes():
    from bs4 import BeautifulSoup

    from app.web.deps import templates
    from app.web.planner import PRIORITY_LABELS, STATUS_LABELS

    task = tasks._projection(row(title_encrypted="<script>private</script>"))
    context = dict(
        request=SimpleNamespace(query_params={}),
        user=None,
        tenant=None,
        perms=None,
        csrf="synthetic-csrf",
        section="planner",
        view="board",
        editing=task,
        items=[task],
        status_labels=STATUS_LABELS,
        priority_labels=PRIORITY_LABELS,
        can_create=True,
        can_update=True,
        can_delete=True,
        next_before_id=2,
        url_for=lambda name, **kwargs: "/static/" + kwargs.get("path", ""),
        static_v=lambda path: "/static/" + path,
    )
    html = templates.env.get_template("web/planner.html").render(**context)
    soup = BeautifulSoup(html, "html.parser")
    assert "<script>private</script>" not in html and "&lt;script&gt;private&lt;/script&gt;" in html
    forms = soup.select('form[method="post"]')
    assert all(form.select_one('input[name="_csrf"]')["value"] == "synthetic-csrf" for form in forms)
    scopes = [form.select_one('input[name="scope"]') for form in forms if form["action"].endswith("/reschedule")]
    assert {value["value"] for value in scopes} == {"occurrence", "series"}
    assert soup.select_one('a[aria-current="page"]')["href"].endswith("view=board")
    assert "Следующие задачи" in soup.text


async def test_typed_tools_register_only_with_catalog_and_honor_confirm(monkeypatch):
    from types import MappingProxyType

    from app.agent import tools
    from app.agent.tool_contracts import ToolBudget, ToolInputError
    from app.agent.toolset import planner as module
    from app.core import access_catalog

    registry = {}
    monkeypatch.setattr(tools, "_REGISTRY", registry)
    monkeypatch.setattr(tools, "TOOL_REGISTRY", MappingProxyType(registry))
    monkeypatch.setattr(access_catalog, "resolve_permission_key", lambda key: None)
    with pytest.raises(ValueError, match="catalog_not_registered"):
        module.register_planner_tools()
    assert registry == {}
    resolver = lambda key: (
        SimpleNamespace(key="planner.task"),
        ActionType.CREATE if key.endswith("create") else ActionType.VIEW,
    )
    monkeypatch.setattr(access_catalog, "resolve_permission_key", resolver)
    monkeypatch.setattr(tools, "resolve_permission_key", resolver)
    module.register_planner_tools()
    module.register_planner_tools()
    assert set(registry) == {"planner_create", "planner_list"}
    create = registry["planner_create"]
    assert create.confirm and not registry["planner_list"].confirm
    assert create.required_permission == "planner.task.create"
    assert create.contract.arguments({"task": {"title": "safe"}}, ToolBudget()) == {"task": {"title": "safe"}}
    with pytest.raises(ToolInputError):
        create.contract.arguments({"task": {"title": "safe", "tenant_id": 8}}, ToolBudget())
    with pytest.raises(ToolInputError):
        registry["planner_list"].contract.arguments({"limit": True}, ToolBudget())
    expected = tasks._projection(row())
    monkeypatch.setattr(module.planner_service, "create", AsyncMock(return_value=expected))
    assert (await module.planner_create({"title": "safe"}))["id"] == 1
    assert (await module.planner_create({"title": "safe", "tenant_id": 8}))["error"]
    for error in (PlannerUnavailable("secret"), PermissionError("secret"), ValueError("secret")):
        monkeypatch.setattr(module.planner_service, "create", AsyncMock(side_effect=error))
        result = await module.planner_create({"title": "safe"})
        assert result["error"] and "secret" not in str(result)
    monkeypatch.setattr(
        module.planner_service,
        "list",
        AsyncMock(return_value={"items": [expected], "limit": 50, "next_before_id": None}),
    )
    assert (await module.planner_list())["items"][0]["id"] == 1
    monkeypatch.setattr(module.planner_service, "list", AsyncMock(side_effect=PermissionError("secret")))
    assert (await module.planner_list())["error"]


@pytest.mark.parametrize("until,timed", [("00010101", False), ("09991231T235959Z", True)])
def test_rule_text_roundtrips_four_digit_years(until, timed):
    parsed = parse_rule(f"FREQ=DAILY;UNTIL={until}", timed=timed)
    assert parsed.text.endswith("UNTIL=" + until)
    assert parse_rule(parsed.text, timed=timed) == parsed


async def test_contact_link_rejects_stale_cached_permission(service, monkeypatch):
    monkeypatch.setattr(tasks, "workspace_grants", lambda role: {("plannertask", action) for action in ActionType})
    with pytest.raises(PermissionError, match="contact_denied"):
        await service._references(Session(values=[5]), create_request(contact_id=5), (7, 3, "member"))


def test_migration_0140_matches_model_and_owns_full_lifecycle(monkeypatch):
    import importlib.util
    from pathlib import Path

    from sqlalchemy import Column, Integer, MetaData, Table, Text, UniqueConstraint

    path = Path(__file__).resolve().parents[2] / "migrations/versions/0140_planner_tasks.py"
    spec = importlib.util.spec_from_file_location("planner_0140_unit", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "0140" and migration.down_revision == "0139"
    metadata = MetaData(schema="test_schema")
    for name in ("tenants", "users", "contacts"):
        Table(
            name,
            metadata,
            Column("id", Integer, primary_key=True),
            Column("tenant_id", Integer),
            UniqueConstraint("tenant_id", "id"),
        )
    statements, created, indexes, dropped = [], [], [], []
    bind = SimpleNamespace(
        dialect=postgresql.dialect(), execute=lambda sql, params=None: statements.append((str(sql), params))
    )

    def create_table(name, *columns, **kwargs):
        created.append(Table(name, metadata, *columns, **kwargs))

    monkeypatch.setattr(migration, "settings", SimpleNamespace(DB_SCHEMA="test_schema"))
    monkeypatch.setattr(
        migration,
        "op",
        SimpleNamespace(
            get_bind=lambda: bind,
            create_table=create_table,
            create_index=lambda *args, **kwargs: indexes.append((args, kwargs)),
            execute=lambda sql: statements.append((str(sql), None)),
            drop_table=lambda *args, **kwargs: dropped.append((args, kwargs)),
        ),
    )
    migration.upgrade()
    assert len(created) == 1 and created[0].name == "planner_tasks"
    table = created[0]
    assert set(table.c.keys()) == set(PlannerTask.__table__.c.keys())
    for column in table.c:
        model_column = PlannerTask.__table__.c[column.name]
        assert column.nullable == model_column.nullable, column.name
        assert column.type.compile(dialect=postgresql.dialect()) == model_column.type.compile(
            dialect=postgresql.dialect()
        ), column.name
    assert isinstance(table.c.recurrence.type, Text)
    assert table.c.recurrence_source.server_default.arg == "planner"
    assert table.c.pii_updated_at.server_default.arg == "1"
    assert len(indexes) == 1 and indexes[0][0][2] == ["tenant_id", "status", "due_at", "due_date"]

    def foreign_keys(source):
        return {
            (
                tuple(f.column_keys),
                tuple(e.target_fullname.split(".", 1)[1] for e in f.elements),
                f.ondelete,
                f.deferrable,
                f.initially,
            )
            for f in source.foreign_key_constraints
        }

    assert foreign_keys(table) == foreign_keys(PlannerTask.__table__)
    assert "CREATE TABLE test_schema.planner_tasks" in str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert any(
        "CREATE TRIGGER contact_pii_marker" in sql and "test_schema.planner_tasks" in sql for sql, _ in statements
    )
    assert any(params and params.get("resource_key") == "planner.task" for _, params in statements)
    assert migration.WORKSPACE_ROLE_PRESETS["viewer"] == ("VIEW",)
    from app.core.access_catalog import workspace_grants

    for role, actions in migration.WORKSPACE_ROLE_PRESETS.items():
        assert {action.name for model, action in workspace_grants(role) if model == "plannertask"} == set(actions)
    migration.downgrade()
    assert dropped == [(("planner_tasks",), {"schema": "test_schema"})]
    assert any("DELETE FROM test_schema.model_types" in sql for sql, _ in statements)


def test_integrated_catalog_facade_rotation_and_navigation():
    from app.core.access_catalog import resolve_permission_key, workspace_grants
    from app.models import PlannerTask as public_model
    from app.web.nav import NAV_ITEMS, section_of
    from cli.commands.pii import REGISTRY

    assert public_model is PlannerTask
    assert resolve_permission_key("planner.task.view")[0].model_name == "plannertask"
    for role in ("owner", "admin", "manager"):
        assert {
            ("plannertask", action)
            for action in (ActionType.VIEW, ActionType.CREATE, ActionType.UPDATE, ActionType.DELETE)
        } <= workspace_grants(role)
    assert ("plannertask", ActionType.VIEW) in workspace_grants("viewer")
    assert ("plannertask", ActionType.UPDATE) not in workspace_grants("viewer")
    assert ("plannertask", ActionType.CREATE) in workspace_grants("analyst")
    assert ("plannertask", ActionType.DELETE) not in workspace_grants("analyst")
    assert public_model.objects.create.__self__ is tasks.planner_service
    for forbidden in ("filter", "bulk_create", "update_by_id", "delete_by_id"):
        with pytest.raises(AttributeError):
            getattr(public_model.objects, forbidden)
    expected = {
        ("planner_tasks", name, None)
        for name in ("title_encrypted", "description_encrypted", "series_template_encrypted")
    }
    assert expected <= set(REGISTRY)
    nav = next(item for item in NAV_ITEMS if item.key == "planner")
    assert nav.href == "/app/planner" and nav.required_permission == "planner.task.view" and nav.ready
    assert section_of("/app/planner/1/edit").key == "planner"


def test_public_app_registration_has_no_cron_namespace_overlap():
    from app.agent.tools import get_tool
    from app.api.v1.entry import router as api_router
    from app.web import web_router

    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    app.include_router(web_router)
    paths = app.openapi()["paths"]
    assert "/api/v1/planner/tasks" in paths
    assert "/app/planner/{task_id}/edit" in paths
    assert get_tool("planner_create").confirm
    assert get_tool("planner_create").required_permission == "planner.task.create"
    assert not get_tool("planner_list").confirm
