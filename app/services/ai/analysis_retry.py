"""Strict timestamp bounds for an explicit retry of staged analysis inputs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.utils.date_parsing import universal_date_parser


def parse_staged_window(value: Any) -> dict[str, datetime]:
    """Reject missing/invalid bounds rather than silently draining all history.

    Unlike CLI day windows, a stored content range includes timestamps. Preserve
    both endpoints (including a single instant) for preview, reset and execution.
    """
    if not isinstance(value, dict):
        raise ValueError("A stored content window is required")
    bounds = {}
    for key in ("start_date", "end_date"):
        raw = value.get(key)
        if not isinstance(raw, (str, datetime)):
            raise ValueError("Both stored content bounds are required")
        parsed = universal_date_parser(raw)
        if parsed is None:
            raise ValueError("Invalid stored content bound")
        bounds[key] = parsed
    if bounds["end_date"] < bounds["start_date"]:
        raise ValueError("Reversed stored content window")
    return bounds
