"""Bounded HTTPS with shared egress authorization and pinned DNS/SNI.

No proxy, redirect, retry, environment credentials or second DNS lookup.
"""

import asyncio
import os
import ssl
from urllib.parse import urlsplit

import certifi
import h11
import httpx

from app.core.outbound import OutboundRefusal, StaticPolicy, acquire_outbound_target
from app.services.calendar.contracts import CalendarError

HOSTS = ("www.googleapis.com", "caldav.yandex.ru")
MAX_BODY = 8 * 1024 * 1024


class CalendarTransport:
    async def request(self, method, url, *, headers=None, content=None, params=None):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in HOSTS or parsed.port not in (None, 443):
            raise CalendarError("invalid_calendar_url")
        if parsed.username or parsed.password or parsed.fragment or "SSLKEYLOGFILE" in os.environ:
            raise CalendarError("invalid_calendar_url")
        if method not in {"GET", "POST", "PATCH", "DELETE", "PUT", "PROPFIND", "REPORT"}:
            raise CalendarError("invalid_calendar_method")
        request = httpx.Request(method, url, headers=headers, content=content, params=params)
        request.headers.update({"Connection": "close", "Accept-Encoding": "identity"})
        body = request.content
        if len(body) > MAX_BODY:
            raise CalendarError("calendar_payload_too_large")
        writer = None
        try:
            async with asyncio.timeout(20):
                target = await asyncio.to_thread(
                    acquire_outbound_target, parsed.hostname, 443, policy=StaticPolicy((parsed.hostname,))
                )
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                context.load_verify_locations(certifi.where())
                context.minimum_version = ssl.TLSVersion.TLSv1_2
                context.set_alpn_protocols(["http/1.1"])
                reader, writer = await asyncio.open_connection(
                    target.addresses[0], 443, ssl=context, server_hostname=target.host, ssl_handshake_timeout=8
                )
                protocol = h11.Connection(h11.CLIENT, max_incomplete_event_size=16384)
                wire = protocol.send(
                    h11.Request(method=method.encode(), target=request.url.raw_path, headers=request.headers.raw)
                )
                if body:
                    wire += protocol.send(h11.Data(data=body))
                wire += protocol.send(h11.EndOfMessage())
                writer.write(wire)
                await writer.drain()
                status, response_headers, result = None, [], bytearray()
                while True:
                    event = protocol.next_event()
                    if event is h11.NEED_DATA:
                        chunk = await reader.read(16384)
                        protocol.receive_data(chunk)
                        if not chunk and status is None:
                            raise CalendarError()
                    elif isinstance(event, h11.Response):
                        status, response_headers = event.status_code, list(event.headers)
                        if any(k == b"content-encoding" and v.lower() != b"identity" for k, v in event.headers):
                            raise CalendarError()
                        if 300 <= status < 400:
                            raise CalendarError("calendar_redirect_forbidden")
                    elif isinstance(event, h11.Data):
                        result.extend(event.data)
                        if len(result) > MAX_BODY:
                            raise CalendarError("calendar_response_too_large")
                    elif isinstance(event, h11.EndOfMessage):
                        return httpx.Response(status, headers=response_headers, content=bytes(result), request=request)
                    else:
                        raise CalendarError()
        except (OSError, TimeoutError, OutboundRefusal, h11.ProtocolError, httpx.HTTPError, ValueError):
            raise CalendarError() from None
        finally:
            if writer is not None:
                writer.close()
                try:
                    async with asyncio.timeout(1):
                        await writer.wait_closed()
                except (OSError, TimeoutError):
                    pass
