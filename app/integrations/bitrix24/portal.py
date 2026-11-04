"""Bitrix24 portal domain validation and endpoint builder (P14-T01).

Per Owner Decision D2 (Q-18):
- Cloud portals (`*.bitrix24.ru`, `*.bitrix24.by`, `*.bitrix24.kz`, `*.bitrix24.com`,
  `*.bitrix24.eu`, `*.bitrix24.de`, `*.bitrix24.ua`, `*.bitrix24.pl`, `*.bitrix24.br`,
  `*.bitrix24.in`) are accepted by default.
- On-premise portals are accepted ONLY when the installation administrator has
  approved the host in the trusted outbound host registry (P14-T09); the caller
  passes that decision as `is_trusted_host`. `allowed_onprem_domains` remains a
  static, code-owned list for compatibility; no connector reads it from settings.
- Raw IP literals, credentials, ports, query strings, paths, or internal hostnames
  are always rejected.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Callable

CLOUD_BITRIX_SUFFIXES: tuple[str, ...] = (
    ".bitrix24.ru",
    ".bitrix24.by",
    ".bitrix24.kz",
    ".bitrix24.com",
    ".bitrix24.eu",
    ".bitrix24.de",
    ".bitrix24.ua",
    ".bitrix24.pl",
    ".bitrix24.br",
    ".bitrix24.in",
)

_HOSTNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")


class InvalidBitrixPortalError(ValueError):
    """Value-safe portal validation error."""

    def __init__(self, code: str = "invalid_portal_domain", message: str = "Invalid Bitrix24 portal domain") -> None:
        super().__init__(message)
        self.code = code


def normalize_portal_domain(
    raw_domain: str,
    *,
    allowed_onprem_domains: set[str] | tuple[str, ...] = (),
    is_trusted_host: Callable[[str], bool] | None = None,
) -> str:
    """Validate and normalize a Bitrix24 portal hostname (e.g., `company.bitrix24.ru`)."""
    if not isinstance(raw_domain, str) or not raw_domain.strip():
        raise InvalidBitrixPortalError("portal_domain_required")

    candidate = raw_domain.strip().lower()
    if candidate.startswith("https://"):
        candidate = candidate[len("https://") :].rstrip("/")
    elif candidate.startswith("http://"):
        raise InvalidBitrixPortalError("https_required")

    if "/" in candidate or ":" in candidate or "@" in candidate or "?" in candidate or "#" in candidate:
        raise InvalidBitrixPortalError("invalid_portal_domain")

    if len(candidate) > 253 or not _HOSTNAME_RE.fullmatch(candidate):
        raise InvalidBitrixPortalError("invalid_portal_domain")

    try:
        ipaddress.ip_address(candidate)
        raise InvalidBitrixPortalError("ip_literal_forbidden")
    except ValueError as exc:
        if isinstance(exc, InvalidBitrixPortalError):
            raise

    onprem_allowlist = {d.strip().lower() for d in allowed_onprem_domains if isinstance(d, str) and d.strip()}
    if candidate in onprem_allowlist:
        return candidate
    if is_trusted_host is not None and is_trusted_host(candidate):
        return candidate

    for suffix in CLOUD_BITRIX_SUFFIXES:
        if candidate.endswith(suffix):
            prefix = candidate[: -len(suffix)]
            if prefix and "." not in prefix and len(prefix) >= 2:
                return candidate

    raise InvalidBitrixPortalError("untrusted_portal_domain")


def build_portal_authorize_url(portal_domain: str) -> str:
    return f"https://{portal_domain}/oauth/authorize/"


def build_portal_rest_url(portal_domain: str, method_name: str) -> str:
    clean_method = method_name.strip().strip("/")
    if not re.fullmatch(r"[a-zA-Z0-9_.]+", clean_method):
        raise InvalidBitrixPortalError("invalid_rest_method")
    return f"https://{portal_domain}/rest/{clean_method}.json"
