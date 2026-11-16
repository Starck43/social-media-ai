"""Small, privacy-safe exception categories shared by job paths."""

from __future__ import annotations


def classify_error(error: BaseException) -> str:
    """Return a bounded category without inspecting exception data."""
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, ConnectionError):
        return "connection_error"
    if isinstance(error, OSError):
        return "io_error"
    if isinstance(error, ValueError):
        return "value_error"
    if isinstance(error, RuntimeError):
        return "runtime_error"
    return "unexpected_error"
