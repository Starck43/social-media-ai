"""Month range remains actor-scoped and separates local dates from instants."""
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock
import pytest
from sqlalchemy.dialects import postgresql
from app.services.planner.tasks import planner_service

__source_only__ = True

class Session:
    def __init__(self): self.query = None
    async def scalars(self, query):
        self.query = query
        return self
    def all(self): return []

@pytest.mark.asyncio
async def test_calendar_range_actor_and_sql_fences(monkeypatch):
    actor = AsyncMock(return_value=(42, 8, 'member'))
    monkeypatch.setattr(planner_service, '_actor', actor)
    session = Session()
    # March 2026 in Europe/Berlin spans a DST change: different offsets.
    data = await planner_service.calendar_range(session=session, first_day=date(2026,3,1), next_day=date(2026,4,1),
        start_utc=datetime(2026,2,28,23,tzinfo=timezone.utc), end_utc=datetime(2026,3,31,22,tzinfo=timezone.utc), limit=50)
    assert data == {'items': [], 'next_before_id': None}
    actor.assert_awaited_once()
    compiled = session.query.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    for fragment in ('tenant_id', 'owner_user_id', 'assignee_id', 'recurrence_source', 'due_at', 'due_date', 'status', 'LIMIT'):
        assert fragment in sql
    assert 42 in compiled.params.values()
    assert date(2026,3,1) in compiled.params.values()
    assert datetime(2026,3,31,22,tzinfo=timezone.utc) in compiled.params.values()

@pytest.mark.asyncio
async def test_calendar_range_rejects_invalid_and_checks_actor_first(monkeypatch):
    actor = AsyncMock(side_effect=PermissionError('denied'))
    monkeypatch.setattr(planner_service, '_actor', actor)
    with pytest.raises(PermissionError):
        await planner_service.calendar_range(session=Session(), first_day=date(2026,3,1), next_day=date(2026,4,1),
            start_utc=datetime(2026,3,1,tzinfo=timezone.utc), end_utc=datetime(2026,4,1,tzinfo=timezone.utc))
    actor.assert_awaited_once()
    monkeypatch.setattr(planner_service, '_actor', AsyncMock(return_value=(42,8,'member')))
    with pytest.raises(ValueError):
        await planner_service.calendar_range(session=Session(), first_day=date(2026,3,1), next_day=date(2026,5,1),
            start_utc=datetime(2026,3,1,tzinfo=timezone.utc), end_utc=datetime(2026,5,1,tzinfo=timezone.utc))
