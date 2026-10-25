"""Actor-bound pending intents and a one-invocation in-process approval grant."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator
from uuid import uuid4

from app.agent.identity import RuntimeIdentity, session_matches_identity
from app.core.permissions import get_current_user
from app.core.tenant_context import current_tenant_id


def _canonical(value: Any) -> str | None:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return None


def _digest(value: Any) -> str | None:
    text = _canonical(value)
    return hashlib.sha256(text.encode()).hexdigest() if text is not None else None


def arguments_digest(args: Any) -> str | None:
    """Canonical digest of a tool's arguments — the confirmation's identity.

    Public so callers can answer "is this the same request?" without
    re-implementing the canonicalisation the pending intent stores.
    """
    return _digest(args)


def tool_contract(spec: Any) -> str | None:
    if spec is None:
        return None
    return _digest(
        {
            "name": spec.name,
            "permission": spec.required_permission,
            "confirm": spec.confirm,
            "parameters": spec.parameters,
        }
    )


def _arguments(args: Any) -> bool:
    return isinstance(args, dict) and all(isinstance(key, str) for key in args) and _canonical(args) is not None


def make_pending_intent(
    identity: RuntimeIdentity,
    session: Any,
    spec: Any,
    args: Any,
    call_id: Any,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    if (
        not session_matches_identity(session, identity)
        or not identity.ai_chat_enabled
        or spec is None
        or not spec.confirm
        or not isinstance(spec.required_permission, str)
        or not spec.required_permission
        or not _arguments(args)
        or not isinstance(call_id, str)
        or not call_id
        or tool_contract(spec) is None
    ):
        return None
    now = now or datetime.now(timezone.utc)
    return {
        "intent_id": uuid4().hex,
        "name": spec.name,
        "args": json.loads(_canonical(args)),
        "tool_call_id": call_id,
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "required_permission": spec.required_permission,
        "arguments_digest": _digest(args),
        "tool_contract": tool_contract(spec),
        "authorization": {
            # Older runtimes reject this binding instead of bypassing consumption.
            "version": 3,
            "actor": identity.actor_binding(),
            "session_id": session.id,
            "approval_binding": identity.approval_binding(),
        },
    }


def pending_actor_matches(pending: Any, identity: RuntimeIdentity) -> bool:
    auth = pending.get("authorization") if isinstance(pending, dict) else None
    return isinstance(auth, dict) and _canonical(auth.get("actor")) == _canonical(identity.actor_binding())


def pending_rejection(
    pending: Any,
    identity: RuntimeIdentity,
    session: Any,
    spec: Any,
    *,
    now: datetime | None = None,
) -> str | None:
    """Static reason, or None. Authorization against fresh rights is separate."""
    if not isinstance(pending, dict) or not isinstance(pending.get("authorization"), dict):
        return "unbound_intent"
    intent_id = pending.get("intent_id")
    if (
        not isinstance(intent_id, str)
        or len(intent_id) != 32
        or any(character not in "0123456789abcdef" for character in intent_id)
    ):
        return "unbound_intent"
    auth = pending["authorization"]
    if type(auth.get("version")) is not int or auth["version"] != 3:
        return "unbound_intent"
    if _canonical(auth.get("actor")) != _canonical(identity.actor_binding()):
        return "actor_mismatch"
    if (
        not session_matches_identity(session, identity)
        or type(auth.get("session_id")) is not int
        or auth["session_id"] != session.id
    ):
        return "session_mismatch"
    if _canonical(auth.get("approval_binding")) != _canonical(identity.approval_binding()):
        return "authority_changed"
    if not identity.ai_chat_enabled:
        return "ai_chat_revoked"
    try:
        expiry = datetime.fromisoformat(pending["expires_at"])
        if expiry.tzinfo is None or expiry <= (now or datetime.now(timezone.utc)):
            return "expired_intent"
    except (KeyError, TypeError, ValueError):
        return "expired_intent"
    if (
        spec is None
        or not spec.confirm
        or spec.required_permission is None
        or pending.get("name") != spec.name
        or pending.get("required_permission") != spec.required_permission
        or pending.get("tool_contract") != tool_contract(spec)
    ):
        return "tool_changed"
    if not _arguments(pending.get("args")) or pending.get("arguments_digest") != _digest(pending["args"]):
        return "arguments_changed"
    if not isinstance(pending.get("tool_call_id"), str) or not pending["tool_call_id"]:
        return "unbound_intent"
    return None


@dataclass
class _Approval:
    tenant_id: int
    user_id: int
    user_role_id: int | None
    membership_role_id: int | None
    workspace_role_codename: str | None
    ai_chat_enabled: bool
    contract: str | None
    arguments_digest: str | None
    consumed: bool = False


_approval: ContextVar[_Approval | None] = ContextVar("agent_tool_approval", default=None)


@contextmanager
def confirmed_dispatch_scope(identity: RuntimeIdentity, spec: Any, args: dict[str, Any]) -> Iterator[None]:
    """Trusted runtime only, AFTER fresh identity/intent/right validation.

    One-use mutable grant is shared with inherited ContextVars; child tasks cannot
    reuse it after consumption. This is not a distributed DB receipt/CAS fence.
    """
    grant = _Approval(
        identity.tenant_id,
        identity.user_id,
        identity.user_role_id,
        identity.membership_role_id,
        identity.workspace_role_codename,
        identity.ai_chat_enabled,
        tool_contract(spec),
        _digest(args),
    )
    token = _approval.set(grant)
    try:
        yield
    finally:
        _approval.reset(token)


def consume_tool_confirmation(spec: Any, args: Any) -> bool:
    grant = _approval.get()
    user = get_current_user()
    from app.agent.identity import current_memory_actor

    actor = current_memory_actor()
    if (
        grant is None
        or grant.consumed
        or user is None
        or current_tenant_id() != grant.tenant_id
        or getattr(user, "id", None) != grant.user_id
        or getattr(user, "role_id", None) != grant.user_role_id
        or actor is None
        or actor[0] != grant.user_id
        or not grant.ai_chat_enabled
        or not _arguments(args)
        or grant.contract is None
        or grant.arguments_digest is None
        or grant.contract != tool_contract(spec)
        or grant.arguments_digest != _digest(args)
    ):
        return False
    grant.consumed = True
    return True
