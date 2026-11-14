"""Mocked audit projections and SQL filters; PostgreSQL validation is deferred."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql
from starlette.requests import Request

from app.core.tenant_context import tenant_scope
from app.models import ActionAuditEvent
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.services.onec import policy
from app.web.onec_audit import _filters


def request(query):
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": query.encode()})


@pytest.mark.parametrize(
    "query",
    [
        "limit=1001",
        "limit=0",
        "connection_id=-1",
        "actor_id=true",
        "action=crm.contact_create",
        "since=2026-11-01",
        "limit=1&limit=2",
        "password=hidden",
        "before_id=2147483648",
    ],
)
def test_invalid_filters_fail_closed(query):
    with pytest.raises(ValueError):
        _filters(request(query))


def test_actor_filter_is_not_request_identity():
    filters = _filters(request("actor_id=99&connection_id=4&limit=8"))
    assert filters["event_actor_id"] == 99
    assert "actor_id" not in filters
    assert filters["connection_id"] == 4


async def test_manager_filters_before_limit_with_tenant_fenced_join():
    captured = []

    async def execute(statement):
        captured.append(str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})))
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    now = datetime.now(timezone.utc)
    with tenant_scope(7):
        result = await ActionAuditEvent.objects.list_events(
            session=SimpleNamespace(execute=execute),
            since=now - timedelta(days=1),
            until=now,
            onec_only=True,
            connection_id=4,
            actor_id=99,
            before_id=20,
            limit=3,
        )
    assert result == []
    sql = captured[0]
    assert "JOIN" in sql and "business_commands" in sql
    assert "connection_id = 4" in sql and "actor_id = '99'" in sql
    assert "tenant_id = 7" in sql and "resource_kind = 'command'" in sql
    assert "CAST(" in sql and "LIMIT 3" in sql and "onec.command_execute" in sql


@pytest.mark.parametrize("export", [False, True])
async def test_projection_uses_existing_manager_and_export_audit(monkeypatch, export):
    session = object()

    @asynccontextmanager
    async def owned():
        yield session

    event = SimpleNamespace(
        id=1,
        occurred_at=datetime.now(timezone.utc),
        actor_id="99",
        action="onec.command_execute",
        outcome="succeeded",
        resource_kind="command",
        resource_id="3",
        reason_code=None,
        payload="private-data-never-projected",
    )
    authorize = AsyncMock(return_value="admin")
    listing = AsyncMock(return_value=[event])
    recording = AsyncMock()
    monkeypatch.setattr(policy, "authorize_capability", authorize)
    monkeypatch.setattr(ActionAuditEvent.objects, "_get_owned_session", owned)
    monkeypatch.setattr(ActionAuditEvent.objects, "list_events", listing)
    monkeypatch.setattr(ActionAuditEvent.objects, "record_success", recording)
    result = await policy.OneCAuditView().read(
        tenant_id=7,
        actor_id=12,
        event_actor_id=99,
        export=export,
        since=event.occurred_at - timedelta(days=1),
        until=event.occurred_at,
    )
    assert "payload" not in result[0]
    assert listing.call_args.kwargs["actor_id"] == 99
    assert authorize.call_args.kwargs["actor_id"] == 12
    assert recording.await_count == int(export)
    if export:
        assert recording.call_args.kwargs["action"] == "onec.audit_export"
        assert recording.call_args.kwargs["resource_id"] == 7
        assert recording.call_args.kwargs["session"] is session


async def test_missing_audit_grant_does_not_read(monkeypatch):
    @asynccontextmanager
    async def owned():
        yield object()

    monkeypatch.setattr(policy, "authorize_capability", AsyncMock(return_value="manager"))
    monkeypatch.setattr(ActionAuditEvent.objects, "_get_owned_session", owned)
    listing = AsyncMock()
    monkeypatch.setattr(ActionAuditEvent.objects, "list_events", listing)
    with pytest.raises(ConnectionDenied):
        await policy.OneCAuditView().read(tenant_id=7, actor_id=12)
    listing.assert_not_awaited()


def test_migration_checks_match_live_registry():
    import runpy

    from app.services.audit_codes import audit_check_sql

    migration = runpy.run_path("migrations/versions/p17t05_onec_audit_export.py")
    assert migration["checks"](export=True) == audit_check_sql()
    assert "onec.audit_export" not in migration["checks"](export=False)["action"]
