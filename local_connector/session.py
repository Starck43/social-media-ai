"""Fail-closed placeholder for the not-yet-implemented device session.

This scaffold has no cloud transport. The accepted device-side protocol,
authentication, replay protection and operation dispatch are owned by later P18
work and must not be approximated by sending unsigned or incompatible messages.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping


class ProtocolError(RuntimeError):
    """A session operation cannot be performed by this local-only scaffold."""


class OutboundSessionClient:
    """Compatibility seam that rejects every request without network activity."""

    def __init__(
        self,
        transport: Callable[[dict], Mapping] | None = None,
        *,
        device_id: str = "",
        tenant_id: str = "",
        owner_id: str = "",
        actor_id: str = "",
        signer: Callable[[bytes], str] | None = None,
    ) -> None:
        # Preserve constructor compatibility while intentionally retaining no
        # transport or signer reference in this local-only scaffold.
        pass

    def request(self, operation: str, args: Mapping[str, object] | None = None) -> Mapping:
        raise ProtocolError("device sessions are disabled in the local-only scaffold")
