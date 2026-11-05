"""Per-capability operator controls; no global token or approval can bypass them."""

from __future__ import annotations

import os
import re

from app.channels.senders.contracts import EMAIL, MAX, TELEGRAM, SendBinding
from app.core.tenant_context import current_tenant_id, is_bypass

_PREFIXES = {EMAIL: "P15_EMAIL_SEND", TELEGRAM: "P15_TELEGRAM_BOT_SEND", MAX: "P15_MAX_BOT_SEND"}


def admission_code(capability: str, binding: SendBinding) -> str | None:
    """Re-read switches for every admission, including immediately before I/O."""
    prefix = _PREFIXES.get(capability)
    if prefix is None:
        return "unsupported_capability"
    if any(
        type(value) is not int or value <= 0
        for value in (binding.workspace_id, binding.binding_id, binding.owner_user_id, binding.actor_user_id)
    ):
        return "invalid_binding"
    workspace_id = current_tenant_id()
    if is_bypass() or type(workspace_id) is not int or workspace_id != binding.workspace_id:
        return "workspace_mismatch"
    if binding.active is not True or binding.verified is not True:
        return "account_unavailable"
    if os.environ.get(f"{prefix}_KILL", "0") != "0":
        return "capability_killed"
    if os.environ.get(f"{prefix}_ENABLED", "0") != "1":
        return "capability_disabled"
    entries = os.environ.get(f"{prefix}_BINDINGS", "").split(",")
    if any(re.fullmatch(r"[1-9][0-9]*:[1-9][0-9]*", entry) is None for entry in entries):
        return "invalid_release_bindings"
    if f"{binding.workspace_id}:{binding.binding_id}" not in entries:
        return "binding_not_released"
    return None
