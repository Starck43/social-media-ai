"""Legacy provider cost display/projection parsing, not settlement evidence."""

from typing import Any, Optional


def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip().replace("$", "").replace("USD", "").strip().lower()
        try:
            return float(s)
        except ValueError:
            return None
    return None


def extract_cost_info(raw_response: Any) -> dict[str, Any]:
    """Extract token usage and reported dollar costs from a raw LLM API response.

    Understands OpenAI/Anthropic usage plus common provider cost extensions
    (OpenRouter `cost`, LiteLLM proxy `prompt_cost`/`completion_cost`/`total_cost`).

    Returns:
        tokens:      {"input": int, "output": int, "total": int}
        cost:        {"input": float|None, "output": float|None, "total": float|None}
        price_per_1k: {"input": float|None, "output": float|None}  (derived from cost/tokens)
        has_cost_report: bool
    """
    empty = {
        "tokens": {"input": 0, "output": 0, "total": 0},
        "cost": {"input": None, "output": None, "total": None},
        "price_per_1k": {"input": None, "output": None},
        "has_cost_report": False,
    }
    if not isinstance(raw_response, dict):
        return empty

    usage = raw_response.get("usage")
    if not isinstance(usage, dict) or not usage:
        # Some providers report cost at the top level of the response
        if _num(raw_response.get("total_cost")) is not None or _num(raw_response.get("cost")) is not None:
            return {
                "tokens": {"input": 0, "output": 0, "total": 0},
                "cost": {
                    "input": None,
                    "output": None,
                    "total": _num(raw_response.get("total_cost") or raw_response.get("cost")),
                },
                "price_per_1k": {"input": None, "output": None},
                "has_cost_report": True,
            }
        return empty

    input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)

    input_cost = _num(usage.get("prompt_cost") or usage.get("input_cost"))
    output_cost = _num(usage.get("completion_cost") or usage.get("output_cost"))
    total_cost = _num(usage.get("total_cost") or usage.get("cost"))
    if total_cost is None:
        total_cost = _num(raw_response.get("total_cost") or raw_response.get("cost"))

    has_report = any(v is not None for v in (input_cost, output_cost, total_cost))

    def per_1k(cost: Optional[float], tokens: int) -> Optional[float]:
        if cost is None or tokens <= 0:
            return None
        return round(cost / tokens * 1000, 10)

    return {
        "tokens": {
            "input": input_tokens,
            "output": output_tokens,
            "total": input_tokens + output_tokens,
        },
        "cost": {"input": input_cost, "output": output_cost, "total": total_cost},
        "price_per_1k": {
            "input": per_1k(input_cost, input_tokens),
            "output": per_1k(output_cost, output_tokens),
        },
        "has_cost_report": has_report,
    }

