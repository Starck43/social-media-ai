"""Operator-supplied OAuth application credentials for global applications (P14-T05).

A global application (today: Bitrix24) is registered once, outside this product, and its
`client_id` / `client_secret` live in a secret store the operator controls. The deployment
knows only the path of an operator-supplied secret file (`BITRIX24_APP_SECRET_FILE`) and
the name of the secret inside it (`BITRIX24_APP_SECRET_NAME`). The file is JSON:
`{"client_id": "...", "client_secret": "..."}` or `{"<name>": {"client_id": ..., ...}}`.

Resolution happens per call through `resolve_app_credentials`, the single seam a caller can
replace. Nothing here logs, renders or returns the secret outside the returned tuple, and an
absent, unreadable, malformed or incomplete source raises `OAuthFlowError` with the distinct
fail-closed code `bitrix_credentials_unavailable` - there is no silent fallback.

Rollback: while the operator store is not in place, `BITRIX24_LEGACY_ENV_CREDENTIALS` (default
OFF) re-enables the historical deployment-settings path. It is never consulted once
`BITRIX24_APP_SECRET_FILE` is set.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

BITRIX24_PROVIDER_ID = "bitrix24"
CREDENTIALS_UNAVAILABLE = "bitrix_credentials_unavailable"
_MAX_SECRET_FILE_BYTES = 64 * 1024
_LEGACY_SETTING_KEYS = ("BITRIX24_CLIENT_ID", "BITRIX24_CLIENT_SECRET")

# Explicitly injected credential source per provider: a callable returning `(client_id,
# client_secret)`, or a static pair. Consulted before any configured source; tests and
# embedding deployments use it instead of touching settings or environment variables.
INJECTED_APP_CREDENTIALS: dict[str, Any] = {}


def _unavailable() -> Exception:
    # Imported lazily: `service` imports this module.
    from .service import OAuthFlowError

    return OAuthFlowError(CREDENTIALS_UNAVAILABLE)


def _read_secret_file(path: str, secret_name: str) -> tuple[str, str] | None:
    """Return credentials from the operator file, or None when the file yields no usable pair."""
    try:
        raw = Path(path).read_bytes()
        if len(raw) > _MAX_SECRET_FILE_BYTES:
            return None
        data = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(data, Mapping):
        return None
    entry: Any = data
    if secret_name and isinstance(data.get(secret_name), Mapping):
        entry = data[secret_name]
    client_id = str(entry.get("client_id") or "").strip()
    client_secret = str(entry.get("client_secret") or "").strip()
    if not client_id or not client_secret:
        return None
    return client_id, client_secret


def resolve_app_credentials(provider_id: str) -> tuple[str, str]:
    """Resolve application `(client_id, client_secret)` for a global application.

    Order: injected provider, operator secret file, then the default-OFF legacy settings
    bridge. Missing/unreadable sources fail closed; no value reaches logs or errors.
    """
    from app.core.config import settings

    injected = INJECTED_APP_CREDENTIALS.get(provider_id)
    if injected is not None:
        pair = injected() if callable(injected) else injected
        if isinstance(pair, (tuple, list)) and len(pair) == 2:
            client_id, client_secret = str(pair[0] or "").strip(), str(pair[1] or "").strip()
            if client_id and client_secret:
                return client_id, client_secret

    configured_file = str(getattr(settings, "BITRIX24_APP_SECRET_FILE", "") or "").strip()
    if configured_file:
        resolved = _read_secret_file(configured_file, str(getattr(settings, "BITRIX24_APP_SECRET_NAME", "") or ""))
        if resolved is not None:
            return resolved
        raise _unavailable()

    if getattr(settings, "BITRIX24_LEGACY_ENV_CREDENTIALS", False) is True:
        id_key, secret_key = _LEGACY_SETTING_KEYS
        client_id = str(getattr(settings, id_key, "") or "").strip()
        client_secret = str(getattr(settings, secret_key, "") or "").strip()
        if client_id and client_secret:
            return client_id, client_secret

    raise _unavailable()
