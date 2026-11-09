"""Yandex-only CalDAV, discovery and ctag/ETag comparison fallback.

Keep complete iCalendar resources, including recurrence exceptions and attendees.
No calendar expansion or schedule calculations are done locally.
"""

import base64
import hashlib
from datetime import datetime, timezone
from urllib.parse import quote, unquote, urljoin, urlsplit
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from icalendar import Calendar, Event

from app.integrations.calendar.transport import CalendarTransport
from app.services.calendar.contracts import CalendarError, PreconditionFailed, ReauthRequired

HOST = "https://caldav.yandex.ru"
D = "{DAV:}"
C = "{urn:ietf:params:xml:ns:caldav}"


def resource_path(value):
    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
        raise CalendarError("invalid_calendar_url")
    parsed = urlsplit(urljoin(HOST + "/", value))
    decoded = unquote(parsed.path)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "caldav.yandex.ru"
        or parsed.query
        or parsed.fragment
        or "\\" in decoded
        or any(part in {".", ".."} for part in decoded.split("/"))
    ):
        raise CalendarError("invalid_calendar_url")
    return parsed.path


def xml_rows(response):
    raw = response.content
    if len(raw) > 8 * 1024 * 1024 or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise CalendarError("invalid_calendar_response")
    try:
        root = ET.fromstring(raw)
        if root.tag != D + "multistatus":
            raise ValueError
        rows = []
        for item in root.findall(D + "response"):
            href = item.findtext(D + "href")
            if not href:
                raise ValueError
            properties = {}
            for stat in item.findall(D + "propstat"):
                code = stat.findtext(D + "status", "").split()
                if len(code) < 2 or code[1] not in {"200", "404"}:
                    raise ValueError
                if code[1] == "200":
                    prop = stat.find(D + "prop")
                    if prop is None:
                        raise ValueError
                    properties.update({node.tag: node for node in prop})
            if not properties:
                raise ValueError
            rows.append((resource_path(href), properties))
        return rows
    except (ValueError, ET.ParseError):
        raise CalendarError("invalid_calendar_response") from None


def validate_ical(raw):
    try:
        calendar = Calendar.from_ical(raw)
        events = calendar.walk("VEVENT")
        if not events or any(not event.get("UID") or not event.get("DTSTART") for event in events):
            raise ValueError
        if len({str(e["UID"]) for e in events}) != 1:
            raise ValueError
        return calendar
    except (ValueError, TypeError, KeyError):
        raise CalendarError("invalid_calendar_response") from None


