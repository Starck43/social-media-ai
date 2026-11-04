"""Global-DNS and allowlisted HTTPS transport policy for OAuth 2.0 endpoints (P13-T08).

Reuses the P12-T08 network safety rules:
- Only `https://` URLs with no embedded credentials or fragments are allowed.
- Hostname must match the provider's allowlisted hosts (or exact template pattern),
  (installation approval does not widen a non-empty provider allowlist).
- DNS resolution must return at least one address, and EVERY resolved IP address
  must be globally routable unless the registry authorizes it (P14-T09).
- HTTP redirects are disabled (`follow_redirects=False`).

Host, DNS and address decisions live in the shared seam `app.core.outbound`;
this module owns only the OAuth URL shape and the legacy error codes.
"""

from __future__ import annotations

from typing import Callable
from urllib.parse import urlsplit

import httpx

from app.core import outbound
from app.core.outbound import OutboundRefusal, RegistryReader, StaticPolicy, acquire_outbound_target


class OAuthNetworkError(RuntimeError):
    """Value-safe network/DNS/TLS error for OAuth transport."""

    def __init__(
        self, code: str, message: str = "OAuth network validation failed", *, refusal: OutboundRefusal | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        # The typed seam refusal (host + owner) when the seam made the decision.
        self.refusal = refusal


ResolverCallable = Callable[[str, int], list[tuple]]


# The one production resolver lives in the seam; re-exported for compatibility.
default_dns_resolver = outbound.default_dns_resolver

# Seam refusal -> pre-P14-T09 OAuth code, so empty-registry behaviour is unchanged.
_LEGACY_CODES = {
    outbound.HOST_NOT_TRUSTED: "untrusted_oauth_host",
    outbound.PRIVATE_ADDRESS_FORBIDDEN: "unsafe_address",
    outbound.DNS_RESOLUTION_FAILED: "dns_resolution_failed",
    outbound.INVALID_TARGET: "unsafe_oauth_url",
    outbound.ADDRESS_NOT_AUTHORIZED: "address_not_authorized",
}


def validate_oauth_url(
    url: str,
    *,
    allowed_hosts: tuple[str, ...] = (),
    resolver: ResolverCallable = default_dns_resolver,
    registry: RegistryReader | None = None,
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

    try:
        target = acquire_outbound_target(
            parsed.hostname,
            port,
            policy=StaticPolicy(tuple(allowed_hosts)),
            registry=registry,
            resolver=resolver,
        )
    except OutboundRefusal as exc:
        raise OAuthNetworkError(_LEGACY_CODES[exc.code], refusal=exc) from exc
    return target.host, target.port


async def post_oauth_form(
    url: str,
    data: dict[str, str],
    *,
    allowed_hosts: tuple[str, ...] = (),
    resolver: ResolverCallable = default_dns_resolver,
    http_client: httpx.AsyncClient | None = None,
    timeout: float = 10.0,
    registry: RegistryReader | None = None,
) -> tuple[int, dict]:
    """POST form-encoded parameters to a validated OAuth endpoint without following redirects."""
    validate_oauth_url(url, allowed_hosts=allowed_hosts, resolver=resolver, registry=registry)

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
