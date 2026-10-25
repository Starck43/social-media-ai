"""Platform rate limits during collection: a bounded pause, then an honest deferral.

A platform that says "too many requests" has not failed and the source is not
empty: the content is still there, it just cannot be read right now. Before this
module both cases were indistinguishable from real errors (VK code 6 raised a
generic API error) or from "nothing new" (a Telegram ``FloodWaitError`` was
logged and turned into an empty pull).

The policy is deliberately small:

- a short, retryable limit is waited out with a bounded pause and retried a
  bounded number of times (``SOCIAL_RATE_LIMIT_RETRIES``,
  ``SOCIAL_RATE_LIMIT_MAX_WAIT``);
- a limit that does not clear in that budget, or one that cannot clear within a
  run (VK flood control or a daily method quota, a long Telegram flood wait),
  raises :class:`SourceRateLimited`. The collect job records the source as
  ``rate_limited`` — deferred to the next run — not as an error and not as empty.

Nothing fetched before the deferral is staged or acknowledged, so the source's
cursor does not advance and the next run reads the same window again.
Only read requests of the collection path use this; external writes are never
retried here.
"""

from __future__ import annotations

from typing import Any, Optional

from app.core.config import settings

# VK error codes (https://dev.vk.com/ru/reference/errors).
# 6 — too many requests per second: clears within a second, worth a retry.
VK_RETRYABLE_CODES = frozenset({6})
# 9 — flood control, 29 — method quota reached: do not clear within a run.
VK_DEFERRED_CODES = frozenset({9, 29})

# Exponential pause base for a retryable limit without an explicit hint.
BASE_PAUSE_SECONDS = 1.0

RATE_LIMIT_HINT = "Платформа временно ограничила частоту запросов; источник будет собран при следующем запуске."


class SourceRateLimited(Exception):
    """The platform rate limit did not clear within the bounded retry budget.

    Not a ``RuntimeError`` on purpose: VK layer fallback and token refresh catch
    ``RuntimeError`` and must not treat a rate limit as a reason to switch
    credentials and hit the same limit again.
    """

    def __init__(self, platform: str, retry_after: Optional[float] = None):
        self.platform = platform
        self.retry_after = _positive_seconds(retry_after)
        self.hint = RATE_LIMIT_HINT
        super().__init__("Сбор отложен: платформа ограничила частоту запросов")


def _positive_seconds(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value > 0 else None


def response_rate_limit(response: Any) -> Optional[tuple[bool, Optional[float]]]:
    """``(retryable, retry_after)`` for a rate-limit error body, else None.

    Recognises VK-style ``{"error": {"error_code": ...}}`` and Bot-API-style
    ``{"ok": false, "error_code": 429, "parameters": {"retry_after": N}}``.
    """
    if not isinstance(response, dict):
        return None
    error = response.get("error")
    if isinstance(error, dict):
        code = error.get("error_code")
        if type(code) is int and code in VK_RETRYABLE_CODES:
            return True, None
        if type(code) is int and code in VK_DEFERRED_CODES:
            return False, None
        return None
    if response.get("ok") is False and response.get("error_code") == 429:
        parameters = response.get("parameters")
        retry_after = parameters.get("retry_after") if isinstance(parameters, dict) else None
        return True, _positive_seconds(retry_after)
    return None


def http_rate_limit(error: Any) -> Optional[tuple[bool, Optional[float]]]:
    """``(True, retry_after)`` for an HTTP 429 status error, else None."""
    response = getattr(error, "response", None)
    if getattr(response, "status_code", None) != 429:
        return None
    headers = getattr(response, "headers", None) or {}
    try:
        retry_after = float(headers.get("Retry-After"))
    except (TypeError, ValueError):
        retry_after = None
    return True, _positive_seconds(retry_after)


def telegram_flood_wait_seconds(error: BaseException) -> Optional[float]:
    """Seconds of a Telethon flood wait, or None when ``error`` is not one.

    Matched by class name across the MRO so the optional Telethon dependency is
    not imported here (``FloodPremiumWaitError`` subclasses ``FloodWaitError``).
    """
    if not any(cls.__name__ == "FloodWaitError" for cls in type(error).__mro__):
        return None
    return _positive_seconds(getattr(error, "seconds", None)) or 0.0


def next_pause(attempt: int, retryable: bool, retry_after: Optional[float]) -> Optional[float]:
    """Seconds to wait before retry number ``attempt + 1``, or None to defer.

    A hint longer than the per-pause cap defers at once: waiting a shorter time
    and asking again would only repeat the refusal.
    """
    if not retryable or attempt >= max(0, int(settings.SOCIAL_RATE_LIMIT_RETRIES)):
        return None
    pause = retry_after if retry_after is not None else BASE_PAUSE_SECONDS * (2**attempt)
    if pause > float(settings.SOCIAL_RATE_LIMIT_MAX_WAIT):
        return None
    return pause
