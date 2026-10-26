"""Unified runtime entrypoint: task runner + worker + agent chat listener.

Run: python -m app.runtime
"""

import asyncio
import logging

from app.core.config import settings
from app.core.runtime_presence import runtime_owner

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    from app.channels.listener import listen_forever
    from app.jobs.dispatcher import worker_forever
    from app.tasks.bootstrap import ensure_all_default_tasks
    from app.tasks.runner import run_forever

    # Acquire the opt-in local owner lock BEFORE bootstrap can write or start
    # components. A second configured owner must stop without side effects.
    with runtime_owner(settings.RUNTIME_STATE_DIR, scheduler_enabled=settings.SCHEDULER_ENABLED) as presence:
        # Default tasks are tenant-owned: seed every active workspace once.
        await ensure_all_default_tasks()

        components = [asyncio.create_task(listen_forever())]
        if not settings.SCHEDULER_ENABLED:
            logger.info("Scheduler disabled via SCHEDULER_ENABLED=false")
        else:
            components.append(asyncio.create_task(run_forever()))
        components.append(asyncio.create_task(worker_forever()))
        group = asyncio.gather(*components)
        heartbeat = None
        try:
            if presence is not None:
                presence.write("running")
                heartbeat = asyncio.create_task(presence.heartbeat())
                # Observe heartbeat failure without keeping a completed runtime
                # alive just to publish heartbeats. Component progress is not
                # asserted by this process-level signal.
                done, _ = await asyncio.wait((group, heartbeat), return_when=asyncio.FIRST_COMPLETED)
                for completed in done:
                    completed.result()
            else:
                await group
        finally:
            group.cancel()
            for component in components:
                component.cancel()
            if heartbeat is not None:
                heartbeat.cancel()
            await asyncio.gather(
                *components, group, *([heartbeat] if heartbeat is not None else []), return_exceptions=True
            )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
