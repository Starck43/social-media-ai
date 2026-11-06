"""Outbound-only session boundary for the accepted local connector protocol."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from collections.abc import Callable, Mapping

ALLOWED_OPERATIONS = frozenset({"health", "index.status", "search", "file.metadata", "file.fetch", "index.delete"})
MAX_ENVELOPE_BYTES = 64 * 1024


class ProtocolError(ValueError):
    """Raised before an invalid outbound envelope is sent."""


class OutboundSessionClient:
    """Create signed-envelope-shaped requests through an injected outbound transport.

    The class deliberately exposes no listener and accepts no server path. TLS,
    mTLS and signature implementations are injected by the packaged connector.
    """

    def __init__(
        self,
        transport: Callable[[dict], Mapping],
        *,
        device_id: str,
        tenant_id: str,
        owner_id: str,
        actor_id: str,
        signer: Callable[[bytes], str] | None = None,
    ) -> None:
        self.transport = transport
        self.device_id = device_id
        self.tenant_id = tenant_id
        self.owner_id = owner_id
        self.actor_id = actor_id
        self.signer = signer

    def request(self, operation: str, args: Mapping[str, object] | None = None) -> Mapping:
        if operation not in ALLOWED_OPERATIONS:
            raise ProtocolError("operation is not allowed")
        payload = dict(args or {})
        if _contains_raw_path(payload):
            raise ProtocolError("raw paths are not accepted by the session boundary")
        if operation == "search" and len(str(payload.get("query", "")).encode()) > 4096:
            raise ProtocolError("query exceeds protocol limit")
        issued_at = int(time.time())
        envelope = {
            "protocol_version": 1,
            "device_id": self.device_id,
            "tenant_id": self.tenant_id,
            "owner_id": self.owner_id,
            "actor_id": self.actor_id,
            "operation": operation,
            "args": payload,
            "request_id": secrets.token_hex(16),
            "issued_at": issued_at,
            "expires_at": issued_at + 30,
        }
        canonical = json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        envelope["request_digest"] = hashlib.sha256(canonical).hexdigest()
        if self.signer is not None:
            envelope["signature"] = self.signer(canonical)
        if len(json.dumps(envelope, separators=(",", ":")).encode()) > MAX_ENVELOPE_BYTES:
            raise ProtocolError("envelope exceeds protocol limit")
        response = self.transport(envelope)
        if not isinstance(response, Mapping):
            raise ProtocolError("invalid response")
        if response.get("request_id") != envelope["request_id"]:
            raise ProtocolError("response request mismatch")
        if response.get("request_digest") != envelope["request_digest"]:
            raise ProtocolError("response digest mismatch")
        return response


def _contains_raw_path(value: object) -> bool:
    if isinstance(value, Mapping):
        for child_key, child_value in value.items():
            if str(child_key).casefold() in {"path", "absolute_path", "local_path", "filename"}:
                return True
            if _contains_raw_path(child_value):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_raw_path(item) for item in value)
    return False
