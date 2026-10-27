"""Pure VK connection-selection policy, independent of product entitlements.

No vault, environment, network or ORM access is performed here. A caller must
supply a fresh authorized snapshot before every operation; a serialized preview
is never an execution permit. Method routes describe existing client paths,
not VK application-profile availability. Q-5 owns that live evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

# Supported client routes, NOT a declaration that VK grants these methods.
METHOD_MODES: Mapping[str, frozenset[str]] = {
    "wall.get": frozenset({"api", "user"}),
    "wall.getComments": frozenset({"api", "user"}),
    "likes.getList": frozenset({"api", "user"}),
    "wall.getReposts": frozenset({"api", "user"}),
    "users.get": frozenset({"api", "user"}),
    "groups.getById": frozenset({"api", "user"}),
    "utils.resolveScreenName": frozenset({"api", "user"}),
    "newsfeed.search": frozenset({"api", "user"}),
    "groups.search": frozenset({"user"}),
    "users.search": frozenset({"user"}),
    "database.getCities": frozenset({"api", "user"}),
}


class VKSelectionError(ValueError):
    """Malformed explicit selection; messages are fixed, secret-free codes."""


def _positive_id(value: object) -> bool:
    return type(value) is int and value > 0


@dataclass(frozen=True)
class VKSelection:
    """A pinned L1 deployment connection or one exact personal vault row.

    `auto` intentionally is not accepted: it remains a legacy source-collector
    policy and must not become a selected task's implicit fallback policy.
    """

    mode: str
    credential_id: int | None = None

    def __post_init__(self) -> None:
        if self.mode not in ("api", "user"):
            raise VKSelectionError("vk_access_mode_invalid")
        if self.mode == "user" and not _positive_id(self.credential_id):
            raise VKSelectionError("vk_access_credential_required")
        if self.mode == "api" and self.credential_id is not None:
            raise VKSelectionError("vk_access_service_credential_invalid")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"mode": self.mode}
        if self.credential_id is not None:
            result["credential_id"] = self.credential_id
        return result


def parse_vk_selection(raw: object) -> VKSelection:
    """Strict parser: never accept tokens, owner overrides or arbitrary metadata."""
    if not isinstance(raw, Mapping) or set(raw) - {"mode", "credential_id"}:
        raise VKSelectionError("vk_access_selection_invalid")
    return VKSelection(mode=raw.get("mode"), credential_id=raw.get("credential_id"))


@dataclass(frozen=True)
class VKAccessSnapshot:
    """Fresh local facts supplied by the authorized vault/membership adapter.

    `allowed_methods=None` means provider grants have not been established;
    unknown is not an allow. Scopes come from grant metadata, never OAuth
    requested scopes. Do not put tokens, refresh metadata, exception text or
    free-form provider payloads into this structure.
    """

    tenant_id: int
    mode: str
    credential_id: int | None = None
    owner_user_id: int | None = None
    active: bool = True
    expires_at: datetime | None = None
    granted_scopes: frozenset[str] = frozenset()
    allowed_methods: frozenset[str] | None = None
    # No default live-verified claim: method evidence is external to this policy.
    live_verified_methods: frozenset[str] = frozenset()


@dataclass(frozen=True)
class VKAccessDecision:
    allowed: bool
    reason: str
    methods: tuple[str, ...]
    mode: str
    # Only registry method keys may be serialized, never caller/provider strings.
    live_unverified_methods: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "methods": list(self.methods),
            "mode": self.mode,
            "live_unverified_methods": list(self.live_unverified_methods),
        }


def decide_vk_access(
    selection: VKSelection,
    snapshot: VKAccessSnapshot | None,
    *,
    tenant_id: int,
    active_member_ids: frozenset[int],
    required_methods: frozenset[str],
    required_scopes: frozenset[str] = frozenset(),
    now: datetime | None = None,
) -> VKAccessDecision:
    """Fail closed without selecting another credential or renewing a token.

    The calling subsystem owns its scope requirements; this policy does not
    infer VK permissions from a login, product tariff, token type or a token's
    mere presence. Expired renewable tokens must be renewed separately and
    followed by a fresh authorized snapshot, never accepted stale here.
    """
    methods = tuple(sorted(method for method in required_methods if method in METHOD_MODES))
    mode = selection.mode

    def deny(reason: str) -> VKAccessDecision:
        return VKAccessDecision(False, reason, methods, mode)

    if not _positive_id(tenant_id):
        return deny("workspace_required")
    if not required_methods or any(method not in METHOD_MODES for method in required_methods):
        return deny("method_unsupported")
    if snapshot is None:
        return deny("connection_missing")
    if snapshot.tenant_id != tenant_id or type(snapshot.tenant_id) is not int:
        return deny("workspace_mismatch")
    if snapshot.mode != selection.mode or snapshot.credential_id != selection.credential_id:
        return deny("connection_selection_mismatch")
    if snapshot.active is not True:
        return deny("connection_revoked")
    if mode == "user" and (
        not _positive_id(snapshot.owner_user_id)
        or snapshot.owner_user_id not in {value for value in active_member_ids if _positive_id(value)}
    ):
        return deny("connection_owner_not_member")
    if mode == "api" and snapshot.owner_user_id is not None:
        return deny("connection_selection_mismatch")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if snapshot.expires_at is not None:
        expiry = snapshot.expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry <= current:
            return deny("connection_expired")
    if any(mode not in METHOD_MODES[method] for method in methods):
        return deny("method_mode_unsupported")
    if snapshot.allowed_methods is None:
        return deny("method_grants_unknown")
    if not required_methods.issubset(snapshot.allowed_methods):
        return deny("method_not_granted")
    if not required_scopes.issubset(snapshot.granted_scopes):
        return deny("scope_missing")
    return VKAccessDecision(
        True,
        "local_policy_allowed",
        methods,
        mode,
        tuple(method for method in methods if method not in snapshot.live_verified_methods),
    )
