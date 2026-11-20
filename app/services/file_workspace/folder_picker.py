"""Short-lived, browser-bound credentials for personal folder setup."""

import asyncio
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone


class PickerExpired(Exception):
    """The setup handle cannot be used in this session."""


@dataclass(frozen=True)
class PendingFolder:
    binding: str
    provider: str
    credential: str = field(repr=False)
    expires_at: datetime


_pending: dict[str, tuple[PendingFolder, asyncio.TimerHandle]] = {}


def discard(handle: str) -> None:
    value = _pending.pop(handle, None)
    if value:
        value[1].cancel()


def issue(pending: PendingFolder) -> str:
    delay = (pending.expires_at - datetime.now(timezone.utc)).total_seconds()
    if delay <= 0:
        raise PickerExpired
    while len(_pending) >= 4096:
        discard(next(iter(_pending)))
    handle = secrets.token_urlsafe(32)
    timer = asyncio.get_running_loop().call_later(delay, discard, handle)
    _pending[handle] = pending, timer
    return handle


def get(handle: str, binding: str, *, consume: bool = False) -> PendingFolder:
    value = _pending.get(handle)
    if not value or value[0].binding != binding:
        raise PickerExpired
    pending = value[0]
    if pending.expires_at <= datetime.now(timezone.utc):
        discard(handle)
        raise PickerExpired
    if consume:
        discard(handle)
    return pending
