"""DNS-pinned connection primitive for the future Yandex metadata transport.

This module does not send HTTP, handle credentials, or activate a route. A
caller must still provide verified TLS with the original provider hostname,
ignore proxy environment variables, enforce the endpoint/method and redaction,
and close the resulting connection. It is not an SSRF-safe HTTP client by itself.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from typing import TypeVar

_HOST = "cloud-api.yandex.net"
_PORT = 443
_MAX_ANSWERS = 32
_TIMEOUT = 10.0
_T = TypeVar("_T")


class NetworkUnavailable(Exception):
    """Generic failure without DNS answers or filesystem/provider details."""


async def _resolve() -> list:
    loop = asyncio.get_running_loop()
    return await loop.getaddrinfo(_HOST, _PORT, type=socket.SOCK_STREAM)


def _validated_addresses(answers: object) -> tuple[str, ...]:
    """Fail closed for malformed, mixed public/private or excessive answers."""
    if not isinstance(answers, (list, tuple)) or not 1 <= len(answers) <= _MAX_ANSWERS:
        raise NetworkUnavailable("network_unavailable")
    result = []
    try:
        for family, kind, _protocol, _canonname, sockaddr in answers:
            if family not in (socket.AF_INET, socket.AF_INET6) or kind != socket.SOCK_STREAM:
                raise ValueError
            if not isinstance(sockaddr, tuple) or len(sockaddr) != (2 if family == socket.AF_INET else 4):
                raise ValueError
            raw, port = sockaddr[:2]
            if not isinstance(raw, str) or "%" in raw or port != _PORT:
                raise ValueError
            if family == socket.AF_INET6 and (sockaddr[2] != 0 or sockaddr[3] != 0):
                raise ValueError
            address = ipaddress.ip_address(raw)
            if address.version != (4 if family == socket.AF_INET else 6):
                raise ValueError
            if getattr(address, "ipv4_mapped", None) is not None:
                raise ValueError
            if not address.is_global or address.is_multicast or address.is_reserved:
                raise ValueError
            canonical = str(address)
            if canonical not in result:
                result.append(canonical)
    except (ValueError, TypeError, OverflowError):
        raise NetworkUnavailable("network_unavailable") from None
    return tuple(result)


async def _connect_pinned(
    connector: Callable[[str, int], Awaitable[_T]],
    *,
    resolver: Callable[[], Awaitable[list]] = _resolve,
) -> _T:
    """Resolve once and connect to the validated numeric address, not the name.

    The connector is trusted application code and must connect to the supplied
    numeric address without a proxy or a second hostname lookup. No fallback to
    other answers is attempted. A single timeout covers DNS and connect. The
    caller remains responsible for cancellation/closing after a connection.
    """
    try:
        async with asyncio.timeout(_TIMEOUT):
            addresses = _validated_addresses(await resolver())
            return await connector(addresses[0], _PORT)
    except (TimeoutError, OSError, ValueError):
        raise NetworkUnavailable("network_unavailable") from None
