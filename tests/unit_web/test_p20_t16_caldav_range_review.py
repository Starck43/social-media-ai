"""Autonomous CalDAV range contract tests.

The suite stays below the service/caller boundary: it exercises only the
adapter with synthetic REPORT responses and never contacts a provider.
"""

from datetime import datetime, timezone

import pytest

from app.integrations.calendar.caldav import YandexCalendar
from app.services.calendar.contracts import CalendarError

__source_only__ = True


class Transport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def request(self, method, url, *, content=None, headers=None, params=None):
        self.calls.append((method, url, content, headers or {}, params))
        return self.responses.pop(0)


def response(status, body=b"", *, headers=None):
    import httpx

    return httpx.Response(status, content=body, headers=headers or {})


def multistatus(*rows):
    return (
        b'<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
        + b"".join(rows)
        + b"</d:multistatus>"
    )


def prop_response(href, props):
    return (
        b"<d:response><d:href>"
        + href.encode()
        + b"</d:href><d:propstat><d:prop>"
        + props
        + b"</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
    )


def event(uid, start, *, summary="Событие", extra=b""):
    return (
        b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:"
        + uid.encode()
        + b"\r\nDTSTART:"
        + start.encode()
        + b"\r\nSUMMARY:"
        + summary.encode()
        + b"\r\n"
        + extra
        + b"END:VEVENT\r\nEND:VCALENDAR\r\n"
    )


def calendar_data(raw):
    return b"<c:calendar-data>" + raw + b"</c:calendar-data>"


START = datetime(2026, 11, 1, tzinfo=timezone.utc)
END = datetime(2026, 12, 1, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_range_projects_only_bounded_nonrecurring_events_and_never_writes():
    report = multistatus(
        prop_response(
            "/cal/alice/work/later.ics",
            b'<d:getetag>"later"</d:getetag>'
            + calendar_data(event("later", "20261120T090000Z", summary="Later")),
        ),
        prop_response(
            "/cal/alice/work/earlier.ics",
            b'<d:getetag>"earlier"</d:getetag>'
            + calendar_data(event("earlier", "20261102T100000Z", summary="Earlier")),
        ),
    )
    transport = Transport([response(207, report)])
    client = YandexCalendar("alice", "password", "/cal/alice/work/", transport=transport)

    result = await client.range_events(start=START, end=END, max_items=10)

    assert result == {
        "state": "available",
        "items": [
            {"title": "Earlier", "when": "2026-11-02T10:00:00Z"},
            {"title": "Later", "when": "2026-11-20T09:00:00Z"},
        ],
        "complete": True,
    }
    assert all(set(item) == {"title", "when"} for item in result["items"])
    assert [call[0] for call in transport.calls] == ["REPORT"]
    body = transport.calls[0][2].decode()
    headers = transport.calls[0][3]
    assert "calendar-query" in body and "time-range" in body
    assert 'start="20261101T000000Z"' in body and 'end="20261201T000000Z"' in body
    assert headers["Depth"] == "1"
    assert headers["Content-Type"] == "application/xml"


@pytest.mark.asyncio
async def test_range_rejects_unexpanded_rrule_as_explicit_unavailable():
    report = multistatus(
        prop_response(
            "/cal/alice/work/weekly.ics",
            calendar_data(event("weekly", "20261102T100000Z", extra=b"RRULE:FREQ=WEEKLY\r\n")),
        ),
    )
    transport = Transport([response(207, report)])
    client = YandexCalendar("alice", "password", "/cal/alice/work/", transport=transport)

    result = await client.range_events(start=START, end=END)

    assert result == {
        "state": "range_unavailable",
        "items": None,
        "complete": False,
        "reason": "recurrence_unverified",
    }


@pytest.mark.asyncio
async def test_range_rejects_exception_set_without_returning_partial_items():
    raw = (
        b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
        b"BEGIN:VEVENT\r\nUID:series\r\nDTSTART:20261102T100000Z\r\nSUMMARY:Master\r\nEND:VEVENT\r\n"
        b"BEGIN:VEVENT\r\nUID:series\r\nRECURRENCE-ID:20261109T100000Z\r\n"
        b"DTSTART:20261109T120000Z\r\nSUMMARY:Exception\r\nEND:VEVENT\r\n"
        b"END:VCALENDAR\r\n"
    )
    client = YandexCalendar(
        "alice", "password", "/cal/alice/work/", transport=Transport(
            [response(207, multistatus(prop_response("/cal/alice/work/series.ics", calendar_data(raw))))]
        )
    )

    result = await client.range_events(start=START, end=END)

    assert result == {
        "state": "range_unavailable",
        "items": None,
        "complete": False,
        "reason": "exceptions_unverified",
    }


@pytest.mark.asyncio
async def test_range_marks_response_at_cap_incomplete_without_guessing():
    report = multistatus(
        prop_response("/cal/alice/work/one.ics", b""),
        prop_response("/cal/alice/work/two.ics", b""),
    )
    transport = Transport([response(207, report)])
    client = YandexCalendar("alice", "password", "/cal/alice/work/", transport=transport)

    result = await client.range_events(start=START, end=END, max_items=2)

    assert result == {
        "state": "range_unavailable",
        "items": None,
        "complete": False,
        "reason": "response_completeness_unconfirmed",
    }


@pytest.mark.asyncio
async def test_range_rejects_duplicate_or_foreign_resource_paths():
    report = multistatus(
        prop_response(
            "/cal/other/private.ics",
            calendar_data(event("private", "20261102T100000Z", summary="Private")),
        ),
    )
    transport = Transport([response(207, report)])
    client = YandexCalendar("alice", "password", "/cal/alice/work/", transport=transport)

    result = await client.range_events(start=START, end=END)

    assert result["state"] == "range_unavailable"
    assert result["items"] is None
    assert result["complete"] is False
    assert "private" not in str(result)
    assert "/cal/other" not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "start,end,max_items",
    [
        (datetime(2026, 11, 1), END, 100),
        (START, datetime(2026, 12, 1), 101),
        (START, datetime(2027, 1, 1, tzinfo=timezone.utc), 100),
    ],
)
async def test_range_rejects_unsafe_bounds_before_provider_request(start, end, max_items):
    transport = Transport([])
    client = YandexCalendar("alice", "password", "/cal/alice/work/", transport=transport)

    with pytest.raises(ValueError, match="invalid_calendar_range"):
        await client.range_events(start=start, end=end, max_items=max_items)

    assert transport.calls == []


@pytest.mark.asyncio
async def test_range_malformed_event_is_source_error_not_empty_success():
    report = multistatus(
        prop_response(
            "/cal/alice/work/bad.ics",
            b"<c:calendar-data>not-an-icalendar</c:calendar-data>",
        ),
    )
    transport = Transport([response(207, report)])
    client = YandexCalendar("alice", "password", "/cal/alice/work/", transport=transport)

    with pytest.raises(CalendarError, match="invalid_calendar_response"):
        await client.range_events(start=START, end=END)
