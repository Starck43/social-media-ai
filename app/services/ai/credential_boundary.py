"""Non-secret freshness and deployment-only managed credential references.

Personal provider rows never resolve managed references. The future platform
catalog consumes this guard, not the personal encrypted store.
"""

from __future__ import annotations

import os
import re

MAX_CREDENTIAL_VERSION = 9223372036854775807


def credential_version(value: object) -> int:
    if type(value) is not int or not 1 <= value <= MAX_CREDENTIAL_VERSION:
        raise PermissionError("Credential version is unavailable")
    return value


def require_current_credential_version(accepted: object, current: object) -> None:
    if credential_version(accepted) != credential_version(current):
        raise PermissionError("Credential configuration changed; reload before dispatch")


def managed_ref(value: object, allowlist: dict[str, str]) -> str:
    """Validate an opaque alias against deployment configuration, not env names."""
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[a-z][a-z0-9_-]{0,99}", value)
        or value not in allowlist
        or not isinstance(allowlist[value], str)
        or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,199}", allowlist[value])
    ):
        raise PermissionError("Managed credential reference is unavailable")
    return value


def resolve_managed_credential(ref: object) -> str:
    """Internal platform-only resolver; never accepts an arbitrary environment key."""
    from app.core.config import settings

    allowlist = settings.LLM_MANAGED_CREDENTIAL_REFS
    alias = managed_ref(ref, allowlist)
    value = os.environ.get(allowlist[alias])
    if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise PermissionError("Managed credential is unavailable")
    return value
