"""One installation-wide egress seam for every outbound connector (P14-T09).

`acquire_outbound_target(host, port, ...)` is the single place that decides whether
this server may open a connection to `host:port`. It is provider-neutral: callers
pass a host, a port and their code-owned static policy; OAuth, REST, IMAP or OData
details stay with the caller. The function is pure over two injected boundaries —
a DNS resolver and a registry reader — so tests never touch the network or a DB.

Rules, in order:

1. Normalize the host (lower case, no trailing dot).
2. A non-empty static policy must match the host, regardless of registry entries.
   Explicit self-hosted flows validate the host before choosing their policy.
3. Resolve DNS on *every* attempt. Nothing here caches or pins an address.
4. Without a registry entry every resolved address must be globally routable
   (`private_address_forbidden`). A `public` entry keeps that rule; a `private`
   entry authorizes exactly its listed addresses, nothing else
   (`address_not_authorized`). A changed DNS answer therefore fails closed.

An unreadable, empty or absent registry is the empty reader: only code-owned hosts
are reachable, and no environment variable ever widens that.
"""

from __future__ import annotations

import contextlib
import contextvars
import fnmatch
import ipaddress
import logging
import socket
from dataclasses import dataclass, field
from typing import AsyncIterator, Awaitable, Callable, Iterable, Mapping

logger = logging.getLogger(__name__)

HOST_NOT_TRUSTED = "host_not_trusted"
ADDRESS_NOT_AUTHORIZED = "address_not_authorized"
PRIVATE_ADDRESS_FORBIDDEN = "private_address_forbidden"
TRUST_REFUSALS = frozenset({HOST_NOT_TRUSTED, ADDRESS_NOT_AUTHORIZED, PRIVATE_ADDRESS_FORBIDDEN})
INVALID_TARGET = "invalid_outbound_target"
DNS_RESOLUTION_FAILED = "dns_resolution_failed"

# The actor-facing owner of every trust refusal: tenants cannot change the registry.
REFUSAL_OWNER = "installation_administrator"

_MESSAGES = {
    HOST_NOT_TRUSTED: "Хост {host} не входит в список доверенных: добавить его может только администратор установки.",
    ADDRESS_NOT_AUTHORIZED: "Адрес хоста {host} не одобрен администратором установки: подключение заблокировано.",
    PRIVATE_ADDRESS_FORBIDDEN: "Хост {host} указывает на внутренний адрес: разрешить его может только администратор "
    "установки.",
    INVALID_TARGET: "Недопустимый адрес назначения.",
    DNS_RESOLUTION_FAILED: "Не удалось определить адрес хоста {host}.",
}

KINDS = ("public", "private")


class OutboundRefusal(RuntimeError):
    """Typed, value-safe refusal; names the host and the owner who can change it."""

    def __init__(self, code: str, host: str = "") -> None:
        self.code = code
        self.host = host
        self.owner = REFUSAL_OWNER if code in TRUST_REFUSALS else ""
        super().__init__(_MESSAGES[code].format(host=host or "?"))


@dataclass(frozen=True)
class TrustedHost:
    """One installation-approved destination as seen by the seam (no DB types)."""

    host: str
    kind: str
    authorized_addresses: frozenset[str] = frozenset()


@dataclass(frozen=True)
class StaticPolicy:
    """Code-owned provider policy. Empty `allowed_hosts` means "any globally routable host"."""

    allowed_hosts: tuple[str, ...] = ()

    def matches(self, host: str) -> bool:
        if not self.allowed_hosts:
            return True
        return any(fnmatch.fnmatchcase(host, pattern.lower()) for pattern in self.allowed_hosts)


@dataclass(frozen=True)
class OutboundTarget:
    """Validated destination for exactly one attempt; never store or reuse it."""

    host: str
    port: int
    addresses: tuple[str, ...]
    trusted_entry: TrustedHost | None = field(default=None)


Resolver = Callable[[str, int], list[tuple]]
RegistryReader = Callable[[str], "TrustedHost | None"]
RegistryLoader = Callable[[], Awaitable[RegistryReader]]


def default_dns_resolver(host: str, port: int) -> list[tuple]:
    """The only production resolution path for outbound connectors."""
    return socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)


def empty_registry(_host: str) -> TrustedHost | None:
    return None


