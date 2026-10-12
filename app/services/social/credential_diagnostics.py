"""Bounded credential diagnostic errors; never inspect exception text or payloads."""

from __future__ import annotations

import httpx


def credential_http_error(status_code: object) -> str:
    """Use a numeric status only, never a request URL or response description."""
    if type(status_code) is not int or not 100 <= status_code <= 599:
        return "error: invalid_response"
    if status_code in (401, 403):
        return "error: authorization_error"
    if status_code == 429:
        return "error: rate_limited"
    if status_code >= 500:
        return "error: provider_unavailable"
    return "error: provider_error"


def credential_exception_status(error: BaseException) -> str:
    """Classify known types without formatting messages, args or tracebacks."""
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return "error: timeout"
    if isinstance(error, httpx.HTTPStatusError):
        response = getattr(error, "response", None)
        return credential_http_error(getattr(response, "status_code", None))
    if isinstance(error, (ConnectionError, httpx.TransportError)):
        return "error: connection_error"
    if isinstance(error, ImportError):
        return "error: integration_unavailable"
    if isinstance(error, PermissionError):
        return "error: authorization_error"
    if isinstance(error, ValueError):
        return "error: invalid_response"
    # Telethon is optional: classify a small fixed vocabulary without importing it.
    names = {base.__name__ for base in type(error).__mro__}
    if names & {"AuthKeyUnregisteredError", "SessionRevokedError", "UnauthorizedError"}:
        return "error: authorization_error"
    if names & {"FloodWaitError", "FloodError"}:
        return "error: rate_limited"
    return "error: integration_error"


def credential_provider_error(platform: str, payload: object) -> str:
    """Only allowlisted numeric error codes affect the diagnostic category."""
    if not isinstance(payload, dict):
        return "error: invalid_response"
    if platform == "vk":
        error = payload.get("error")
        if not isinstance(error, dict):
            return "error: invalid_response"
        code = error.get("error_code")
        if type(code) is not int:
            return "error: invalid_response"
        if code in (5, 7, 15, 27, 28):
            return "error: authorization_error"
        if code in (6, 9, 29):
            return "error: rate_limited"
        return "error: provider_error"
    if platform == "telegram":
        return credential_http_error(payload.get("error_code"))
    return "error: provider_error"
