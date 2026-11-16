"""Explicit 1C destination validation and per-attempt DNS authorization.

The workspace allowlist must come from an authorized administrator, not request
input. A caller must connect only to the returned address while verifying TLS
against the returned hostname. Validation alone is not a network transport.
"""

import ipaddress
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from app.core.outbound import (
    OutboundRefusal,
    OutboundTarget,
    Resolver,
    StaticPolicy,
    acquire_outbound_target,
    default_dns_resolver,
    empty_registry,
    is_ip_literal,
    normalize_host,
)


class OneCEndpointError(ValueError):
    """Safe fixed error code without provider input or credentials."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class OneCEndpoint:
    host: str
    port: int
    base_path: str
    interface: str

    @property
    def health_path(self) -> str:
        suffix = "/odata/standard.odata/$metadata" if self.interface == "odata_standard" else "/ping"
        return self.base_path + suffix


def validate_endpoint(base_url: str, interface: str, allowed_hosts: frozenset[str]) -> OneCEndpoint:
    """Validate a URL against an independently supplied exact workspace host allowlist."""
    if not isinstance(base_url, str) or any(ord(c) < 33 or ord(c) == 127 for c in base_url):
        raise OneCEndpointError("invalid_endpoint")
    if interface not in {"odata_standard", "http_service"}:
        raise OneCEndpointError("invalid_interface")
    try:
        parsed = urlsplit(base_url)
        host = normalize_host(parsed.hostname or "")
        port = parsed.port if parsed.port is not None else 443
        allowed = {normalize_host(h) for h in allowed_hosts}
    except (ValueError, TypeError, OutboundRefusal):
        raise OneCEndpointError("invalid_endpoint") from None
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or is_ip_literal(host)
        or not 0 < port <= 65535
    ):
        raise OneCEndpointError("invalid_endpoint")
    if host not in allowed:
        raise OneCEndpointError("host_not_allowed")
    decoded = unquote(parsed.path)
    if (
        not parsed.path.startswith("/")
        and parsed.path
        or any(ord(c) < 32 or ord(c) == 127 for c in decoded)
        or "\\" in decoded
        or "%" in decoded
        or decoded.count("/") != parsed.path.count("/")
        or any(part in {".", ".."} for part in decoded.split("/"))
        or "?" in decoded
        or "#" in decoded
    ):
        raise OneCEndpointError("invalid_endpoint")
    return OneCEndpoint(host, port, parsed.path.rstrip("/"), interface)


def resolve_endpoint(endpoint: OneCEndpoint, *, resolver: Resolver = default_dns_resolver) -> OutboundTarget:
    """Resolve afresh and reject every non-public answer even if globally trusted.

    The returned target is valid for only one attempt and must be used by an
    IP-pinned transport. The future P18 tunnel is not implemented here.
    """
    try:
        target = acquire_outbound_target(
            endpoint.host,
            endpoint.port,
            policy=StaticPolicy((endpoint.host,)),
            registry=empty_registry,
            resolver=resolver,
        )
        if any(ipaddress.ip_address(address).is_multicast for address in target.addresses):
            raise OneCEndpointError("private_address_forbidden")
        return target
    except OutboundRefusal as exc:
        raise OneCEndpointError(exc.code) from None
