"""Channel abstraction: outbound messaging + inbound polling for messengers."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol

logger = logging.getLogger(__name__)


@dataclass
class Inbound:
    """Normalized incoming message from any channel."""

    channel: str  # 'telegram' | 'max'
    chat_id: str
    user_id: str
    text: str
    is_channel_post: bool = False  # message posted to a channel (not a DM)
    raw: dict[str, Any] = field(default_factory=dict)


class Channel(Protocol):
    """Messaging channel protocol."""

    name: str

    async def send(self, chat_id: str, text: str, parse_mode: str | None = None) -> dict[str, Any]:
        """Send a text message; returns provider response info (message_id etc.)."""
        ...

    def poll(self) -> AsyncIterator[Inbound]:
        """Long-poll inbound updates."""
        ...
