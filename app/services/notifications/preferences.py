"""Validation and defaults for personal notification presentation settings."""

from __future__ import annotations

import re

DEFAULTS = {
    "version": 1,
    "connections": True,
    "tasks": True,
    "popup": False,
    "quiet": False,
    "start": "22:00",
    "end": "08:00",
}
FIELDS = frozenset(DEFAULTS)
_CLOCK = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def validate(value: object) -> dict | None:
    if not isinstance(value, dict) or set(value) != FIELDS or type(value.get("version")) is not int:
        return None
    if value["version"] != 1:
        return None
    if any(type(value[key]) is not bool for key in ("connections", "tasks", "popup", "quiet")):
        return None
    if any(not isinstance(value[key], str) or not _CLOCK.fullmatch(value[key]) for key in ("start", "end")):
        return None
    if value["quiet"] and value["start"] == value["end"]:
        return None
    return {key: value[key] for key in DEFAULTS}
