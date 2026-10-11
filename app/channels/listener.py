"""Channel listener: poll enabled channels, route inbound to the agent, reply.

Wiring: app.runtime runs task runner + worker AND this listener in one process,
so one VPS service covers cron jobs, background work and the agent chat.

Design notes:
- One `poll()` iterator per enabled channel; failures are per-channel and the
  loop keeps running (a dead Telegram token must not stop MAX).
- Every inbound goes through `_ingest_safely` first: Telegram hands channel posts
  to the bot as updates, so this is where Bot API collection happens (see
  `app/services/monitoring/ingest.py`). It is synchronous on purpose — the
  `getUpdates` offset advances only after the iteration completes.
- `handle_inbound` returns the reply text (or None for strangers/empty text);
  this module sends it back to the same chat.
- The agent ignores channel posts from non-owners (`is_channel_post` + owner
  allowlist), so the bot never replies to its own digest channel posts.
- Telegram `getUpdates` offset lives in memory (`channel._offset`) — Telegram
  replays unacked updates on restart, and unseen ones are simply handled (the
  per-source watermark in ingest keeps that from double-charging the LLM).
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import aclosing

from app.channels.base import Inbound
from app.channels.registry import enabled_channels

logger = logging.getLogger(__name__)


async def _consume_channel(channel) -> None:
    """Poll one channel forever, routing each message through the agent."""
    name = getattr(channel, "name", type(channel).__name__)
    logger.info(f"Agent listener started for channel {name!r}")
    while True:
        try:
            async with aclosing(channel.poll()) as updates:
                async for inbound in updates:
                    # Ingest first and synchronously: the `getUpdates` offset is only
                    # confirmed once this iteration completes, which gives push-based
                    # collection at-least-once delivery (the source watermark then
                    # suppresses duplicate admission).
                    await _ingest_safely(inbound)
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


async def _ingest_safely(inbound: Inbound) -> None:
    """Fail closed: uncertain admission aborts polling before transport ACK."""
    from app.services.monitoring.ingest import ingest_channel_post

    try:
        await ingest_channel_post(inbound)
    except Exception:  # noqa: BLE001
        logger.error("ingest_admission_failed error_code=admission_unconfirmed")
        raise RuntimeError("ingest_admission_unconfirmed") from None


async def listen_forever() -> None:
    """Poll every enabled channel concurrently; returns only on cancel."""
    channels = enabled_channels()
    if not channels:
        logger.info("No messenger channels configured — agent listener idle")
        return
    await asyncio.gather(*(_consume_channel(ch) for ch in channels))
