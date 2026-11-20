"""Google Calendar wire adapter, conditional writes and complete incremental pages."""

import json
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from app.integrations.calendar.transport import CalendarTransport
from app.services.calendar.contracts import CalendarError, EventInput, PreconditionFailed, ReauthRequired


class GoogleCalendar:
    def __init__(self, token, calendar_id, *, transport=None):
        self.transport = transport or CalendarTransport()
        self.headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
        self.base = "https://www.googleapis.com/calendar/v3/calendars/" + quote(calendar_id, safe="") + "/events"

    async def _request(self, method, suffix="", *, params=None, payload=None, etag=None):
        headers = dict(self.headers)
        if etag:
            headers["If-Match"] = etag
        if payload is not None:
            headers["Content-Type"] = "application/json"
        response = await self.transport.request(
            method,
            self.base + suffix,
            headers=headers,
            params=params,
            content=json.dumps(payload).encode() if payload is not None else None,
        )
        if response.status_code in {401, 403}:
            raise ReauthRequired()
        if response.status_code in {409, 412}:
            raise PreconditionFailed()
        if response.status_code == 410:
            raise CalendarError("sync_token_expired")
        if response.status_code == 404:
            raise CalendarError("event_missing")
        if response.status_code not in {200, 201, 204}:
            raise CalendarError()
        if response.status_code == 204:
            return None
        try:
            value = response.json()
            if not isinstance(value, dict):
                raise ValueError
            return value
        except ValueError:
            raise CalendarError("invalid_calendar_response") from None

    async def pull(self, cursor=None):
        """Stage every page before returning a cursor; 410 never deletes decisions."""
        params = {"showDeleted": "true", "singleEvents": "false", "maxResults": 2500}
        if cursor:
            params["syncToken"] = cursor
        items, seen = [], set()
        for _ in range(100):
            try:
                page = await self._request("GET", params=params)
            except CalendarError as exc:
                if exc.code == "sync_token_expired" and cursor:
                    return await self.pull(None)
                raise
            for item in page.get("items", []):
                if (
                    not isinstance(item, dict)
                    or not item.get("id")
                    or (not item.get("etag") and item.get("status") != "cancelled")
                ):
                    raise CalendarError("invalid_calendar_response")
                items.append(
                    {
                        "external_id": item["id"],
                        "etag": item.get("etag"),
                        "deleted": item.get("status") == "cancelled",
                        "payload": item,
                    }
                )
            token = page.get("nextPageToken")
            if not token:
                if not page.get("nextSyncToken"):
                    raise CalendarError("invalid_calendar_response")
                return items, page["nextSyncToken"]
            if token in seen:
                raise CalendarError("invalid_calendar_response")
            seen.add(token)
            params["pageToken"] = token
        raise CalendarError("calendar_page_limit")

    async def range_events(self, *, start, end, max_pages=3):
        """Bounded, read-only occurrence query independent of sync tokens."""
        if (not isinstance(start, datetime) or not isinstance(end, datetime)
            or start.utcoffset() is None or end.utcoffset() is None
            or not timedelta(0) < end - start <= timedelta(days=33)
            or type(max_pages) is not int or not 1 <= max_pages <= 3):
            raise ValueError("invalid_calendar_range")
        def stamp(value):
            return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        params = {"timeMin": stamp(start), "timeMax": stamp(end), "singleEvents": "true",
                  "showDeleted": "false", "orderBy": "startTime", "maxResults": 100}
        items, seen = [], set()
        for _ in range(max_pages):
            page = await self._request("GET", params=dict(params))
            values = page.get("items", [])
            if not isinstance(values, list) or len(values) > 100:
                raise CalendarError("invalid_calendar_response")
            for value in values:
                if not isinstance(value, dict) or not isinstance(value.get("start"), dict):
                    raise CalendarError("invalid_calendar_response")
                start_value = value["start"].get("dateTime") or value["start"].get("date")
                title = value.get("summary") or "Событие"
                if not isinstance(start_value, str) or not isinstance(title, str):
                    raise CalendarError("invalid_calendar_response")
                items.append({"title": title[:500], "when": start_value})
            token = page.get("nextPageToken")
            if not token:
                return {"items": items, "complete": True}
            if not isinstance(token, str) or token in seen:
                raise CalendarError("invalid_calendar_response")
            seen.add(token)
            params["pageToken"] = token
        return {"items": items, "complete": False}

    async def read(self, external_id):
        item = await self._request("GET", "/" + quote(external_id, safe=""))
        return {
            "external_id": item["id"],
            "etag": item.get("etag"),
            "deleted": item.get("status") == "cancelled",
            "payload": item,
        }

    @staticmethod
    def event_body(event: EventInput):
        def bound(value):
            return (
                {"dateTime": value.isoformat(), "timeZone": event.timezone}
                if isinstance(value, datetime)
                else {"date": value.isoformat()}
            )

        return {
            "summary": event.title,
            "description": event.description,
            "location": event.location,
            "start": bound(event.start),
            "end": bound(event.end),
            "recurrence": event.recurrence,
        }

    async def write(self, external_id, event, etag=None):
        body = self.event_body(event)
        if etag is None:
            body["id"] = external_id  # Deterministic provider id, never blind duplicate inserts.
            item = await self._request("POST", payload=body)
        else:
            item = await self._request("PATCH", "/" + quote(external_id, safe=""), payload=body, etag=etag)
        return {"external_id": item["id"], "etag": item.get("etag"), "deleted": False, "payload": item}

    async def delete(self, external_id, etag):
        if not etag:
            raise PreconditionFailed()
        await self._request("DELETE", "/" + quote(external_id, safe=""), etag=etag)
