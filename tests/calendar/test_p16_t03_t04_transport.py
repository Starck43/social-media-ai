"""Calendar HTTPS transport boundary tests with an in-memory TLS stream."""

import pytest

from app.integrations.calendar.transport import MAX_BODY, CalendarTransport
from app.services.calendar.contracts import CalendarError


class Reader:
    def __init__(self, wire):
        self.wire = wire

    async def read(self, size):
        chunk, self.wire = self.wire[:size], self.wire[size:]
        return chunk


class Writer:
    def __init__(self):
        self.written = b""
        self.closed = False

    def write(self, value):
        self.written += value

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


def setup_stream(monkeypatch, response_wire):
    import asyncio
    from types import SimpleNamespace

    writer = Writer()

    def acquire(host, port, *, policy):
        return SimpleNamespace(addresses=["203.0.113.10"], host=host)

    async def to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    async def open_connection(address, port, **kwargs):
        assert address == "203.0.113.10" and port == 443
        assert kwargs["server_hostname"] == "www.googleapis.com"
        return Reader(response_wire), writer

    monkeypatch.setattr("app.integrations.calendar.transport.acquire_outbound_target", acquire)
    monkeypatch.setattr(asyncio, "to_thread", to_thread)
    monkeypatch.setattr(asyncio, "open_connection", open_connection)
    return writer


@pytest.mark.asyncio
async def test_transport_pins_host_and_parses_single_response_without_proxy(monkeypatch):
    writer = setup_stream(monkeypatch, b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
    result = await CalendarTransport().request(
        "GET", "https://www.googleapis.com/calendar/v3/events", headers={"X-Test": "yes"}
    )
    assert result.status_code == 200 and result.content == b"ok"
    assert b"GET /calendar/v3/events HTTP/1.1" in writer.written
    assert b"X-Test: yes" in writer.written
    assert b"Connection: close" in writer.written
    assert writer.closed


@pytest.mark.parametrize(
    "url",
    [
        "http://www.googleapis.com/events",
        "https://evil.test/events",
        "https://www.googleapis.com:444/events",
        "https://user@www.googleapis.com/events",
        "https://www.googleapis.com/events#fragment",
    ],
)
@pytest.mark.asyncio
async def test_transport_rejects_unsafe_urls_before_opening_stream(url):
    with pytest.raises(CalendarError, match="invalid_calendar_url"):
        await CalendarTransport().request("GET", url)


@pytest.mark.asyncio
async def test_transport_rejects_unsupported_methods_and_oversize_request_body():
    transport = CalendarTransport()
    with pytest.raises(CalendarError, match="invalid_calendar_method"):
        await transport.request("TRACE", "https://www.googleapis.com/events")
    with pytest.raises(CalendarError, match="calendar_payload_too_large"):
        await transport.request("PUT", "https://www.googleapis.com/events", content=b"x" * (MAX_BODY + 1))


@pytest.mark.parametrize(
    "wire,code",
    [
        (
            b"HTTP/1.1 302 Found\r\nLocation: https://evil.test/\r\nContent-Length: 0\r\n\r\n",
            "calendar_redirect_forbidden",
        ),
        (b"HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nContent-Length: 0\r\n\r\n", "source_unavailable"),
    ],
)
@pytest.mark.asyncio
async def test_transport_rejects_redirect_and_compressed_responses(monkeypatch, wire, code):
    setup_stream(monkeypatch, wire)
    with pytest.raises(CalendarError) as exc:
        await CalendarTransport().request("GET", "https://www.googleapis.com/events")
    assert exc.value.code == code


@pytest.mark.asyncio
async def test_transport_stops_response_when_body_exceeds_limit(monkeypatch):
    wire = b"HTTP/1.1 200 OK\r\nContent-Length: " + str(MAX_BODY + 1).encode() + b"\r\n\r\n" + b"x" * (MAX_BODY + 1)
    setup_stream(monkeypatch, wire)
    with pytest.raises(CalendarError, match="calendar_response_too_large"):
        await CalendarTransport().request("GET", "https://www.googleapis.com/events")
