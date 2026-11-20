"""Offline bounded range, recurrence and actor/connection checks."""
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import pytest
from app.integrations.calendar.google import GoogleCalendar
from app.services.calendar.contracts import CalendarError, ReauthRequired
from app.services.calendar.service import CalendarService
from app.services.planner.tasks import planner_service

__source_only__ = True
START=datetime(2026,3,1,tzinfo=timezone.utc)
END=datetime(2026,4,1,tzinfo=timezone.utc)

@pytest.mark.asyncio
async def test_google_range_uses_occurrences_and_bounded_pages():
    adapter=GoogleCalendar('fake','primary')
    adapter._request=AsyncMock(side_effect=[
      {'items':[{'id':'private', 'summary':'Встреча', 'start':{'dateTime':'2026-03-02T10:00:00Z'}}], 'nextPageToken':'next'},
      {'items':[{'id':'private2', 'summary':'Повтор', 'start':{'dateTime':'2026-03-04T00:00:00Z'}}]}])
    result=await adapter.range_events(start=START,end=END)
    assert result == {'complete':True, 'items':[{'title':'Встреча','when':'2026-03-02T10:00:00Z'},{'title':'Повтор','when':'2026-03-04T00:00:00Z'}]}
    calls=adapter._request.await_args_list
    first=calls[0].kwargs['params']; second=calls[1].kwargs['params']
    assert first['singleEvents']=='true' and first['timeMin']=='2026-03-01T00:00:00Z' and first['timeMax']=='2026-04-01T00:00:00Z'
    assert first['maxResults']==100 and 'syncToken' not in first and second['pageToken']=='next'

@pytest.mark.asyncio
async def test_google_range_partial_and_invalid_tokens():
    adapter=GoogleCalendar('fake','primary')
    adapter._request=AsyncMock(return_value={'items':[], 'nextPageToken':'next'})
    assert (await adapter.range_events(start=START,end=END,max_pages=1)) == {'items':[], 'complete':False}
    with pytest.raises(CalendarError): await adapter.range_events(start=START,end=END,max_pages=2)
    with pytest.raises(ValueError): await adapter.range_events(start=START.replace(tzinfo=None),end=END)

class Session:
    async def scalar(self, query): return NS(connection_id=7)

@pytest.mark.asyncio
async def test_range_denial_before_adapter_and_reauth(monkeypatch):
    svc=CalendarService()
    monkeypatch.setattr(planner_service, '_actor', AsyncMock(side_effect=PermissionError('denied')))
    adapter=AsyncMock(); monkeypatch.setattr(svc,'_adapter',adapter)
    with pytest.raises(PermissionError): await svc.events_range(session=Session(),start=START,end=END)
    adapter.assert_not_awaited()
    monkeypatch.setattr(planner_service, '_actor', AsyncMock(return_value=(42,8,'member')))
    conn=NS(id=7,is_active=True,state='verified',provider_id='google_calendar')
    get=AsyncMock(return_value=conn); monkeypatch.setattr(svc,'_connection',get)
    adapter.side_effect=ReauthRequired()
    result=await svc.events_range(session=Session(),start=START,end=END)
    assert result == {'state':'reauth','items':None,'complete':False}
    assert conn.state=='reauth'
    assert get.await_args.args[1:3] == (7,(42,8,'member'))

@pytest.mark.asyncio
async def test_range_connection_lookup_scopes_owner_and_tenant():
    from sqlalchemy.dialects import postgresql
    class Capture:
        query=None
        async def scalar(self, query):
            self.query=query
            return NS(is_active=True,state='verified')
    session=Capture()
    await CalendarService()._connection(session, 7, (42,8,'member'), verified=False)
    compiled=session.query.compile(dialect=postgresql.dialect())
    sql=str(compiled)
    assert 'tenant_id' in sql and 'owner_user_id' in sql and 'provider_id' in sql
    assert {42,8,7}.issubset(set(v for v in compiled.params.values() if isinstance(v,int)))

@pytest.mark.asyncio
async def test_range_skips_cancelled_occurrence():
    adapter=GoogleCalendar('fake','primary')
    adapter._request=AsyncMock(return_value={'items':[{'status':'cancelled'}]})
    assert await adapter.range_events(start=START,end=END) == {'items':[], 'complete':True}


@pytest.mark.asyncio
@pytest.mark.parametrize("start_value", [
    {"dateTime": "2026-02-28T23:59:59Z"}, {"dateTime": "2026-04-01T00:00:00Z"},
    {"dateTime": "2026-03-08T02:30:00"}, {"dateTime": "not-a-date"},
    {"dateTime": "2026-03-01 00:00:00+00:00"}, {"date": "2026-02-30"},
    {"dateTime": "2026-03-01T00:00:00Z", "date": "2026-03-01"},
])
async def test_google_rejects_unverified_event_instead_of_partial_success(start_value):
    adapter=GoogleCalendar('fake','primary')
    adapter._request=AsyncMock(return_value={'items':[
        {'summary':'Valid', 'start':{'dateTime':'2026-03-02T10:00:00Z'}},
        {'summary':'Invalid', 'start':start_value}]})
    with pytest.raises(CalendarError, match='invalid_calendar_response'):
        await adapter.range_events(start=START,end=END)

@pytest.mark.asyncio
async def test_google_start_inclusive_and_dst_offset_normalized():
    adapter=GoogleCalendar('fake','primary')
    adapter._request=AsyncMock(return_value={'items':[
        {'summary':'Start','start':{'dateTime':'2026-03-01T00:00:00Z'}},
        {'summary':'DST','start':{'dateTime':'2026-03-08T03:30:00-04:00'}}]})
    result=await adapter.range_events(start=START,end=END)
    assert result['complete'] and [item['title'] for item in result['items']] == ['Start','DST']

@pytest.mark.asyncio
async def test_google_overlap_from_previous_month_is_not_provider_failure():
    adapter = GoogleCalendar.__new__(GoogleCalendar)
    adapter._request = AsyncMock(return_value={'items': [
        {'summary': 'Starts earlier', 'start': {'dateTime': '2026-02-28T23:00:00Z'}, 'end': {'dateTime': '2026-03-01T01:00:00Z'}},
        {'summary': 'Starts in month', 'start': {'dateTime': '2026-03-01T00:00:00Z'}}]})
    result = await adapter.range_events(start=START, end=END)
    assert result == {'items': [{'title': 'Starts in month', 'when': '2026-03-01T00:00:00Z'}], 'complete': True}


@pytest.mark.asyncio
async def test_mixed_all_day_range_requests_inventory_not_partial_or_error(monkeypatch):
    adapter=GoogleCalendar('fake','primary')
    adapter._request=AsyncMock(return_value={'items':[
        {'summary':'Timed','start':{'dateTime':'2026-03-02T10:00:00Z'}},
        {'summary':'All day','start':{'date':'2026-03-08'}}]})
    svc=CalendarService()
    monkeypatch.setattr(planner_service, '_actor', AsyncMock(return_value=(42,8,'member')))
    monkeypatch.setattr(svc,'_connection',AsyncMock(return_value=NS(id=7,is_active=True,state='verified',provider_id='google_calendar')))
    monkeypatch.setattr(svc,'_adapter',AsyncMock(return_value=adapter))
    assert await svc.events_range(session=Session(),start=START,end=END) == {'state':'range_unavailable','items':None,'complete':False}
