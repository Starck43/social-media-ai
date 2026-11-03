"""Normalize provider rate-limit responses without retaining response bodies."""

import math
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


def _delay(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and value >= 0 else None


class ProviderLimitSignal(Exception):
    def __init__(self, retry_after=None):
        self.retry_after = _delay(retry_after)
        super().__init__("Provider rate limit")


def retry_after_seconds(value, *, now=None):
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        numeric = float(text)
    except ValueError:
        numeric = None
    if numeric is not None:
        # Enormous valid delta-seconds must not degrade into a 1s cooldown.
        if math.isinf(numeric) and numeric > 0 and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", text):
            return 1e300
        return _delay(numeric)
    try:
        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            return None
        return max(0.0, (parsed - (now or datetime.now(timezone.utc))).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def provider_signal(provider, status, headers=None, body=None, *, now=None):
    """Recognize known signals; callers retain no raw provider response here."""
    bitrix_limit = provider == "bitrix24" and isinstance(body, dict) and body.get("error") == "QUERY_LIMIT_EXCEEDED"
    if status != 429 and not bitrix_limit and not (provider == "onec" and status == 503):
        return None
    header = next((v for k, v in (headers or {}).items() if str(k).lower() == "retry-after"), None)
    return ProviderLimitSignal(retry_after_seconds(header, now=now))
