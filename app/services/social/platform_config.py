"""Canonical non-secret platform configuration.

Defaults are creation/migration input only. Runtime reads never fall back to
process settings; missing or inactive catalog entries fail before provider I/O.
Deployment and personal credentials retain their existing secure stores.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from urllib.parse import urlsplit


class PlatformConfigurationError(ValueError):
    """A safe, value-free configuration failure suitable for diagnostics."""


# Bootstrap values, not a runtime fallback or a second configuration overlay.
_BOOTSTRAP = {
    "vk": {
        "api_base_url": "https://api.vk.com/method",
        "api_version": "5.199",
        "auth_type": "oauth",
        "oauth_base_url": "https://id.vk.ru",
        "oauth_token_url": "https://id.vk.ru/oauth2/auth",
        "redirect_uri": "http://localhost/api/v1/social/callback",
    },
    "telegram": {"api_base_url": "https://api.telegram.org", "auth_type": "bot"},
}


def platform_key(value: Any) -> str:
    return str(getattr(value, "db_value", value)).lower()


def initial_platform_params(kind: Any, params: dict | None = None) -> dict:
    """Materialize creation defaults once, preserving all supplied custom keys."""
    if params is not None and not isinstance(params, dict):
        raise PlatformConfigurationError("platform_params_must_be_object")
    result = deepcopy(_BOOTSTRAP.get(platform_key(kind), {}))
    result.update(deepcopy(params or {}))
    return result


def require_platform_param(platform: Any, key: str) -> str:
    """Read the persisted JSON, rejecting incomplete config before any request."""
    if getattr(platform, "is_active", True) is not True:
        raise PlatformConfigurationError("platform_inactive")
    return require_config_param(getattr(platform, "params", None), key)


def require_config_param(params: dict | None, key: str) -> str:
    """Validate one value from a previously loaded JSON snapshot."""
    value = params.get(key) if isinstance(params, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise PlatformConfigurationError("platform_configuration_missing")
    value = value.strip()
    if key.endswith("_url") or key == "redirect_uri":
        try:
            url = urlsplit(value)
            valid = url.scheme in ("http", "https") and bool(url.hostname) and not url.username and not url.password
            valid = valid and not url.query and not url.fragment
            url.port
        except ValueError:
            valid = False
        if not valid:
            raise PlatformConfigurationError("platform_api_url_invalid")
        return value.rstrip("/") if key in ("api_base_url", "oauth_base_url") else value
    return value


async def load_platform_config(kind: str) -> dict:
    """Fresh shared catalog read; no per-user variants, cache or env overlay."""
    from app.models import Platform
    from app.types import PlatformType

    platform_type = PlatformType.get_by_value(kind)
    if platform_type is None:
        raise PlatformConfigurationError("platform_unknown")
    rows = await Platform.objects.get_by_type(platform_type, is_active=None)
    if len(rows) != 1:
        raise PlatformConfigurationError("platform_catalog_not_unique")
    platform = rows[0]
    api_base = require_platform_param(platform, "api_base_url")
    result = dict(platform.params)
    result["api_base_url"] = api_base
    if kind == "vk":
        result["api_version"] = require_platform_param(platform, "api_version")
    return result
