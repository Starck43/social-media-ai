"""Provider-specific cache usage normalization and exact tariff pricing.

    Ordinary rates use the explicit model unit; cache rates are USD per 1M.
    Missing evidence remains ``None``;
cache write TTL categories are never inferred or combined with their aggregate.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext
from typing import Any, Mapping

MAX_TOKENS = 2147483647
MAX_COST = Decimal("999999999.999999999")

CACHE_RATE_FIELDS = (
    "cache_read_cost_per_1m",
    "cache_write_5m_cost_per_1m",
    "cache_write_1h_cost_per_1m",
)
VALID_PRICING_TOKEN_UNITS = (1000, 1_000_000)
_MISSING = object()


def pricing_token_unit(value: Any = _MISSING) -> int:
    """Validate a model unit; only an absent legacy-test-double attribute means 1K."""
    if value is _MISSING:
        return 1000
    if type(value) is not int or value not in VALID_PRICING_TOKEN_UNITS:
        raise ValueError("pricing_token_unit must be 1000 or 1000000")
    return value


def model_pricing_token_unit(model: Any) -> int:
    """Default only absent attributes on legacy test doubles; explicit NULL is invalid."""
    return pricing_token_unit(getattr(model, "pricing_token_unit", _MISSING))


def canonical_decimal(value: Any, *, max_value: Decimal | None = None) -> Decimal | None:
    """Return a finite, nonnegative, exactly representable decimal or None."""
    if type(value) not in (int, float, str, Decimal):
        return None
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0:
            return None
        if max_value is not None and amount > max_value:
            return None
        return amount
    except (InvalidOperation, ValueError, OverflowError):
        return None


def _count(value: Any) -> int | None:
    return value if type(value) is int and 0 <= value <= MAX_TOKENS else None


def normalize_cache_usage(raw: Any, api_format: str | None) -> dict[str, Any]:
    """Normalize only documented provider cache counters into fixed JSON shape."""
    usage = raw.get("usage") if isinstance(raw, Mapping) else None
    usage = usage if isinstance(usage, Mapping) else {}
    if api_format == "openai":
        details = usage.get("prompt_tokens_details")
        details = details if isinstance(details, Mapping) else {}
        return {
            "format": "openai",
            "cache_read_tokens": _count(details.get("cached_tokens")),
            "cache_write_tokens": _count(details.get("cache_write_tokens")),
            "cache_write_5m_tokens": None,
            "cache_write_1h_tokens": None,
        }
    if api_format == "anthropic":
        ttl = usage.get("cache_creation")
        ttl = ttl if isinstance(ttl, Mapping) else {}
        total_write = _count(usage.get("cache_creation_input_tokens"))
        write_5m = _count(ttl.get("ephemeral_5m_input_tokens"))
        write_1h = _count(ttl.get("ephemeral_1h_input_tokens"))
        if write_5m is not None and write_1h is not None:
            if total_write is not None and write_5m + write_1h != total_write:
                return {
                    "format": "unknown",
                    "cache_read_tokens": None,
                    "cache_write_tokens": None,
                    "cache_write_5m_tokens": None,
                    "cache_write_1h_tokens": None,
                }
            elif total_write is None:
                total_write = write_5m + write_1h
        return {
            "format": "anthropic",
            "cache_read_tokens": _count(usage.get("cache_read_input_tokens")),
            "cache_write_tokens": total_write,
            "cache_write_5m_tokens": write_5m,
            "cache_write_1h_tokens": write_1h,
        }
    return {
        "format": "unknown",
        "cache_read_tokens": None,
        "cache_write_tokens": None,
        "cache_write_5m_tokens": None,
        "cache_write_1h_tokens": None,
    }


def validate_cache_usage(value: Any) -> dict[str, Any] | None:
    """Validate stored normalized evidence without silently repairing it."""
    fields = {
        "format",
        "cache_read_tokens",
        "cache_write_tokens",
        "cache_write_5m_tokens",
        "cache_write_1h_tokens",
    }
    if not isinstance(value, Mapping) or set(value) != fields or value.get("format") not in {
        "openai",
        "anthropic",
        "unknown",
    }:
        return None
    counts = {key: value[key] for key in fields - {"format"}}
    if any(count is not None and _count(count) is None for count in counts.values()):
        return None
    if value["format"] == "openai" and any(
        counts[key] is not None for key in ("cache_write_5m_tokens", "cache_write_1h_tokens")
    ):
        return None
    if value["format"] == "anthropic":
        total, five, hour = (
            counts["cache_write_tokens"],
            counts["cache_write_5m_tokens"],
            counts["cache_write_1h_tokens"],
        )
        if five is not None and hour is not None and total is not None and five + hour != total:
            return None
        if total is not None and any(bucket is not None and bucket > total for bucket in (five, hour)):
            return None
    return dict(value)


def cache_price_usd(
    cache_usage: Any,
    tariff: Mapping[str, Any],
    *,
    prompt_tokens: int | None,
    pricing_unit: int = 1000,
) -> Decimal | None:
    """Compute cache-aware input cost; ``None`` means evidence/rate is incomplete."""
    usage = validate_cache_usage(cache_usage)
    if usage is None or usage["format"] == "unknown":
        return None
    prompt = _count(prompt_tokens)
    read = usage["cache_read_tokens"]
    writes = usage["cache_write_tokens"]
    five, hour = usage["cache_write_5m_tokens"], usage["cache_write_1h_tokens"]
    base = canonical_decimal(tariff.get("input_cost_per_1k"))
    read_rate = canonical_decimal(tariff.get("cache_read_cost_per_1m"))
    five_rate = canonical_decimal(tariff.get("cache_write_5m_cost_per_1m"))
    hour_rate = canonical_decimal(tariff.get("cache_write_1h_cost_per_1m"))
    if base is None:
        return None
    try:
        pricing_unit = pricing_token_unit(pricing_unit)
    except ValueError:
        return None

    if usage["format"] == "openai":
        # OpenAI prompt_tokens is inclusive of cached tokens.
        if prompt is None or read is None or writes is None or read + writes > prompt:
            return None
        if writes > 0:
            return None  # The aggregate has no documented TTL price category.
        ordinary = prompt - read - writes
        if read > 0 and read_rate is None:
            return None
        with localcontext() as context:
            context.prec = 64
            input_cost = Decimal(ordinary) * base / pricing_unit
            input_cost += Decimal(read) * (read_rate or Decimal(0)) / 1_000_000
    else:
        # Anthropic input_tokens excludes cache reads and writes.
        if prompt is None or read is None:
            return None
        if read > 0 and read_rate is None:
            return None
        with localcontext() as context:
            context.prec = 64
            input_cost = Decimal(prompt) * base / pricing_unit + Decimal(read) * (read_rate or Decimal(0)) / 1_000_000
        if five is None or hour is None:
            if writes == 0:
                pass
            elif writes is not None and five_rate == 0 and hour_rate == 0:
                pass
            else:
                return None  # Aggregate writes cannot identify the billed TTL.
        else:
            if writes is None or five + hour != writes:
                return None
            if five > 0 and five_rate is None or hour > 0 and hour_rate is None:
                return None
            with localcontext() as context:
                context.prec = 64
                input_cost += Decimal(five) * (five_rate or Decimal(0)) / 1_000_000
                input_cost += Decimal(hour) * (hour_rate or Decimal(0)) / 1_000_000

    if read is not None and read < 0:
        return None
    total = input_cost
    if total > MAX_COST:
        return None
    return total
