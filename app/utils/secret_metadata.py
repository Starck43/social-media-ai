"""Value-safe recursive connector configuration projection; no credential access."""

from __future__ import annotations

import re

_SENSITIVE = ("token", "secret", "password", "key", "authorization", "cookie", "credential", "cipher", "session")


def safe_secret_metadata(value, *, depth=0):
    if depth > 12:
        return None
    if isinstance(value, dict):
        return {
            key: safe_secret_metadata(item, depth=depth + 1)
            for key, item in value.items()
            if isinstance(key, str) and not any(part in key.lower() for part in _SENSITIVE)
        }
    if isinstance(value, (list, tuple)):
        return [safe_secret_metadata(item, depth=depth + 1) for item in value[:1000]]
    if isinstance(value, str):
        if re.search(
            r"(?i)(?:bearer\s+|gAAAAA|(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)\s*[=:])", value
        ):
            return "[redacted]"
        return value
    return value if value is None or type(value) in (bool, int, float) else None
