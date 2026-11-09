"""Yandex CalDAV discovery, sync and conditional resource behavior."""

from datetime import date

import pytest

from app.integrations.calendar.caldav import YandexCalendar
from app.services.calendar.contracts import CalendarError, EventInput, PreconditionFailed


def response(status, body=b"", *, headers=None):
    import httpx

    return httpx.Response(status, content=body, headers=headers or {})


class Transport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def request(self, method, url, *, content=None, headers=None, params=None):
        self.calls.append((method, url, content, headers or {}))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def multistatus(*rows):
    return (
        b'<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav" '
        b'xmlns:cs="http://calendarserver.org/ns/">' + b"".join(rows) + b"</d:multistatus>"
    )


def prop_response(href, props):
    return (
        b"<d:response><d:href>"
        + href.encode()
        + b"</d:href><d:propstat><d:prop>"
        + props
        + b"</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
    )


@pytest.mark.asyncio
async def test_discovery_resolves_principal_home_and_filters_calendar_resources():
    transport = Transport(
        [
            response(
                207,
                multistatus(
                    prop_response(
                        "/", b"<d:current-user-principal><d:href>/p/alice</d:href></d:current-user-principal>"
                    )
                ),
            ),
            response(
                207,
                multistatus(
                    prop_response(
                        "/p/alice", b"<c:calendar-home-set><d:href>/calendars/alice/</d:href></c:calendar-home-set>"
                    )
                ),
            ),
            response(
                207,
                multistatus(
                    prop_response(
                        "/calendars/alice/work/",
                        b"<d:resourcetype><c:calendar/></d:resourcetype><d:displayname>Work</d:displayname>",
                    ),
                    prop_response(
                        "/calendars/alice/not-calendar/", b"<d:resourcetype/><d:displayname>Folder</d:displayname>"
                    ),
                ),
            ),
        ]
    )
    client = YandexCalendar("alice", "password", "/calendars/alice/work/", transport=transport)

    calendars = await client.discover()

    assert calendars == [{"id": "/calendars/alice/work/", "label": "Work"}]
    assert [call[0] for call in transport.calls] == ["PROPFIND"] * 3
    assert transport.calls[2][3]["Depth"] == "1"


@pytest.mark.asyncio
async def test_discovery_rejects_cross_origin_principal_before_following_it():
    transport = Transport(
        [
            response(
                207,
                multistatus(
                    prop_response(
                        "/",
                        b"<d:current-user-principal><d:href>https://evil.test/p</d:href></d:current-user-principal>",
                    )
                ),
            ),
        ]
    )
    client = YandexCalendar("alice", "password", "/calendars/alice/work/", transport=transport)

    with pytest.raises(CalendarError) as exc:
        await client.discover()

    assert exc.value.code == "invalid_calendar_url"
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_report_returns_changed_resources_and_deletions_with_new_etag_snapshot():
    report = multistatus(
        prop_response(
            "/cal/a/work/changed.ics",
            b'<d:getetag>"v2"</d:getetag><c:calendar-data>BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:changed\r\nDTSTART:20261109T100000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n</c:calendar-data>',
        ),
        prop_response(
            "/cal/a/work/same.ics",
            b'<d:getetag>"v1"</d:getetag><c:calendar-data>BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:same\r\nDTSTART:20261109T100000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n</c:calendar-data>',
        ),
    )
    transport = Transport(
        [
            response(207, multistatus(prop_response("/cal/a/work/", b"<cs:getctag>ctag-2</cs:getctag>"))),
            response(207, report),
        ]
    )
    client = YandexCalendar("alice", "password", "/cal/a/work/", transport=transport)

    items, cursor = await client.pull(
        {"ctag": "ctag-1", "etags": {"/cal/a/work/same.ics": '"v1"', "/cal/a/work/gone.ics": '"v8"'}}
    )

    assert [(item["external_id"], item["deleted"]) for item in items] == [
        ("/cal/a/work/changed.ics", False),
        ("/cal/a/work/gone.ics", True),
    ]
    assert items[0]["etag"] == '"v2"'
    assert cursor == {"ctag": "ctag-2", "etags": {"/cal/a/work/changed.ics": '"v2"', "/cal/a/work/same.ics": '"v1"'}}


