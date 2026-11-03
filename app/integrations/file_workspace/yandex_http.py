"""Fixed-endpoint metadata HTTP/1.1 transport; not wired to routes or jobs.

No redirects, proxies, compression, retries, generic URL fetching, or request
logging. Authentication and folder policy remain the caller's responsibility.
This client is compatible with the metadata reader's narrow ``stream`` call.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

import h11
import httpx

from app.integrations.file_workspace.policy import _components
from app.integrations.file_workspace.yandex_disk import (
    _FIELDS,
    _MAX_OFFSET,
    _MAX_PAGE_SIZE,
    _MAX_RESPONSE_BYTES,
    _METADATA_URL,
    MetadataError,
)
from app.integrations.file_workspace.yandex_network import NetworkUnavailable
from app.integrations.file_workspace.yandex_tls import open_yandex_tls

_MAX_HEADER_BYTES = 16 * 1024
_MAX_REQUEST_BYTES = 16 * 1024
_READ_SIZE = 8192
_CLOSE_TIMEOUT = 1.0


@dataclass(frozen=True)
class _Response:
    status_code: int
    _body: bytes

    async def aiter_bytes(self):
        if self._body:
            yield self._body


def _request_bytes(method, url, params, headers, follow_redirects) -> tuple[h11.Connection, bytes]:
    """Build just the reader's fixed GET; reject unsupported inputs before I/O."""
    try:
        if method != "GET" or url != _METADATA_URL or follow_redirects is not False:
            raise ValueError
        if not isinstance(params, dict) or set(params) != {"path", "limit", "offset", "sort", "fields"}:
            raise ValueError
        if not isinstance(params["path"], str) or not params["path"].startswith("disk:/"):
            raise ValueError
        _components(params["path"])
        if type(params["limit"]) is not int or not 1 <= params["limit"] <= _MAX_PAGE_SIZE:
            raise ValueError
        if type(params["offset"]) is not int or not 0 <= params["offset"] <= _MAX_OFFSET:
            raise ValueError
        if params["sort"] != "name" or params["fields"] != _FIELDS:
            raise ValueError
        if not isinstance(headers, dict) or set(headers) != {"Authorization", "Accept"}:
            raise ValueError
        auth = headers["Authorization"]
        if not isinstance(auth, str) or not auth.startswith("OAuth ") or headers["Accept"] != "application/json":
            raise ValueError
        token = auth[6:]
        if not 1 <= len(token) <= 2048 or any(not 33 <= ord(char) <= 126 for char in token):
            raise ValueError
        request = httpx.Request(
            "GET",
            _METADATA_URL,
            params=params,
            headers={**headers, "Accept-Encoding": "identity", "Connection": "close"},
        )
        protocol = h11.Connection(h11.CLIENT, max_incomplete_event_size=_MAX_HEADER_BYTES)
        result = protocol.send(h11.Request(method=b"GET", target=request.url.raw_path, headers=request.headers.raw))
        result += protocol.send(h11.EndOfMessage())
        if len(result) > _MAX_REQUEST_BYTES:
            raise ValueError
        return protocol, result
    except (ValueError, TypeError, KeyError, UnicodeError, httpx.InvalidURL, httpx.HTTPError, h11.ProtocolError):
        raise httpx.HTTPError("provider_unavailable") from None


async def _exchange(protocol, reader, writer, request: bytes) -> _Response:
    writer.write(request)
    await writer.drain()
    status = None
    header_bytes = 0
    body = bytearray()
    keep_body = True
    while True:
        event = protocol.next_event()
        if event is h11.NEED_DATA:
            chunk = await reader.read(_READ_SIZE)
            if not isinstance(chunk, (bytes, bytearray)):
                raise httpx.HTTPError("provider_unavailable")
            if status is None:
                header_bytes += len(chunk)
                if header_bytes > _MAX_HEADER_BYTES:
                    raise httpx.HTTPError("provider_unavailable")
            protocol.receive_data(chunk)
            if not chunk and status is not None and protocol.our_state is h11.MUST_CLOSE:
                # Unframed body: the peer closed, which is its terminator. Account
                # for our side so h11 releases the buffered event instead of
                # waiting for data that will never come (RFC 9112 §3.3.2).
                protocol.send(h11.ConnectionClosed())
        elif isinstance(event, h11.Response):
            status = event.status_code
            encodings = [value.strip().lower() for name, value in event.headers if name.lower() == b"content-encoding"]
            if any(value != b"identity" for value in encodings) or len(encodings) > 1:
                raise httpx.HTTPError("provider_unavailable")
            # Non-200 bodies are drained, never returned: the reader reports the
            # status alone, and leaving the message unconsumed would not be a
            # bounded single-use exchange.
            keep_body = status == 200
            if keep_body:
                for name, value in event.headers:
                    if name.lower() == b"content-length" and int(value) > _MAX_RESPONSE_BYTES:
                        raise MetadataError("response_too_large")
        elif isinstance(event, h11.Data):
            if len(body) + len(event.data) > _MAX_RESPONSE_BYTES:
                raise MetadataError("response_too_large")
            if keep_body:
                body.extend(event.data)
        elif isinstance(event, h11.EndOfMessage):
            if status is None:
                raise httpx.HTTPError("provider_unavailable")
            return _Response(status, bytes(body) if keep_body else b"")
        else:
            # No informational responses, protocol upgrades or ambiguous states.
            raise httpx.HTTPError("provider_unavailable")


class YandexMetadataClient:
    """Single-use bounded exchange, with a private opener seam for mock tests."""

    def __init__(self, *, _opener: Callable = open_yandex_tls):
        self._opener = _opener

    @asynccontextmanager
    async def stream(self, method, url, *, params, headers, timeout, follow_redirects):
        # The caller's absolute deadline governs the whole exchange (TLS, request,
        # bounded body). A second timer here would only create two conflicting
        # budgets, so the reader's timeout is what counts. The connection is
        # always closed, which is also what terminates a `chunked` or unframed
        # body (RFC 9112 §4.4/§3.3.2).
        del timeout
        protocol, request = _request_bytes(method, url, params, headers, follow_redirects)
        writer = None
        try:
            reader, writer = await self._opener()
            response = await _exchange(protocol, reader, writer, request)
        except (TimeoutError, OSError, ValueError, h11.ProtocolError, NetworkUnavailable):
            raise httpx.HTTPError("provider_unavailable") from None
        finally:
            if writer is not None:
                try:
                    writer.close()
                    async with asyncio.timeout(_CLOSE_TIMEOUT):
                        await writer.wait_closed()
                except (TimeoutError, OSError):
                    pass
        yield response
