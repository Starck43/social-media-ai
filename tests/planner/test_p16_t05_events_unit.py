"""DB-exempt orchestration tests: database and provider boundaries are explicit fakes."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event
from sqlalchemy.engine.interfaces import Dialect

from app.core.tenant_context import tenant_scope
from app.models.planner.events import PlannerEventRun, PlannerEventSettings
from app.services.crm.greetings import crm_greetings_service
from app.services.planner.events import EventPlanningService, EventPlanningUnavailable
from app.services.planner.tasks import planner_service

__source_only__ = True
pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def no_database(monkeypatch):
    import app.core.database as database

    def forbidden(*args, **kwargs):
        raise AssertionError("real database is forbidden")

    event.listen(Dialect, "do_connect", forbidden)
    monkeypatch.setattr(database, "async_session_maker", forbidden)
    yield
    event.remove(Dialect, "do_connect", forbidden)


class FakeSession:
    def __init__(self, *values):
        self.values = list(values)
        self.queries = []
        self.added = []
        self.flushes = 0

    async def scalar(self, query):
        self.queries.append(query)
        assert self.values, "unexpected query"
        return self.values.pop(0)

    async def execute(self, query):
        self.queries.append(query)

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flushes += 1


def owner_values(*, role="owner", user=True, current_owner=9):
    return (
        9,
        SimpleNamespace(id=9, is_active=True) if user else None,
        SimpleNamespace(id=7, timezone="Europe/Moscow"),
        SimpleNamespace(role_id=2),
        role,
        SimpleNamespace(owner_user_id=current_owner),
    )


def fake_event():
    return {"contact_id": 42, "event_type": "birthday", "event_date": date(2028, 1, 3), "display_name": "Test"}


async def test_disabled_without_owner_does_not_load_contacts(monkeypatch):
    fetch = AsyncMock()
    monkeypatch.setattr(crm_greetings_service, "list_upcoming_events", fetch)
    with tenant_scope(7):
        assert await EventPlanningService().run_daily(session=FakeSession(None)) == {"status": "disabled", "created": 0}
    fetch.assert_not_awaited()


@pytest.mark.parametrize("options", [{"user": False}, {"role": "viewer"}, {"current_owner": 12}])
async def test_revoked_or_changed_owner_never_creates(monkeypatch, options):
    fetch = AsyncMock()
    monkeypatch.setattr(crm_greetings_service, "list_upcoming_events", fetch)
    session = FakeSession(*owner_values(**options))
    with tenant_scope(7):
        outcome = await EventPlanningService().run_daily(session=session)
    assert outcome["created"] == 0
    assert outcome["status"] in {"disabled", "owner_unavailable"}
    assert not session.added
    fetch.assert_not_awaited()


async def test_run_locks_authority_creates_parent_and_steps_once(monkeypatch):
    session = FakeSession(*owner_values(), None)
    fetch = AsyncMock(return_value={"items": [fake_event()]})
    create = AsyncMock(side_effect=[SimpleNamespace(id=1), SimpleNamespace(id=2), SimpleNamespace(id=3)])
    monkeypatch.setattr(crm_greetings_service, "list_upcoming_events", fetch)
    monkeypatch.setattr(planner_service, "create", create)
    with tenant_scope(7):
        assert await EventPlanningService().run_daily(session=session) == {"status": "ok", "created": 1}
    (ledger,) = session.added
    assert isinstance(ledger, PlannerEventRun)
    assert (ledger.contact_id, ledger.year, ledger.root_task_id, ledger.greeting_task_id) == (42, 2028, 1, 3)
    assert create.await_count == 3
    root, gift, greeting = [call.args[0] for call in create.await_args_list]
    assert root.due_date == date(2028, 1, 3) and root.contact_id == 42
    assert gift.parent_id == 1
    assert gift.due_date == date(2027, 12, 27) and greeting.due_date == date(2028, 1, 2)
    assert all(call.kwargs["session"] is session for call in create.await_args_list)
    assert fetch.await_args.kwargs["emit_notifications"] is False
    assert all(query._for_update_arg is not None for query in session.queries[1:7])
    assert "ORDER BY" in str(session.queries[6])


async def test_preexisting_ledger_skips_event(monkeypatch):
    session = FakeSession(*owner_values(), 99)
    monkeypatch.setattr(
        crm_greetings_service, "list_upcoming_events", AsyncMock(return_value={"items": [fake_event()]})
    )
    create = AsyncMock()
    monkeypatch.setattr(planner_service, "create", create)
    with tenant_scope(7):
        assert await EventPlanningService().run_daily(session=session) == {"status": "ok", "created": 0}
    assert not session.added
    create.assert_not_awaited()


async def test_enable_binds_actor_disable_does_not_transfer(monkeypatch):
    monkeypatch.setattr(planner_service, "_actor", AsyncMock(return_value=(7, 9, "owner")))
    service = EventPlanningService()
    session = FakeSession(None)
    with tenant_scope(7):
        assert await service.set_enabled(True, session=session) is True
        (row,) = session.added
        assert isinstance(row, PlannerEventSettings) and row.owner_user_id == 9
        assert await service.set_enabled(False, session=FakeSession(row)) is False
        assert row.owner_user_id is None


async def test_settings_denied_to_analyst_and_bypass(monkeypatch):
    monkeypatch.setattr(planner_service, "_actor", AsyncMock(return_value=(7, 9, "analyst")))
    with tenant_scope(7), pytest.raises(PermissionError):
        await EventPlanningService().set_enabled(True, session=FakeSession())
    with tenant_scope(bypass=True), pytest.raises(PermissionError):
        await EventPlanningService().run_daily(session=FakeSession())


async def test_draft_requires_matching_ledger_contact(monkeypatch):
    monkeypatch.setattr(planner_service, "_actor", AsyncMock(return_value=(7, 9, "owner")))
    monkeypatch.setattr(planner_service, "_task", AsyncMock(return_value=SimpleNamespace(contact_id=44)))
    monkeypatch.setattr("app.core.permissions.has_permission", lambda *a: True)
    fake = AsyncMock()
    monkeypatch.setattr(crm_greetings_service, "generate_greeting_draft", fake)
    session = FakeSession(SimpleNamespace(contact_id=42))
    with tenant_scope(7), pytest.raises(EventPlanningUnavailable):
        await EventPlanningService().draft_for_task(3, session=session)
    fake.assert_not_awaited()


def test_contact_month_day_and_leap_dates_match_crm_contract():
    from app.services.crm.greetings import _contact_event_value, next_occurrence_date

    assert next_occurrence_date(_contact_event_value("02-29", False), today=date(2027, 2, 1))[0] == date(2027, 2, 28)
    assert next_occurrence_date(_contact_event_value("1992-02-29", True), today=date(2028, 2, 1))[0] == date(
        2028, 2, 29
    )
    assert _contact_event_value("--02-29", False) == "--02-29"


async def test_draft_action_uses_ledger_and_actual_actor(monkeypatch):
    from app.core.access_catalog import workspace_grants
    from app.core.permissions import permission_scope

    user = SimpleNamespace(id=9, is_active=True)
    monkeypatch.setattr(planner_service, "_actor", AsyncMock(return_value=(7, 9, "owner")))
    monkeypatch.setattr(planner_service, "_task", AsyncMock(return_value=SimpleNamespace(contact_id=42)))
    fake = AsyncMock(return_value={"draft_text": "safe", "send_enabled": False})
    monkeypatch.setattr(crm_greetings_service, "generate_greeting_draft", fake)
    session = FakeSession(
        SimpleNamespace(contact_id=42, event_type="birthday", year=2028), SimpleNamespace(timezone="UTC")
    )
    with tenant_scope(7), permission_scope(user, member_grants=workspace_grants("owner")):
        result = await EventPlanningService().draft_for_task(3, session=session)
    assert result["send_enabled"] is False
    assert fake.await_args.kwargs == {
        "tenant_id": 7,
        "actor_id": 9,
        "contact_id": 42,
        "event_type": "birthday",
        "year": 2028,
        "tz_name": "UTC",
        "session": session,
    }


def test_job_registration_is_daily_and_payload_cannot_select_actor():
    from app.jobs.handlers import HANDLERS
    from app.tasks.bootstrap import DEFAULT_TASKS

    cron, kind, payload = DEFAULT_TASKS["planner-events-daily"]
    assert cron == "0 6 * * *" and kind == "planner.events.daily" and payload == {}
    assert kind in HANDLERS and len(kind) <= 20


@pytest.mark.parametrize(
    "result, expected",
    [
        ({"status": "disabled", "created": 0}, {"status": "skipped", "reason": "disabled", "created": 0}),
        (
            {"status": "owner_unavailable", "created": 0},
            {"status": "skipped", "reason": "owner_unavailable", "created": 0},
        ),
        ({"status": "ok", "created": 1}, {"status": "ok", "created": 1}),
    ],
)
async def test_worker_never_trusts_payload_and_suppresses_empty_notifications(monkeypatch, result, expected):
    from app.jobs.job_handlers import planner_events

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def begin(self):
            return self

    worker = AsyncMock(return_value=result)
    monkeypatch.setattr(planner_events, "async_session_maker", Session)
    monkeypatch.setattr(planner_events.event_planning_service, "run_daily", worker)
    assert await planner_events.handle_planner_events({"owner_user_id": 999}) == expected
    assert set(worker.await_args.kwargs) == {"session"}


async def test_crm_listing_and_draft_accept_contact_manager_month_day(monkeypatch):
    from app.models.audit.action_audit_event import ActionAuditEvent
    from app.models.contacts.core import Contact
    from app.services.crm.greetings import CrmGreetingsService

    contact = Contact(
        id=42,
        tenant_id=7,
        display_name_encrypted="Synthetic",
        birthday_encrypted="03-04",
        birthday_has_year=False,
        preferred_channel="email",
    )

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def scalars(self):
            return self

        def all(self):
            return self.rows

        def scalar_one_or_none(self):
            return self.rows[0] if self.rows else None

    class Session:
        def __init__(self, rows):
            self.rows = list(rows)
            self.added = []

        async def execute(self, query):
            return Result(self.rows.pop(0))

        def add(self, row):
            row.id = 101
            self.added.append(row)

        async def flush(self):
            pass

    monkeypatch.setattr(ActionAuditEvent.objects, "record_success", AsyncMock())
    service = CrmGreetingsService()
    with tenant_scope(7):
        listing = await service.list_upcoming_events(
            tenant_id=7, actor_id=9, reference_date=date(2032, 2, 26), session=Session([[contact], [], []])
        )
        assert [event["event_date"] for event in listing["items"]] == [date(2032, 3, 4)]
        session = Session([[contact], [], []])
        draft = await service.generate_greeting_draft(
            tenant_id=7,
            actor_id=9,
            contact_id=42,
            event_type="birthday",
            year=2032,
            reference_date=date(2032, 2, 26),
            session=session,
        )
    assert draft["event_date"] == date(2032, 3, 4) and draft["send_enabled"] is False
