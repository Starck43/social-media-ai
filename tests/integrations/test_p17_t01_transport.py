"""Synthetic 1C HTTPS responses: no live sockets, provider, or database."""

import asyncio
import hashlib
import socket
from contextlib import asynccontextmanager

import pytest

from app.integrations.onec.endpoint import validate_endpoint
from app.integrations.onec.transport import MAX_BODY_BYTES, OneCHealthError, check_health


class Limiter:
    def __init__(self):
        self.calls = []
        self.signals = []

    @asynccontextmanager
    async def acquire(self, budget, *, cost, max_wait):
        self.calls.append((budget, cost, max_wait))
        yield

    async def note_signal(self, budget, signal):
        self.signals.append((budget, signal.retry_after))


class Reader:
    def __init__(self, payload):
        self.payload = payload

    async def read(self, n):
        chunk, self.payload = self.payload[:n], self.payload[n:]
        return chunk


class Writer:
    def __init__(self):
        self.wire = b""
        self.closed = False

    def write(self, data):
        self.wire += data

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


def dns(host, port):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]


def response(status=200, body=b"<metadata />", extra=b""):
    return b"HTTP/1.1 %d Result\r\nContent-Length: %d\r\n" % (status, len(body)) + extra + b"\r\n" + body


def endpoint(interface="odata_standard"):
    return validate_endpoint("https://erp.example.com/base", interface, frozenset({"erp.example.com"}))


def fake_network(monkeypatch, payload):
    writer = Writer()
    calls = []

    async def open_connection(host, port, **kw):
        calls.append((host, port, kw))
        return Reader(payload), writer

    monkeypatch.setattr(asyncio, "open_connection", open_connection)
    return calls, writer


@pytest.mark.asyncio
async def test_health_pins_dns_preserves_sni_and_hashes_metadata(monkeypatch):
    calls, writer = fake_network(monkeypatch, response())
    limiter = Limiter()
    result = await check_health(
        endpoint(), "reader", "synthetic-password", limiter=limiter, budget="mock", resolver=dns
    )
    assert limiter.calls == [("mock", 2, 0)]
    assert calls[0][0:2] == ("8.8.8.8", 443)
    assert calls[0][2]["server_hostname"] == "erp.example.com"
    assert b"GET /base/odata/standard.odata/$metadata HTTP/1.1" in writer.wire
    assert b"host: erp.example.com" in writer.wire.lower()
    assert result.metadata_sha256 == hashlib.sha256(b"<metadata />").hexdigest()
    assert writer.closed


@pytest.mark.asyncio
async def test_http_service_ping_has_no_metadata_hash(monkeypatch):
    _, writer = fake_network(monkeypatch, response(body=b"pong"))
    result = await check_health(endpoint("http_service"), "reader", "mock", limiter=Limiter(), budget=1, resolver=dns)
    assert result.metadata_sha256 is None
    assert b"GET /base/ping HTTP/1.1" in writer.wire


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (301, "redirect_forbidden"),
        (302, "redirect_forbidden"),
        (401, "authentication_failed"),
        (403, "authentication_failed"),
        (429, "rate_limited"),
        (503, "service_unavailable"),
    ],
)
@pytest.mark.asyncio
async def test_error_statuses_never_follow_redirects(monkeypatch, status, code):
    calls, writer = fake_network(monkeypatch, response(status=status, body=b"provider-secret"))
    with pytest.raises(OneCHealthError) as exc:
        await check_health(endpoint(), "reader", "mock", limiter=Limiter(), budget=1, resolver=dns)
    assert exc.value.code == code
    assert "provider-secret" not in str(exc.value)
    assert len(calls) == 1 and writer.closed


@pytest.mark.asyncio
async def test_oversized_and_compressed_responses_fail_closed(monkeypatch):
    fake_network(monkeypatch, response(body=b"x" * (MAX_BODY_BYTES + 1)))
    with pytest.raises(OneCHealthError, match="response_too_large"):
        await check_health(endpoint(), "reader", "mock", limiter=Limiter(), budget=1, resolver=dns)
    fake_network(monkeypatch, response(extra=b"Content-Encoding: gzip\r\n"))
    with pytest.raises(OneCHealthError, match="unsupported_encoding"):
        await check_health(endpoint(), "reader", "mock", limiter=Limiter(), budget=1, resolver=dns)


@pytest.mark.asyncio
async def test_private_dns_does_not_open_connection(monkeypatch):
    calls, _ = fake_network(monkeypatch, response())
    private = lambda host, port: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
    with pytest.raises(OneCHealthError, match="private_address_forbidden"):
        await check_health(endpoint(), "reader", "mock", limiter=Limiter(), budget=1, resolver=private)
    assert not calls


@pytest.mark.asyncio
async def test_budget_denial_happens_before_any_connection(monkeypatch):
    calls, _ = fake_network(monkeypatch, response())

    class DeniedLimiter:
        @asynccontextmanager
        async def acquire(self, budget, *, cost, max_wait):
            raise RuntimeError("budget denied")
            yield

    with pytest.raises(RuntimeError, match="budget denied"):
        await check_health(endpoint(), "reader", "mock", limiter=DeniedLimiter(), budget=1, resolver=dns)
    assert not calls


@pytest.mark.asyncio
async def test_invalid_credentials_and_timeout_are_sanitized(monkeypatch):
    calls, _ = fake_network(monkeypatch, response())
    with pytest.raises(OneCHealthError, match="invalid_credentials"):
        await check_health(endpoint(), "invalid:login", "mock", limiter=Limiter(), budget=1, resolver=dns)
    assert not calls

    async def timed_out(*args, **kwargs):
        raise TimeoutError("sensitive provider details")

    monkeypatch.setattr(asyncio, "open_connection", timed_out)
    with pytest.raises(OneCHealthError) as exc:
        await check_health(endpoint(), "reader", "mock", limiter=Limiter(), budget=1, resolver=dns)
    assert exc.value.code == "connection_failed"
    assert "sensitive" not in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 503])
async def test_throttling_signal_is_recorded_in_shared_limiter(monkeypatch, status):
    fake_network(monkeypatch, response(status=status, extra=b"Retry-After: 12\r\n"))
    limiter = Limiter()
    with pytest.raises(OneCHealthError):
        await check_health(endpoint(), "reader", "mock", limiter=limiter, budget="shared", resolver=dns)
    assert limiter.signals == [("shared", 12.0)]


def test_two_token_admission_bounds_each_60_second_window():
    # One bucket, initial burst 10 and refill 10/60 per second as in P13-T03.
    tokens, last, accepted = 10.0, 0.0, []
    for tick in range(1200):
        now = tick / 10
        tokens = min(10.0, tokens + (now - last) / 6)
        last = now
        if tokens >= 2:
            tokens -= 2
            accepted.append(now)
    assert max(sum(start <= t < start + 60 for t in accepted) for start in accepted) <= 10
