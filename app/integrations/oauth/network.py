"""Global-DNS and allowlisted HTTPS transport policy for OAuth 2.0 endpoints (P13-T08).

Reuses the P12-T08 network safety rules:
- Only `https://` URLs with no embedded credentials or fragments are allowed.
- Hostname must match the provider's allowlisted hosts (or exact template pattern).
- DNS resolution must return at least one address, and EVERY resolved IP address
  must be globally routable (`ipaddress.ip_address(ip).is_global`) — rejecting
  loopback, RFC1918 private, link-local, multicast, and unspecified addresses.
- HTTP redirects are disabled (`follow_redirects=False`).
"""

from __future__ import annotations

import fnmatch
import ipaddress
import socket
from typing import Callable
from urllib.parse import urlsplit

import httpx


class OAuthNetworkError(RuntimeError):
    """Value-safe network/DNS/TLS error for OAuth transport."""

    def __init__(self, code: str, message: str = "OAuth network validation failed") -> None:
        super().__init__(message)
        self.code = code


ResolverCallable = Callable[[str, int], list[tuple]]


def default_dns_resolver(host: str, port: int) -> list[tuple]:
    return socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)


def validate_oauth_url(
    url: str,
    *,
    allowed_hosts: tuple[str, ...] = (),
    resolver: ResolverCallable = default_dns_resolver,
) -> tuple[str, int]:
    """Validate an OAuth endpoint URL and verify all resolved IPs are globally routable."""
    if not isinstance(url, str) or not url.strip():
        raise OAuthNetworkError("invalid_oauth_url")
    try:
        parsed = urlsplit(url.strip())
        port = parsed.port or 443
    except ValueError as exc:
        raise OAuthNetworkError("invalid_oauth_url") from exc

    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port not in (443, 8443)
    ):
        raise OAuthNetworkError("unsafe_oauth_url")

    host = parsed.hostname.lower().rstrip(".")
    if allowed_hosts:
        matched = any(fnmatch.fnmatchcase(host, pattern.lower()) for pattern in allowed_hosts)
        if not matched:
            raise OAuthNetworkError("untrusted_oauth_host")

    try:
        ip_obj = ipaddress.ip_address(host)
        if not ip_obj.is_global:
            raise OAuthNetworkError("unsafe_address")
        return host, port
    except ValueError:
        pass

    try:
        answers = resolver(host, port)
    except OSError as exc:
        raise OAuthNetworkError("dns_resolution_failed") from exc

    addresses: list[str] = []
    for answer in answers:
        sockaddr = answer[4]
        if sockaddr and isinstance(sockaddr[0], str):
            addresses.append(sockaddr[0])

    if not addresses:
        raise OAuthNetworkError("dns_resolution_failed")

    for addr in addresses:
        try:
            parsed_ip = ipaddress.ip_address(addr)
        except ValueError as exc:
            raise OAuthNetworkError("unsafe_address") from exc
        if not parsed_ip.is_global:
            raise OAuthNetworkError("unsafe_address")

    return host, port


async def post_oauth_form(
    url: str,
    data: dict[str, str],
    *,
    allowed_hosts: tuple[str, ...] = (),
    resolver: ResolverCallable = default_dns_resolver,
    http_client: httpx.AsyncClient | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict]:
    """POST form-encoded parameters to a validated OAuth endpoint without following redirects."""
    validate_oauth_url(url, allowed_hosts=allowed_hosts, resolver=resolver)

    if http_client is not None:
        resp = await http_client.post(
            url,
            data=data,
            headers={"Accept": "application/json"},
            follow_redirects=False,
            timeout=timeout,
        )
    else:
        async with httpx.AsyncClient(follow_redirects=False, timeout=timeout, verify=True) as client:
            resp = await client.post(
                url,
                data=data,
                headers={"Accept": "application/json"},
            )

    if 300 <= resp.status_code < 400:
        raise OAuthNetworkError("redirect_forbidden")

    try:
        payload = resp.json()
    except ValueError as exc:
        raise OAuthNetworkError("invalid_token_response") from exc

    if not isinstance(payload, dict):
        raise OAuthNetworkError("invalid_token_response")

    return resp.status_code, payload
