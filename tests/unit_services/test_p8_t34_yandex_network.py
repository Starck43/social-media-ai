"""Mock-only DNS pinning checks; no DNS, sockets, provider or database."""

import asyncio
import socket

import pytest

from app.integrations.file_workspace import yandex_network as network

__source_only__ = True


def answer(ip, family=socket.AF_INET, port=443):
    addr = (ip, port) if family == socket.AF_INET else (ip, port, 0, 0)
    return (family, socket.SOCK_STREAM, 6, "", addr)


@pytest.mark.asyncio
async def test_connects_only_to_validated_numeric_address():
    calls = []

    async def resolve():
        return [answer("8.8.8.8"), answer("1.1.1.1")]

    async def connect(host, port):
        calls.append((host, port))
        return object()

    assert await network._connect_pinned(connect, resolver=resolve) is not None
    assert calls == [("8.8.8.8", 443)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answers",
    [
        [],
        [answer("127.0.0.1")],
        [answer("169.254.169.254")],
        [answer("10.0.0.1")],
        [answer("224.0.0.1")],
        [answer("8.8.8.8"), answer("127.0.0.1")],
        [answer("::1", socket.AF_INET6)],
        [answer("::ffff:8.8.8.8", socket.AF_INET6)],
        [answer("fe80::1%eth0", socket.AF_INET6)],
        [answer("8.8.8.8", port=80)],
        [answer("not-an-ip")],
        [answer("8.8.8.8")] * 33,
        [(socket.AF_INET, socket.SOCK_DGRAM, 0, "", ("8.8.8.8", 443))],
        [(socket.AF_INET,)],
    ],
)
async def test_unsafe_or_malformed_answers_fail_before_connect(answers):
    calls = []

    async def resolve():
        return answers

    async def connect(*args):
        calls.append(args)

    with pytest.raises(network.NetworkUnavailable, match="^network_unavailable$"):
        await network._connect_pinned(connect, resolver=resolve)
    assert calls == []


@pytest.mark.asyncio
async def test_public_ipv6_and_duplicate_addresses():
    async def resolve():
        return [answer("2606:4700:4700::1111", socket.AF_INET6)] * 2

    async def connect(host, port):
        return host, port

    assert await network._connect_pinned(connect, resolver=resolve) == ("2606:4700:4700::1111", 443)


@pytest.mark.asyncio
async def test_dns_and_connect_errors_are_sanitized():
    async def resolve():
        raise OSError("private name and token")

    async def connect(*args):
        raise AssertionError("must not connect")

    with pytest.raises(network.NetworkUnavailable) as exc:
        await network._connect_pinned(connect, resolver=resolve)
    assert str(exc.value) == "network_unavailable"
    assert exc.value.__cause__ is None


@pytest.mark.asyncio
async def test_timeout_and_cancellation_do_not_connect(monkeypatch):
    monkeypatch.setattr(network, "_TIMEOUT", 0.01)
    calls = []

    async def resolve():
        await asyncio.sleep(1)
        return [answer("8.8.8.8")]

    async def connect(*args):
        calls.append(args)

    with pytest.raises(network.NetworkUnavailable, match="^network_unavailable$"):
        await network._connect_pinned(connect, resolver=resolve)
    assert calls == []
    task = asyncio.create_task(network._connect_pinned(connect, resolver=resolve))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == []


@pytest.mark.asyncio
async def test_real_resolver_requests_only_fixed_provider_host(monkeypatch):
    calls = []

    class Loop:
        async def getaddrinfo(self, *args, **kwargs):
            calls.append((args, kwargs))
            return [answer("8.8.8.8")]

    monkeypatch.setattr(network.asyncio, "get_running_loop", lambda: Loop())
    assert await network._resolve() == [answer("8.8.8.8")]
    assert calls == [(("cloud-api.yandex.net", 443), {"type": socket.SOCK_STREAM})]


@pytest.mark.asyncio
async def test_connect_errors_and_timeout_are_sanitized(monkeypatch):
    async def resolve():
        return [answer("8.8.8.8")]

    async def failing_connect(*args):
        raise OSError("sensitive connection details")

    with pytest.raises(network.NetworkUnavailable, match="^network_unavailable$"):
        await network._connect_pinned(failing_connect, resolver=resolve)

    monkeypatch.setattr(network, "_TIMEOUT", 0.01)

    async def slow_connect(*args):
        await asyncio.sleep(1)

    with pytest.raises(network.NetworkUnavailable, match="^network_unavailable$"):
        await network._connect_pinned(slow_connect, resolver=resolve)
