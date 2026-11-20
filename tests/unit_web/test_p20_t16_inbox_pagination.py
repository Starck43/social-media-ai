"""Source-only checks for bounded triage and filtered selection."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.services.communications import triage
from app.web import communications

__source_only__ = True


@pytest.mark.asyncio
async def test_page_triage_is_fenced_to_requested_thread_ids(monkeypatch):
    monkeypatch.setattr(triage, 'require_message_permission', lambda _: 42)
    monkeypatch.setattr(triage, 'get_current_user', lambda: None)
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    assert (await triage.list_triaged_threads(session, thread_ids=[2, 3]))['items'] == []
    stmt = session.execute.await_args.args[0]
    compiled = stmt.compile(dialect=postgresql.dialect())
    assert 'tenant_id' in str(compiled) and ' IN ' in str(compiled)
    assert 42 in compiled.params.values() and [2, 3] in compiled.params.values()
    session.execute.reset_mock()
    await triage.list_triaged_threads(session, thread_ids=[])
    session.execute.assert_not_awaited()
    with pytest.raises(ValueError):
        await triage.list_triaged_threads(session, thread_ids=list(range(1, 102)))


@pytest.mark.asyncio
async def test_filtered_deep_link_cannot_select_excluded_thread(monkeypatch):
    class SessionContext:
        async def __aenter__(self): return object()
        async def __aexit__(self, *_): return False
    monkeypatch.setattr(communications, 'current_tenant_id', lambda: 42)
    monkeypatch.setattr(communications, 'async_session_maker', lambda: SessionContext())
    monkeypatch.setattr(communications, 'snippet_rows', AsyncMock(return_value=[]))
    monkeypatch.setattr(communications, 'list_inbox_threads', AsyncMock(return_value={'items':[{'id': 1}, {'id': 2}], 'next_before_id': None}))
    listing = AsyncMock(return_value={'items':[{'thread_id': 1, 'category':'needs_reply'}]})
    monkeypatch.setattr(communications, 'list_triaged_threads', listing)
    detail = AsyncMock(return_value={'thread': {'id': 1}, 'messages': []})
    monkeypatch.setattr(communications, 'get_thread_detail', detail)
    monkeypatch.setattr(communications, 'render', lambda *args, **kwargs: kwargs)
    output = await communications.communications_page(
        SimpleNamespace(), boundary='cloud', tab='inbox', channel=None,
        triage_category='needs_reply', contact_id=None, thread_id=2,
        is_read=None, is_archived=False, q=None, before_id=None,
    )
    assert listing.await_args.kwargs['thread_ids'] == [1, 2]
    assert detail.await_args.args[1] == 1
    assert [item['id'] for item in output['inbox_threads']] == [1]