def registry_from_entries(entries: Iterable[TrustedHost]) -> RegistryReader:
    """Freeze a snapshot into a reader; used by the DB loader and by tests."""
    table: Mapping[str, TrustedHost] = {entry.host: entry for entry in entries}
    return lambda host: table.get(host)


def normalize_host(host: str) -> str:
    if not isinstance(host, str):
        raise OutboundRefusal(INVALID_TARGET)
    normalized = host.strip().lower().rstrip(".")
    if _is_ip(normalized):
        return normalize_address(normalized)
    if not normalized or len(normalized) > 253 or any(ch in normalized for ch in "/@?#:%[] \t"):
        raise OutboundRefusal(INVALID_TARGET)
    return normalized


def normalize_address(address: str) -> str:
    """Canonical text form of one IP address, or ValueError."""
    return str(ipaddress.ip_address(str(address).strip()))


def is_ip_literal(value: str) -> bool:
    return _is_ip(value)


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _authorized(entry: TrustedHost | None, address: str) -> str | None:
    """Return a refusal code for one resolved address, or None when it is allowed."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return PRIVATE_ADDRESS_FORBIDDEN if entry is None else ADDRESS_NOT_AUTHORIZED
    if entry is None:
        return None if ip.is_global else PRIVATE_ADDRESS_FORBIDDEN
    if entry.kind == "private":
        return None if str(ip) in entry.authorized_addresses else ADDRESS_NOT_AUTHORIZED
    return None if ip.is_global else ADDRESS_NOT_AUTHORIZED


def acquire_outbound_target(
    host: str,
    port: int,
    *,
    policy: StaticPolicy = StaticPolicy(),
    registry: RegistryReader | None = None,
    resolver: Resolver = default_dns_resolver,
) -> OutboundTarget:
    """Authorize one outbound attempt to `host:port`; raise `OutboundRefusal` otherwise."""
    normalized = normalize_host(host)
    if type(port) is not int or not 0 < port <= 65535:
        raise OutboundRefusal(INVALID_TARGET, normalized)
    reader = registry if registry is not None else current_registry()
    try:
        entry = reader(normalized)
    except Exception:  # noqa: BLE001 - an unreadable registry is the empty registry
        logger.warning("outbound_registry_unreadable error_code=registry_read_failed")
        entry = None
    if entry is not None and entry.kind not in KINDS:
        entry = None

    if not policy.matches(normalized):
        raise OutboundRefusal(HOST_NOT_TRUSTED, normalized)

    if _is_ip(normalized):
        addresses = [normalized]
    else:
        try:
            answers = resolver(normalized, port)
        except OSError as exc:
            raise OutboundRefusal(DNS_RESOLUTION_FAILED, normalized) from exc
        addresses = [answer[4][0] for answer in answers or () if answer[4] and isinstance(answer[4][0], str)]
    if not addresses:
        raise OutboundRefusal(DNS_RESOLUTION_FAILED, normalized)

    for address in addresses:
        refusal = _authorized(entry, address)
        if refusal is not None:
            raise OutboundRefusal(refusal, normalized)
    return OutboundTarget(normalized, port, tuple(addresses), entry)


# --- per-operation registry snapshot ------------------------------------------------
#
# The registry is read once per connector operation, never per process: a consumer
# opens `registry_scope(loader)` around one user-visible attempt, and nested calls
# (token refresh, REST call) see the same snapshot. Outside a scope the reader is
# empty, so code that forgets to open one stays cloud-only instead of failing open.

_current: contextvars.ContextVar[RegistryReader | None] = contextvars.ContextVar(
    "trusted_outbound_registry", default=None
)


def current_registry() -> RegistryReader:
    return _current.get() or empty_registry


@contextlib.asynccontextmanager
async def registry_scope(loader: RegistryLoader) -> AsyncIterator[RegistryReader]:
    existing = _current.get()
    if existing is not None:
        yield existing
        return
    try:
        reader = await loader()
    except Exception:  # noqa: BLE001 - unreadable registry → cloud-only, no error
        logger.warning("outbound_registry_unreadable error_code=registry_load_failed")
        reader = empty_registry
    token = _current.set(reader)
    try:
        yield reader
    finally:
        _current.reset(token)
