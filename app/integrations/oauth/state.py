"""Single-use OAuth 2.0 state and PKCE (S256) challenge store (P13-T08).

Every authorization request creates a cryptographically random `state` token
bound to:
- `tenant_id`
- `actor_user_id`
- `provider_id`
- `connection_id`
- `code_verifier` (when PKCE is enabled)
- `expires_at` (short TTL, default 10 minutes)

Consuming a state token is atomic and single-use: replayed, expired, cross-tenant,
cross-actor, or cross-connection callbacks are rejected immediately.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

DEFAULT_STATE_TTL_SECONDS = 600


class OAuthStateError(RuntimeError):
    """Value-safe error for invalid, expired, replayed, or mismatched OAuth state."""

    def __init__(self, code: str, message: str = "Invalid or expired OAuth state") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class OAuthPendingState:
    state_token: str
    tenant_id: int
    actor_user_id: int
    provider_id: str
    connection_id: int
    code_verifier: str | None
    code_challenge: str | None
    redirect_uri: str
    created_at: datetime
    expires_at: datetime


def generate_pkce_pair() -> tuple[str, str]:
    """Generate an RFC 7636 PKCE `code_verifier` and `S256` `code_challenge`."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


class OAuthStateStore:
    """Bounded in-memory single-use state store with deterministic clock injection for tests."""

    def __init__(self, *, max_entries: int = 4096) -> None:
        self._records: dict[str, OAuthPendingState] = {}
        self._max_entries = max_entries

    def _prune(self, now: datetime) -> None:
        expired = [k for k, v in self._records.items() if now >= v.expires_at]
        for k in expired:
            self._records.pop(k, None)
        while len(self._records) >= self._max_entries:
            oldest_key = next(iter(self._records))
            self._records.pop(oldest_key, None)

    def issue(
        self,
        *,
        tenant_id: int,
        actor_user_id: int,
        provider_id: str,
        connection_id: int,
        redirect_uri: str,
        use_pkce: bool = True,
        ttl_seconds: int = DEFAULT_STATE_TTL_SECONDS,
        now: datetime | None = None,
    ) -> OAuthPendingState:
        if type(tenant_id) is not int or tenant_id <= 0 or type(actor_user_id) is not int or actor_user_id <= 0:
            raise OAuthStateError("invalid_state_context")
        if type(connection_id) is not int or connection_id <= 0 or not provider_id:
            raise OAuthStateError("invalid_state_context")

        current_time = now or datetime.now(timezone.utc)
        self._prune(current_time)

        token = secrets.token_urlsafe(32)
        verifier, challenge = generate_pkce_pair() if use_pkce else (None, None)
        pending = OAuthPendingState(
            state_token=token,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            provider_id=provider_id,
            connection_id=connection_id,
            code_verifier=verifier,
            code_challenge=challenge,
            redirect_uri=redirect_uri,
            created_at=current_time,
            expires_at=current_time + timedelta(seconds=ttl_seconds),
        )
        self._records[token] = pending
        return pending

    def consume(
        self,
        state_token: str,
        *,
        tenant_id: int,
        actor_user_id: int,
        provider_id: str,
        expected_connection_id: int | None = None,
        now: datetime | None = None,
    ) -> OAuthPendingState:
        """Atomically pop and verify a single-use state token."""
        if not isinstance(state_token, str) or not state_token.strip():
            raise OAuthStateError("state_missing")

        current_time = now or datetime.now(timezone.utc)
        pending = self._records.pop(state_token.strip(), None)
        if pending is None:
            raise OAuthStateError("state_invalid_or_replayed")

        if current_time >= pending.expires_at:
            raise OAuthStateError("state_expired")

        if pending.tenant_id != tenant_id:
            raise OAuthStateError("state_cross_tenant")

        if pending.actor_user_id != actor_user_id:
            raise OAuthStateError("state_actor_mismatch")

        if pending.provider_id != provider_id:
            raise OAuthStateError("state_provider_mismatch")

        if expected_connection_id is not None and pending.connection_id != expected_connection_id:
            raise OAuthStateError("state_connection_mismatch")

        return pending

    def clear(self) -> None:
        self._records.clear()


oauth_state_store = OAuthStateStore()
