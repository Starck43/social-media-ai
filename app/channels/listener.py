"""Channel listener: poll enabled channels, route inbound to the agent, reply.

Wiring: app.runtime runs scheduler + worker AND this listener in one process,
so one VPS service covers cron jobs, background work and the agent chat.

Design notes:
- One `poll()` iterator per enabled channel; failures are per-channel and the
  loop keeps running (a dead Telegram token must not stop MAX).
- `handle_inbound` returns the reply text (or None for strangers/empty text);
  this module sends it back to the same chat.
- The agent ignores channel posts from non-owners (`is_channel_post` + owner
  allowlist), so the bot never replies to its own digest channel posts.
- Telegram `getUpdates` offset lives in memory (`channel._offset`) — Telegram
  replays unacked updates on restart, and unseen ones are simply handled.
"""

from __future__ import annotations

import asyncio
import logging

from app.channels.base import Inbound
from app.channels.registry import enabled_channels

logger = logging.getLogger(__name__)


async def _consume_channel(channel) -> None:
    """Poll one channel forever, routing each message through the agent."""
    from app.agent.runtime import handle_inbound

    name = getattr(channel, "name", type(channel).__name__)
    logger.info(f"Agent listener started for channel {name!r}")
    while True:
        try:
            async for inbound in channel.poll():
                reply = await _handle_safely(inbound)
                if reply:
                    try:
                        await channel.send(inbound.chat_id, reply, parse_mode="HTML")
                    except Exception as e:  # noqa: BLE001
                        logger.error(f"[{name}] reply to {inbound.chat_id} failed: {e}")
        except asyncio.CancelledError:
            logger.info(f"Agent listener stopped for channel {name!r}")
            return
        except Exception as e:  # noqa: BLE001
            # Never let one channel kill the listener: back off and re-poll.
            logger.error(f"[{name}] poll loop error: {e}", exc_info=True)
            await asyncio.sleep(5)


async def _handle_safely(inbound: Inbound) -> str | None:
    """Run the agent on one inbound message; errors become a short reply."""
    from app.agent.runtime import handle_inbound

    try:
        return await handle_inbound(inbound)
    except Exception as e:  # noqa: BLE001
        logger.error(f"Agent failed for {inbound.channel}:{inbound.chat_id}: {e}", exc_info=True)
        return "Внутренняя ошибка агента. Попробуйте позже."


async def listen_forever() -> None:
    """Poll every enabled channel concurrently; returns only on cancel."""
    channels = enabled_channels()
    if not channels:
        logger.info("No messenger channels configured — agent listener idle")
        return
    await asyncio.gather(*(_consume_channel(ch) for ch in channels))
