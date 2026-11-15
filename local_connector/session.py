"""Device-side signed command session wiring and fail-closed outbound client seam.

Cloud activation remains default-off until Q-19 gates are satisfied. Outbound
client-initiated requests stay disabled; inbound server-signed command envelopes
are verified, bounded, replay-checked and dispatched via SignedCommandReceiver.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from .search.models import SearchProtocolError
from .search.protocol import SignedCommandReceiver


class ProtocolError(RuntimeError):
    """Raised when a device session operation is disabled or violates protocol rules."""

    def __init__(self, message: str, *, code: str = "denied") -> None:
        self.code = code
        super().__init__(message)


class OutboundSessionClient:
    """Compatibility seam that rejects client-initiated requests without network activity."""

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
        # Preserve constructor compatibility while retaining no outbound transport
        # or signer reference.
        pass

    def request(self, operation: str, args: Mapping[str, object] | None = None) -> Mapping:
        raise ProtocolError("device sessions are disabled in the local-only scaffold", code="unavailable")


class DeviceCommandSession:
    """Bounded, default-off device session executing server-signed control envelopes."""

    def __init__(
        self,
        receiver: SignedCommandReceiver,
        *,
        enabled: bool = False,
    ) -> None:
        if not isinstance(receiver, SignedCommandReceiver):
            raise TypeError("receiver must be a SignedCommandReceiver")
        if type(enabled) is not bool:
            raise ValueError("enabled must be a bool")
        self.receiver = receiver
        self.enabled = enabled
        self._closed = False

    def handle_frame(self, payload: bytes, *, now: int | None = None) -> bytes:
        """Process one incoming control frame and return canonical response bytes."""

        if not self.enabled:
            raise ProtocolError("device cloud session is disabled before Q-19 activation", code="unavailable")
        if self._closed or self.receiver.revoked:
            raise ProtocolError("device command session is closed or revoked", code="denied")
        try:
            return self.receiver.handle_payload(payload, now=now)
        except SearchProtocolError as exc:
            raise ProtocolError(exc.code, code=exc.code) from exc

    def receive_and_reply(
        self,
        payload: bytes,
        *,
        send: Callable[[bytes], object],
        now: int | None = None,
    ) -> bytes:
        """Verify, execute and send one canonical response frame over an active transport."""

        response = self.handle_frame(payload, now=now)
        send(response)
        return response

    def revoke_and_close(self, *, device_epoch: int | None = None) -> None:
        self._closed = True
        self.receiver.revoke(device_epoch=device_epoch)

    def close(self) -> None:
        self._closed = True
