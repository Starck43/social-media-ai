"""Bitrix24 OAuth 2.0 connection, REST client, rate budget, and health package (P14-T01)."""

from .client import (
    BITRIX_RATE_LIMIT_PER_SEC,
    MAX_BATCH_COMMANDS,
    Bitrix24AccessDeniedError,
    Bitrix24AuthError,
    Bitrix24Error,
    Bitrix24RateLimitError,
    Bitrix24Service,
    PortalRateGate,
    bitrix24_service,
)
from .portal import (
    CLOUD_BITRIX_SUFFIXES,
    InvalidBitrixPortalError,
    build_portal_authorize_url,
    build_portal_rest_url,
    normalize_portal_domain,
)

__all__ = [
    "BITRIX_RATE_LIMIT_PER_SEC",
    "Bitrix24AccessDeniedError",
    "Bitrix24AuthError",
    "Bitrix24Error",
    "Bitrix24RateLimitError",
    "Bitrix24Service",
    "CLOUD_BITRIX_SUFFIXES",
    "InvalidBitrixPortalError",
    "MAX_BATCH_COMMANDS",
    "PortalRateGate",
    "bitrix24_service",
    "build_portal_authorize_url",
    "build_portal_rest_url",
    "normalize_portal_domain",
]