class YandexCalendar:
    def __init__(self, login, password, calendar_id, *, transport=None):
        if any(ord(c) < 32 for c in login + password) or ":" in login:
            raise CalendarError("invalid_calendar_credentials")
        self.transport = transport or CalendarTransport()
        self.headers = {"Authorization": "Basic " + base64.b64encode((login + ":" + password).encode()).decode()}
        self.path = resource_path(calendar_id)
        if not self.path.endswith("/"):
            raise CalendarError("invalid_calendar_url")

    async def _request(self, method, path, *, body=None, headers=None, allowed=(200, 207)):
        response = await self.transport.request(
            method,
            HOST + resource_path(path),
            content=body,
            headers={**self.headers, **(headers or {})},
        )
        if response.status_code in {401, 403}:
            raise ReauthRequired()
        if response.status_code in {409, 412}:
            raise PreconditionFailed()
        if response.status_code == 404:
            raise CalendarError("event_missing")
        if response.status_code not in allowed:
            raise CalendarError()
        return response

    async def _propfind(self, path, names, depth="0"):
        body = (
            '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav" '
            'xmlns:cs="http://calendarserver.org/ns/"><d:prop>' + names + "</d:prop></d:propfind>"
        )
        return xml_rows(
            await self._request(
                "PROPFIND", path, body=body.encode(), headers={"Depth": depth, "Content-Type": "application/xml"}
            )
        )

    async def discover(self):
        roots = await self._propfind("/", "<d:current-user-principal/>")
        principal = next(
            (
                p[D + "current-user-principal"].findtext(D + "href")
                for _, p in roots
                if D + "current-user-principal" in p
            ),
            None,
        )
        if not principal:
            raise CalendarError("invalid_calendar_response")
        homes = await self._propfind(resource_path(principal), "<c:calendar-home-set/>")
        home = next(
            (p[C + "calendar-home-set"].findtext(D + "href") for _, p in homes if C + "calendar-home-set" in p), None
        )
        if not home:
            raise CalendarError("invalid_calendar_response")
        rows = await self._propfind(resource_path(home), "<d:resourcetype/><d:displayname/>", "1")
        return [
            {"id": href, "label": p.get(D + "displayname").text or "" if D + "displayname" in p else ""}
            for href, p in rows
            if D + "resourcetype" in p and p[D + "resourcetype"].find(C + "calendar") is not None
        ]

    async def pull(self, cursor=None):
        """Portable fallback: complete calendar-query + ETag comparison.

        A failed/incomplete REPORT never commits a cursor or infers deletions.
        ctag is a hint; ETags remain authoritative even if a server reuses ctag.
        """
        props = await self._propfind(self.path, "<cs:getctag/>")
        ctag = next(
            (
                p["{http://calendarserver.org/ns/}getctag"].text
                for _, p in props
                if "{http://calendarserver.org/ns/}getctag" in p
            ),
            None,
        )
        body = (
            '<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
            '<d:prop><d:getetag/><c:calendar-data/></d:prop><c:filter><c:comp-filter name="VCALENDAR">'
            '<c:comp-filter name="VEVENT"/></c:comp-filter></c:filter></c:calendar-query>'
        )
        rows = xml_rows(
            await self._request(
                "REPORT", self.path, body=body.encode(), headers={"Depth": "1", "Content-Type": "application/xml"}
            )
        )
        previous = (cursor or {}).get("etags", {})
        seen, items = {}, []
        for href, props in rows:
            if not href.startswith(self.path) or href == self.path or href in seen:
                raise CalendarError("invalid_calendar_response")
            etag = props.get(D + "getetag")
            data = props.get(C + "calendar-data")
            if etag is None or not etag.text or data is None or not data.text:
                raise CalendarError("invalid_calendar_response")
            validate_ical(data.text)
            seen[href] = etag.text
            if previous.get(href) != etag.text:
                items.append(
                    {"external_id": href, "etag": etag.text, "deleted": False, "payload": {"icalendar": data.text}}
                )
        for href, etag in previous.items():
            if href not in seen:
                items.append({"external_id": href, "etag": etag, "deleted": True, "payload": {}})
        return items, {"ctag": ctag, "etags": seen}

    def _event_path(self, external_id):
        path = resource_path(external_id)
        if not path.startswith(self.path) or path == self.path:
            raise CalendarError("invalid_calendar_url")
        return path

    async def read(self, external_id):
        path = self._event_path(external_id)
        response = await self._request("GET", path, allowed=(200,))
        validate_ical(response.content)
        etag = response.headers.get("etag")
        if not etag:
            raise CalendarError("invalid_calendar_response")
        return {"external_id": path, "etag": etag, "deleted": False, "payload": {"icalendar": response.text}}

    async def write(self, external_id, event, etag=None):
        path = self._event_path(external_id)
        if etag:
            current = await self.read(path)
            if current["etag"] != etag:
                raise PreconditionFailed()
            calendar = validate_ical(current["payload"]["icalendar"])
            master = next((e for e in calendar.walk("VEVENT") if not e.get("RECURRENCE-ID")), None)
            if master is None:
                raise PreconditionFailed()
        else:
            calendar, master = Calendar(), Event()
            calendar.add("prodid", "-//AI Assistant//Calendar//EN")
            calendar.add("version", "2.0")
            master.add("uid", hashlib.sha256(path.encode()).hexdigest() + "@ai-assistant")
            calendar.add_component(master)
        for key in ("SUMMARY", "DESCRIPTION", "LOCATION", "DTSTART", "DTEND", "DURATION", "RRULE", "RDATE", "EXDATE"):
            master.pop(key, None)
        for key, value in (
            ("summary", event.title),
            ("description", event.description),
            ("location", event.location),
            ("dtstart", event.start),
            ("dtend", event.end),
        ):
            master.add(key, value)
        # Let the maintained parser validate recurrence fields rather than writing
        # hand-built iCalendar text (including line folding and escaping).
        if event.recurrence:
            raw = (
                "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:rule\r\nDTSTART:20000101T000000Z\r\n"
                + "\r\n".join(event.recurrence)
                + "\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
            )
            rules = validate_ical(raw).walk("VEVENT")[0]
            for key in ("RRULE", "RDATE", "EXDATE"):
                if key in rules:
                    master[key] = rules[key]
        master.pop("DTSTAMP", None)
        master.add("dtstamp", datetime.now(timezone.utc))
        payload = calendar.to_ical()
        await self._request(
            "PUT",
            path,
            body=payload,
            headers={
                "Content-Type": "text/calendar; charset=utf-8",
                **({"If-Match": etag} if etag else {"If-None-Match": "*"}),
            },
            allowed=(200, 201, 204),
        )
        return await self.read(path)

    async def delete(self, external_id, etag):
        if not etag:
            raise PreconditionFailed()
        await self._request("DELETE", self._event_path(external_id), headers={"If-Match": etag}, allowed=(200, 204))
