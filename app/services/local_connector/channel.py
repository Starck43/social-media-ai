"""One bounded API-process connection registry; no DB lock during network I/O."""

import asyncio
from dataclasses import dataclass, field

from pydantic import ValidationError

from .wire import MAX_CONTROL_BYTES, TIMEOUT, ConnectorUnavailable, SearchResponse, decode


@dataclass
class Session:
    socket: object
    epoch: int
    pending: dict = field(default_factory=dict)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ConnectorChannels:
    def __init__(self, *, maximum=128, per_device=2):
        self.maximum = maximum
        self.per_device = per_device
        self.sessions = {}
        # Search responses and device-policy transitions share this process
        # fence. It is intentionally separate from DB transactions: response
        # bytes may be sent while holding it, but never while holding row locks.
        self.release_fence = asyncio.Lock()

    def attach(self, device_id, socket, epoch):
        if len(self.sessions) >= self.maximum or device_id in self.sessions:
            raise ConnectorUnavailable()
        session = Session(socket, epoch)
        self.sessions[device_id] = session
        return session

    async def detach(self, device_id, session):
        if self.sessions.get(device_id) is session:
            self.sessions.pop(device_id, None)
        for future in session.pending.values():
            if not future.done():
                future.set_exception(ConnectorUnavailable())
        session.pending.clear()

    async def revoke(self, device_id):
        session = self.sessions.get(device_id)
        if session:
            await self.detach(device_id, session)
            await session.socket.close(code=1008)

    async def search(self, device_id, request, payload, digest):
        session = self.sessions.get(device_id)
        if session is None or session.epoch != request.device_epoch or len(session.pending) >= self.per_device:
            raise ConnectorUnavailable()
        if request.request_id in session.pending:
            raise ConnectorUnavailable()
        future = asyncio.get_running_loop().create_future()
        session.pending[request.request_id] = future
        try:
            async with asyncio.timeout(TIMEOUT):
                async with session.send_lock:
                    await session.socket.send_bytes(payload)
                response = await future
                return response.checked(request, digest)
        except Exception:
            raise ConnectorUnavailable() from None
        finally:
            session.pending.pop(request.request_id, None)

    def receive(self, device_id, session, payload):
        if self.sessions.get(device_id) is not session or len(payload) > MAX_CONTROL_BYTES:
            raise ConnectorUnavailable()
        decoded = decode(payload)
        try:
            response = SearchResponse.model_validate(decoded)
        except ValidationError:
            raise ConnectorUnavailable() from None
        future = session.pending.get(response.request_id)
        if future is None or future.done():
            raise ConnectorUnavailable()
        future.set_result(response)
