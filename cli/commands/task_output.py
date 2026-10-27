"""Stable, secret-conscious output helpers for the operator task CLI."""

from __future__ import annotations

import json
import math
import re
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

_OUTPUT_FORMAT: ContextVar[str] = ContextVar("task_cli_output_format", default="human")
_SENSITIVE_KEY = re.compile(
    r"(?:token|secret|password|credential|authorization|cookie|private[_-]?key|"
    r"api[_-]?key|dsn|connection[_-]?string)",
    re.IGNORECASE,
)
_DIAGNOSTIC_KEY = re.compile(
    r"(?:^error$|last[_-]?error|exception|traceback|diagnostic|provider[_-]?error)",
    re.IGNORECASE,
)
_SECRET_VALUE = re.compile(
    r"(?:\bBearer\s+\S+|"
    r"\b(?:sk|gh[pousr]|xox[baprs])-[A-Za-z0-9_-]{8,}|"
    r"\bpostgres(?:ql)?://\S+|"
    r"\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password)\s*[:=]\s*\S+)",
    re.IGNORECASE,
)

_ERRORS = {
    1: ("command_failed", "Command failed; diagnostic details are omitted."),
    2: ("invalid_usage", "Invalid command usage."),
    3: ("target_unresolved", "The requested workspace or task could not be resolved."),
    4: ("operator_required", "An authenticated active platform admin context is required."),
}


def set_task_output_format(output_format: str) -> None:
    """Set the format for the current CLI invocation."""
    if output_format not in {"human", "json"}:
        raise ValueError("output_format must be 'human' or 'json'")
    _OUTPUT_FORMAT.set(output_format)


def is_json_output() -> bool:
    return _OUTPUT_FORMAT.get() == "json"


@contextmanager
def task_output_format(output_format: str) -> Iterator[None]:
    """Temporarily set the output format; primarily useful to isolated tests."""
    if output_format not in {"human", "json"}:
        raise ValueError("output_format must be 'human' or 'json'")
    token = _OUTPUT_FORMAT.set(output_format)
    try:
        yield
    finally:
        _OUTPUT_FORMAT.reset(token)


def task_print(*objects: Any, **kwargs: Any) -> None:
    """Keep Rich output in human mode and suppress it in JSON mode."""
    if is_json_output():
        return
    from rich import print as rich_print

    rich_print(*objects, **kwargs)


def _safe_json_value(value: Any, depth: int = 0) -> Any:
    """Convert supported values to JSON while omitting secret/diagnostic fields."""
    if depth > 12:
        return None
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return _SECRET_VALUE.sub("[REDACTED]", value)
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or _SENSITIVE_KEY.search(key):
                continue
            if _DIAGNOSTIC_KEY.search(key) and not isinstance(item, (bool, int, float)):
                continue
            safe[key] = _safe_json_value(item, depth + 1)
        return safe
    if isinstance(value, (list, tuple)):
        return [_safe_json_value(item, depth + 1) for item in value[:500]]
    return None


def emit_task_json(command: str, exit_code: int, data: Any = None) -> None:
    """Write exactly one versioned JSON envelope; never serialize exceptions."""
    envelope: dict[str, Any] = {
        "schema_version": 1,
        "command": command,
        "ok": exit_code == 0,
        "exit_code": exit_code,
    }
    if exit_code == 0:
        envelope["data"] = _safe_json_value(data)
    else:
        error_code, message = _ERRORS.get(exit_code, _ERRORS[1])
        envelope["error"] = {"code": error_code, "message": message}
    sys.stdout.write(json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n")
    sys.stdout.flush()
