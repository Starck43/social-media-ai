"""Wire-level Google Calendar adapter contract tests."""

import json
from datetime import date

import pytest

from app.integrations.calendar.google import GoogleCalendar
from app.services.calendar.contracts import CalendarError, EventInput, PreconditionFailed, ReauthRequired


class Response:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self.body = body

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


class Transport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.mark.asyncio
async def test_pull_collects_all_pages_and_only_returns_final_cursor():
    transport = Transport(
        [
            Response(200, {"items": [{"id": "e1", "etag": "v1"}], "nextPageToken": "page/2"}),
            Response(200, {"items": [{"id": "e2", "etag": "v2", "status": "cancelled"}], "nextSyncToken": "sync-3"}),
        ]
    )
    adapter = GoogleCalendar("secret", "team/calendar", transport=transport)

    items, cursor = await adapter.pull()

    assert [item["external_id"] for item in items] == ["e1", "e2"]
    assert items[1]["deleted"] is True
    assert cursor == "sync-3"
    assert transport.calls[0][1].endswith("team%2Fcalendar/events")
    assert transport.calls[0][2]["params"]["showDeleted"] == "true"
    assert transport.calls[1][2]["params"]["pageToken"] == "page/2"


@pytest.mark.asyncio
async def test_expired_sync_token_restarts_full_pull_without_returning_partial_items():
    transport = Transport(
        [
            Response(410, {}),
            Response(200, {"items": [{"id": "fresh", "etag": "v4"}], "nextSyncToken": "sync-4"}),
        ]
    )
    adapter = GoogleCalendar("secret", "primary", transport=transport)

    items, cursor = await adapter.pull("old-token")

    assert [item["external_id"] for item in items] == ["fresh"]
    assert cursor == "sync-4"
    assert transport.calls[0][2]["params"]["syncToken"] == "old-token"
    assert "syncToken" not in transport.calls[1][2]["params"]


@pytest.mark.asyncio
async def test_cancelled_tombstone_without_etag_is_retained_for_sync_deletion():
    transport = Transport(
        [
            Response(200, {"items": [{"id": "cancelled-id", "status": "cancelled"}], "nextSyncToken": "sync-5"}),
        ]
    )
    adapter = GoogleCalendar("secret", "primary", transport=transport)

    items, cursor = await adapter.pull()

    assert items == [
        {
            "external_id": "cancelled-id",
            "etag": None,
            "deleted": True,
            "payload": {"id": "cancelled-id", "status": "cancelled"},
        }
    ]
    assert cursor == "sync-5"


@pytest.mark.asyncio
async def test_write_uses_deterministic_insert_and_conditional_update():
    event = EventInput(title="Planning", start=date(2026, 11, 9), end=date(2026, 11, 10), timezone="Europe/Kirov")
    transport = Transport(
        [
            Response(201, {"id": "planned-id", "etag": "v1"}),
            Response(200, {"id": "planned-id", "etag": "v2"}),
        ]
    )
    adapter = GoogleCalendar("secret", "primary", transport=transport)

    await adapter.write("planned-id", event)
    await adapter.write("planned-id", event, etag="v1")

    first_method, _, first = transport.calls[0]
    second_method, second_url, second = transport.calls[1]
    body = json.loads(first["content"])
    assert first_method == "POST" and body["id"] == "planned-id"
    assert body["start"] == {"date": "2026-11-09"}
    assert second_method == "PATCH" and second_url.endswith("/planned-id")
    assert second["headers"]["If-Match"] == "v1"


@pytest.mark.asyncio
async def test_provider_precondition_and_revocation_errors_are_typed():
    conflict = GoogleCalendar("secret", "primary", transport=Transport([Response(412, {})]))
    with pytest.raises(PreconditionFailed):
        await conflict.write("event", EventInput(title="x", start=date(2026, 1, 1), end=date(2026, 1, 2)))

    revoked = GoogleCalendar("secret", "primary", transport=Transport([Response(401, {})]))
    with pytest.raises(ReauthRequired):
        await revoked.pull()


@pytest.mark.asyncio
async def test_invalid_page_cannot_advance_cursor():
    adapter = GoogleCalendar(
        "secret",
        "primary",
        transport=Transport(
            [
                Response(200, {"items": [{"id": "missing-etag"}], "nextSyncToken": "unsafe"}),
            ]
        ),
    )
    with pytest.raises(CalendarError) as exc:
        await adapter.pull()
    assert exc.value.code == "invalid_calendar_response"
