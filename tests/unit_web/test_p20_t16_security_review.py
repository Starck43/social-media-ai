"""Autonomous P20-T16 security regressions for Today, Inbox and calendar.

These tests deliberately stay below the database/provider boundary. They exercise
service adapters with realistic projections and inspect the SQL predicates that
protect tenant-owned records, while preserving the existing source-only policy.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest

from app.core import tenant_context
from app.services.calendar import service as calendar_module
from app.services.communications import inbox
from app.services.workspace import task_surface
from app.services.workspace.today import TodayProjection, TodayRepository

__source_only__ = True


class _Rows:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)

    def scalars(self):
        return self


class _ExecuteResult:
    def __init__(self, *, scalar=None, rows=()):
        self._scalar = scalar
        self._rows = list(rows)

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return _Rows(self._rows)


class _Session:
    def __init__(self, results):
        self.results = list(results)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return self.results.pop(0)

    async def scalars(self, statement):
        self.statements.append(statement)
        return _Rows([])


class _SessionContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def begin(self):
        return self


def _perms(*allowed, role="member"):
    allowed_set = set(allowed)
    return NS(
        workspace_role=role,
        can=lambda resource, action="view": (resource, action) in allowed_set,
    )


def _planner_item(task_id, *, tenant_id=42, owner=8, status="todo", title="Task"):
    values = {
        "id": task_id,
        "tenant_id": tenant_id,
        "title": title,
        "status": status,
        "owner_user_id": owner,
        "assignee_id": None,
        "due_at": None,
        "due_date": date(2026, 11, 20),
        "recurrence_source": "planner",
    }
    return NS(**values, model_dump=lambda: dict(values))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("current_tenant", "bypass"),
    [(41, False), (42, True)],
)
async def test_today_planner_adapter_fails_closed_before_service_call(monkeypatch, current_tenant, bypass):
    monkeypatch.setattr(tenant_context, "current_tenant_id", lambda: current_tenant)
    monkeypatch.setattr(tenant_context, "is_bypass", lambda: bypass)

    from app.services.planner.tasks import planner_service

    fetch = AsyncMock()
    monkeypatch.setattr(planner_service, "list", fetch)

    with pytest.raises(PermissionError):
        await TodayRepository().planner_tasks(42)

    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_today_projection_keeps_personal_tasks_actor_scoped(monkeypatch):
    class Repo:
        async def planner_tasks(self, _workspace_id, limit):
            assert limit == 5
            return [
                _planner_item(3, tenant_id=41, title="Other tenant"),
                _planner_item(2, owner=9, title="Foreign"),
                _planner_item(1, title="Mine"),
            ]

    projection = TodayProjection(
        workspace_id=42,
        user_id=8,
        perms=_perms(("plannertask", "view")),
        repository=Repo(),
        now=datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc),
    )

    section = await projection.build_today_plan_section()

    assert section.status == "ok"
    assert [item["id"] for item in section.items] == [1]
    assert section.items[0]["title"] == "Mine"


@pytest.mark.asyncio
async def test_today_projection_surfaces_planner_denial_as_unavailable(monkeypatch):
    class Repo:
        async def planner_tasks(self, _workspace_id, limit):
            raise PermissionError("planner_denied")

    projection = TodayProjection(
        workspace_id=42,
        user_id=8,
        perms=_perms(("plannertask", "view")),
        repository=Repo(),
        now=datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc),
    )

    section = await projection.build_today_plan_section()

    assert section.status == "unavailable"
    assert section.items == []
    assert "план" in section.reason.lower()


@pytest.mark.asyncio
async def test_inbox_permission_is_checked_before_any_query(monkeypatch):
    permission = Mock(side_effect=PermissionError("message_denied"))
    monkeypatch.setattr(inbox, "require_message_permission", permission)
    session = AsyncMock()

    with pytest.raises(PermissionError, match="message_denied"):
        await inbox.list_inbox_threads(session, limit=10)

    session.execute.assert_not_awaited()
    permission.assert_called_once()


@pytest.mark.asyncio
async def test_inbox_detail_query_is_tenant_fenced(monkeypatch):
    monkeypatch.setattr(inbox, "require_message_permission", lambda _action: 42)
    thread = NS(
        id=7,
        connection_id=3,
        contact_id=None,
        channel="email",
        thread_key="thread-7",
        subject_encrypted="Subject",
        participants=["person@example.test"],
        last_message_at=datetime(2026, 11, 20, tzinfo=timezone.utc),
        message_count=0,
        unread_count=0,
        is_archived=False,
    )
    session = _Session([_ExecuteResult(scalar=thread), _ExecuteResult(rows=[])])

    result = await inbox.get_thread_detail(session, 7)

    assert result["thread"]["id"] == 7
    sql = str(session.statements[0].compile(compile_kwargs={"literal_binds": True}))
    assert "message_threads.tenant_id = 42" in sql
    assert "message_threads.id = 7" in sql



def test_inbox_unavailable_message_hides_external_url_and_body():
    message = NS(
        id=9,
        thread_id=7,
        contact_id=None,
        connection_id=3,
        channel="email",
        direction="inbound",
        sent_at=datetime(2026, 11, 20, tzinfo=timezone.utc),
        received_at=None,
        subject_encrypted="Subject",
        sender_encrypted="sender@example.test",
        recipient_encrypted="recipient@example.test",
        original_id="provider-9",
        original_url="https://provider.invalid/messages/9",
        content_hash="sha256:test",
        is_read=False,
        is_archived=False,
        is_hidden=False,
        is_unavailable=True,
        unavailable_at=datetime(2026, 11, 20, tzinfo=timezone.utc),
    )

    projected = inbox._message_dict(message)

    assert projected["original_url"] is None
    assert projected["availability_status"] == "unavailable"
    assert "body" not in projected
    assert "body_encrypted" not in projected


@pytest.mark.asyncio
async def test_calendar_events_query_is_fenced_to_tenant_and_connection(monkeypatch):
    monkeypatch.setattr(
        calendar_module.planner_service,
        "_actor",
        AsyncMock(return_value=(42, 8, "member")),
    )
    connection = NS(
        id=9,
        tenant_id=42,
        owner_user_id=8,
        provider_id="google_calendar",
        is_active=True,
        state="verified",
        config={"calendar_id": "primary"},
    )
    monkeypatch.setattr(calendar_module.calendar_service, "_connection", AsyncMock(return_value=connection))
    adapter = NS(pull=AsyncMock(return_value=([], None)))
    monkeypatch.setattr(calendar_module.calendar_service, "_adapter", AsyncMock(return_value=adapter))
    session = _Session([])

    result = await calendar_module.calendar_service.events(
        session=session,
        connection_id=9,
        limit=50,
    )

    assert result == {"state": "available", "items": [], "next_before_id": None}
    sql = str(session.statements[0].compile(compile_kwargs={"literal_binds": True}))
    assert "calendar_events.tenant_id = 42" in sql
    assert "source_objects.tenant_id = 42" in sql
    assert "source_objects.connection_id = 9" in sql
    adapter.pull.assert_awaited_once_with(None)


@pytest.mark.asyncio
async def test_calendar_view_preserves_reauth_as_unavailable_not_empty(monkeypatch):
    monkeypatch.setattr(task_surface, "get_current_user", lambda: object())
    monkeypatch.setattr(
        task_surface,
        "has_permission",
        lambda _actor, resource, _action: resource == "plannertask",
    )
    monkeypatch.setattr(task_surface, "async_session_maker", _SessionContext)
    monkeypatch.setattr(
        task_surface,
        "planner_service",
        NS(calendar_range=AsyncMock(return_value={"items": [], "next_before_id": None})),
    )
    monkeypatch.setattr(
        task_surface,
        "calendar_service",
        NS(events=AsyncMock(return_value={"state": "reauth", "items": None, "next_before_id": None})),
    )

    result = await task_surface.calendar_view()

    event_provider = next(row for row in result["providers"] if row["kind"] == "event")
    planner_provider = next(row for row in result["providers"] if row["kind"] == "planner")
    agent_provider = next(row for row in result["providers"] if row["kind"] == "agent")
    assert event_provider == {"kind": "event", "status": "reauth", "items": [], "next_before_id": None}
    assert planner_provider["status"] == "available"
    assert agent_provider["status"] == "denied"
