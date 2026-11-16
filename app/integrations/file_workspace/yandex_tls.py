"""Private, DNS-pinned TLS stream for a future Yandex metadata transport.

No credentials or requests are sent here. This does not enable browsing: the
HTTP endpoint, protocol, redirects, response limits and logging still require
an independently reviewed transport before this stream can be used by a route.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
import ssl
from collections.abc import Callable

import certifi

from app.integrations.file_workspace.yandex_network import _HOST, _PORT, NetworkUnavailable, _connect_pinned


def _tls_context() -> ssl.SSLContext:
    """Use the installed CA bundle, not proxy or CA environment overrides."""
    # Some OpenSSL builds honor this variable independently of Python's
    # SSLContext.keylog_filename. Never construct a context when it is set.
    if "SSLKEYLOGFILE" in os.environ:
        raise NetworkUnavailable("network_unavailable")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cafile=certifi.where())
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.set_alpn_protocols(["http/1.1"])
    return context


async def _open_tls_to_ip(
    host: str,
    port: int,
    *,
    _socket_factory: Callable = socket.socket,
    _loop=None,
    _open_connection: Callable = asyncio.open_connection,
    _context_factory: Callable = _tls_context,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Connect a numeric public IP and authenticate the fixed provider name.

    Keyword seams are private, trusted test/application dependencies, not input
    from an HTTP request. The caller owns and must close the returned writer.
    """
    try:
        address = ipaddress.ip_address(host)
        if (
            port != _PORT
            or not address.is_global
            or address.is_multicast
            or address.is_reserved
            or getattr(address, "ipv4_mapped", None) is not None
            or "%" in host
        ):
            raise ValueError
        family = socket.AF_INET if address.version == 4 else socket.AF_INET6
        context = _context_factory()
        if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
            raise ValueError
        sock = _socket_factory(family, socket.SOCK_STREAM)
    except (ValueError, TypeError, OSError):
        raise NetworkUnavailable("network_unavailable") from None
    try:
        sock.setblocking(False)
        loop = _loop if _loop is not None else asyncio.get_running_loop()
        await loop.sock_connect(sock, (str(address), _PORT))
        # Fail closed if the process environment changed while connecting.
        if "SSLKEYLOGFILE" in os.environ:
            raise NetworkUnavailable("network_unavailable")
        return await _open_connection(
            sock=sock,
            ssl=context,
            server_hostname=_HOST,
            ssl_handshake_timeout=8.0,
        )
    except BaseException:
        # Even cancellation or a failed TLS handshake must not leak the socket.
        sock.close()
        raise


async def open_yandex_tls() -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Resolve once and open verified TLS under the DNS/connect deadline.

    Caller must close the returned writer; no retry, proxy or alternate host.
    """
    return await _connect_pinned(_open_tls_to_ip)