@pytest.mark.asyncio
async def test_malformed_partial_report_fails_without_advancing_sync_cursor():
    malformed = multistatus(
        prop_response(
            "/cal/a/work/partial.ics",
            b'<d:getetag>"v1"</d:getetag><c:calendar-data>not an iCalendar document</c:calendar-data>',
        )
    )
    client = YandexCalendar(
        "alice",
        "password",
        "/cal/a/work/",
        transport=Transport(
            [
                response(207, multistatus(prop_response("/cal/a/work/", b"<cs:getctag>new</cs:getctag>"))),
                response(207, malformed),
            ]
        ),
    )

    with pytest.raises(CalendarError) as exc:
        await client.pull({"ctag": "old", "etags": {"/cal/a/work/old.ics": '"v0"'}})

    assert exc.value.code == "invalid_calendar_response"


@pytest.mark.asyncio
async def test_missing_optional_ctag_does_not_block_etag_snapshot():
    ctag_missing = multistatus(
        b"<d:response><d:href>/cal/a/work/</d:href><d:propstat>"
        b"<d:prop><cs:getctag/></d:prop><d:status>HTTP/1.1 404 Not Found</d:status>"
        b"</d:propstat></d:response>"
    )
    event_report = multistatus(
        prop_response(
            "/cal/a/work/event.ics",
            b'<d:getetag>"v1"</d:getetag><c:calendar-data>BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:event\r\nDTSTART:20261109T100000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n</c:calendar-data>',
        )
    )
    client = YandexCalendar(
        "alice",
        "password",
        "/cal/a/work/",
        transport=Transport(
            [
                response(207, ctag_missing),
                response(207, event_report),
            ]
        ),
    )

    items, cursor = await client.pull()

    assert len(items) == 1 and items[0]["external_id"] == "/cal/a/work/event.ics"
    assert cursor == {"ctag": None, "etags": {"/cal/a/work/event.ics": '"v1"'}}


@pytest.mark.asyncio
async def test_write_preserves_attendee_and_recurrence_exception_and_uses_etag_preconditions():
    source = (
        b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:series\r\n"
        b"DTSTART:20261109T100000Z\r\nDTEND:20261109T110000Z\r\nSUMMARY:Old\r\n"
        b"RRULE:FREQ=WEEKLY\r\nATTENDEE;CN=Alice:mailto:alice@example.test\r\nEND:VEVENT\r\n"
        b"BEGIN:VEVENT\r\nUID:series\r\nRECURRENCE-ID:20261116T100000Z\r\n"
        b"DTSTART:20261116T120000Z\r\nDTEND:20261116T130000Z\r\nSUMMARY:Exception\r\n"
        b"END:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    transport = Transport(
        [
            response(200, source, headers={"ETag": '"v1"'}),
            response(204),
            response(200, source.replace(b"SUMMARY:Old", b"SUMMARY:New"), headers={"ETag": '"v2"'}),
        ]
    )
    client = YandexCalendar("alice", "password", "/cal/a/work/", transport=transport)
    event = EventInput(title="New", start=date(2026, 11, 9), end=date(2026, 11, 10), recurrence=["RRULE:FREQ=WEEKLY"])

    await client.write("/cal/a/work/series.ics", event, etag='"v1"')

    put = transport.calls[1]
    assert put[0] == "PUT" and put[3]["If-Match"] == '"v1"'
    assert "If-None-Match" not in put[3]
    assert b"ATTENDEE" in put[2] and b"RRULE" in put[2]
    assert b"SUMMARY:Exception" in put[2] and b"SUMMARY:New" in put[2]


@pytest.mark.asyncio
async def test_create_uses_if_none_match_and_etag_mismatch_fails_before_put():
    source = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:x\r\nDTSTART:20261109T100000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    event = EventInput(title="New", start=date(2026, 11, 9), end=date(2026, 11, 10))
    creating = Transport([response(204), response(200, source, headers={"ETag": '"v1"'})])
    client = YandexCalendar("alice", "password", "/cal/a/work/", transport=creating)

    await client.write("/cal/a/work/new.ics", event)

    assert creating.calls[0][3]["If-None-Match"] == "*"
    assert "If-Match" not in creating.calls[0][3]

    stale = YandexCalendar(
        "alice",
        "password",
        "/cal/a/work/",
        transport=Transport(
            [
                response(200, source, headers={"ETag": '"v2"'}),
            ]
        ),
    )
    with pytest.raises(PreconditionFailed):
        await stale.write("/cal/a/work/existing.ics", event, etag='"v1"')
