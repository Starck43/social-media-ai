"""LLM destination validation and DNS-pinned, proxy-free HTTPS transport.

HTTPcore retains the original hostname for Host/SNI/certificate verification;
only TCP connect receives a validated numeric address. Every client/attempt uses
a new pool. The installation registry is the only explicit private-host mode.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
from contextlib import asynccontextmanager
from urllib.parse import unquote, urlsplit

import httpx

from app.core.outbound import acquire_outbound_target, registry_scope

_AUTH_NAMES = {"authorization", "x-api-key", "api-key", "x-goog-api-key"}


def validate_auth_header(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    # Preserve the historical safe templates, never literal tokens or arbitrary
    # header/value injection. New callers may supply just an allowlisted name.
    if not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("Invalid authorization header")
    name, separator, template = value.partition(":")
    if name.lower() not in _AUTH_NAMES or not re.fullmatch(r"[A-Za-z-]+", name):
        raise ValueError("Invalid authorization header")
    if separator and template.strip() not in {"{key}", "Bearer {key}"}:
        raise ValueError("Invalid authorization header")
    return value


def credential_headers(value: str | None, key: str) -> dict[str, str]:
    value = validate_auth_header(value) or "Authorization"
    if not isinstance(key, str) or any(ord(c) < 32 or ord(c) == 127 for c in key):
        raise ValueError("Invalid provider credential")
    name, separator, template = value.partition(":")
    if not separator:
        template = "Bearer {key}" if name.lower() == "authorization" else "{key}"
    return {"Content-Type": "application/json", name: template.strip().replace("{key}", key)}


def validate_endpoint_path(path: str | None) -> str | None:
    if path is None:
        return None
    if not isinstance(path, str) or not path.startswith("/") or len(path) > 200:
        raise ValueError("Invalid custom endpoint path")
    decoded = path
    # Reject nested encodings too; gateways can decode more than once.
    for _ in range(4):
        if (
            decoded.startswith("//")
            or any(ord(c) <= 32 or ord(c) == 127 for c in decoded)
            or any(c in decoded for c in "\\?#")
            or any(part in {".", ".."} for part in decoded.split("/"))
        ):
            raise ValueError("Invalid custom endpoint path")
        new = unquote(decoded)
        if new == decoded:
            return path
        decoded = new
    raise ValueError("Invalid custom endpoint path")


def validate_base_url(url: str) -> str:
    if not isinstance(url, str) or any(ord(c) <= 32 or ord(c) == 127 for c in url) or "\\" in url:
        raise ValueError("Invalid provider destination")
    try:
        parsed = urlsplit(url)
        port = parsed.port or 443
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or "%" in parsed.netloc
            or not 1 <= port <= 65535
        ):
            raise ValueError
        if parsed.path:
            validate_endpoint_path(parsed.path)
        # Literal IPs are refused at save time unless installation-approved.
        try:
            ipaddress.ip_address(parsed.hostname)
        except ValueError:
            pass
        else:
            _target(parsed.hostname, port)
    except (ValueError, RuntimeError):
        raise ValueError("Invalid provider destination") from None
    return url.rstrip("/")


def provider_destination(base_url: str, path: str) -> str:
    base = validate_base_url(base_url)
    validate_endpoint_path(path)
    destination = base + path
    if urlsplit(destination).netloc != urlsplit(base).netloc:
        raise ValueError("Invalid provider destination")
    return destination


def _target(host: str, port: int):
    try:
        target = acquire_outbound_target(host, port)
        for raw in target.addresses:
            address = ipaddress.ip_address(raw)
            if (
                address.is_unspecified
                or address.is_multicast
                or address.is_link_local
                or address.is_reserved
                or getattr(address, "ipv4_mapped", None) is not None
                or getattr(address, "sixtofour", None) is not None
                or getattr(address, "teredo", None) is not None
                or str(address) == "100.100.100.200"
            ):
                raise ValueError
        return target
    except (ValueError, RuntimeError, OSError):
        raise ValueError("Provider destination is not authorized") from None


class _PinnedBackend:
    def __init__(self, backend):
        self.backend = backend

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        # Keep the connect deadline inclusive of DNS. No second hostname lookup,
        # proxies, alternative destination or retry after an address refusal.
        async with asyncio.timeout(timeout):
            target = await asyncio.to_thread(_target, host, port)
            return await self.backend.connect_tcp(
                target.addresses[0], port, timeout=timeout, local_address=local_address, socket_options=socket_options
            )

    async def connect_unix_socket(self, *args, **kwargs):
        raise ValueError("Provider destination is not authorized")

    async def sleep(self, seconds):
        await self.backend.sleep(seconds)


class ProviderTransport(httpx.AsyncHTTPTransport):
    def __init__(self):
        super().__init__(verify=True, trust_env=False, retries=0)
        # HTTPX exposes transports, HTTPcore exposes network backends; this one
        # small compatibility bridge is regression-tested against the lockfile.
        self._pool._network_backend = _PinnedBackend(self._pool._network_backend)

    async def handle_async_request(self, request):
        validate_base_url(str(request.url))
        response = await super().handle_async_request(request)
        if 300 <= response.status_code < 400:
            await response.aclose()
            raise ValueError("Provider redirects are forbidden")
        return response


@asynccontextmanager
async def provider_http_client():
    from app.models.managers.trusted_outbound_host_manager import load_trusted_registry

    async with registry_scope(load_trusted_registry):
        async with httpx.AsyncClient(transport=ProviderTransport(), follow_redirects=False, trust_env=False) as client:
            yield client
