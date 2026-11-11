"""Bounded, IP-pinned HTTPS health requests for the 1C connection pilot.

This low-level primitive is not an API or a connection authorization mechanism.
A service must authorize the actor, load the workspace's current allowlist and
secrets, and supply the shared tenant/account rate limiter before calling it.
"""

import asyncio
import base64
import hashlib
import os
import ssl
from dataclasses import dataclass
from urllib.parse import quote

import certifi
import h11

from app.core.outbound import Resolver, default_dns_resolver

from .endpoint import OneCEndpoint, OneCEndpointError, resolve_endpoint

TIMEOUT_SECONDS = 30
MAX_BODY_BYTES = 2 * 1024 * 1024


class OneCHealthError(RuntimeError):
    """Fixed code only: remote bodies and credentials never enter an error."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class HealthResult:
    code: str
    metadata_sha256: str | None


async def check_health(
    endpoint: OneCEndpoint,
    login: str,
    password: str,
    *,
    limiter,
    budget,
    resolver: Resolver = default_dns_resolver,
) -> HealthResult:
    """Perform one GET only, consuming one shared budget admission first."""
    if not isinstance(login, str) or not isinstance(password, str) or not login or not password or ":" in login:
        raise OneCHealthError("invalid_credentials")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in login + password) or "SSLKEYLOGFILE" in os.environ:
        raise OneCHealthError("invalid_credentials")
    writer = None
    try:
        async with asyncio.timeout(TIMEOUT_SECONDS):
            async with limiter.acquire(budget, max_wait=0):
                target = await asyncio.to_thread(resolve_endpoint, endpoint, resolver=resolver)
                tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                tls.load_verify_locations(certifi.where())
                tls.minimum_version = ssl.TLSVersion.TLSv1_2
                tls.set_alpn_protocols(["http/1.1"])
                reader, writer = await asyncio.open_connection(
                    target.addresses[0], target.port, ssl=tls, server_hostname=target.host, ssl_handshake_timeout=8
                )
                auth = base64.b64encode(f"{login}:{password}".encode("utf-8"))
                host = target.host if target.port == 443 else f"{target.host}:{target.port}"
                protocol = h11.Connection(h11.CLIENT, max_incomplete_event_size=16384)
                wire = protocol.send(
                    h11.Request(
                        method=b"GET",
                        target=quote(endpoint.health_path, safe="/%$:@-._~").encode("ascii"),
                        headers=[
                            (b"host", host.encode("ascii")),
                            (b"authorization", b"Basic " + auth),
                            (b"accept-encoding", b"identity"),
                            (b"connection", b"close"),
                        ],
                    )
                ) + protocol.send(h11.EndOfMessage())
                writer.write(wire)
                await writer.drain()
                status, body = None, bytearray()
                while True:
                    event = protocol.next_event()
                    if event is h11.NEED_DATA:
                        chunk = await reader.read(16384)
                        protocol.receive_data(chunk)
                        if not chunk and status is None:
                            raise OneCHealthError("connection_failed")
                    elif isinstance(event, h11.Response):
                        status = event.status_code
                        if 300 <= status < 400:
                            raise OneCHealthError("redirect_forbidden")
                        if status in (401, 403):
                            raise OneCHealthError("authentication_failed")
                        if status == 429:
                            raise OneCHealthError("rate_limited")
                        if status != 200:
                            raise OneCHealthError("service_unavailable")
                        if any(k.lower() == b"content-encoding" and v.lower() != b"identity" for k, v in event.headers):
                            raise OneCHealthError("unsupported_encoding")
                    elif isinstance(event, h11.Data):
                        body.extend(event.data)
                        if len(body) > MAX_BODY_BYTES:
                            raise OneCHealthError("response_too_large")
                    elif isinstance(event, h11.EndOfMessage):
                        if not body:
                            raise OneCHealthError("empty_response")
                        digest = hashlib.sha256(body).hexdigest() if endpoint.interface == "odata_standard" else None
                        return HealthResult("healthy", digest)
                    else:
                        raise OneCHealthError("invalid_response")
    except OneCEndpointError as exc:
        raise OneCHealthError(exc.code) from None
    except (OSError, TimeoutError, ValueError, UnicodeError, h11.ProtocolError):
        raise OneCHealthError("connection_failed") from None
    finally:
        if writer is not None:
            writer.close()
            try:
                async with asyncio.timeout(1):
                    await writer.wait_closed()
            except (OSError, TimeoutError):
                pass
