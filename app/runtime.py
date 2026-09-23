"""Unified runtime entrypoint: scheduler + worker + agent chat listener.

Run: python -m app.runtime
"""

import asyncio
import logging

from app.core.config import settings

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    from app.channels.listener import listen_forever
    from app.jobs.dispatcher import worker_forever
    from app.scheduler.bootstrap import ensure_all_default_schedules
    from app.scheduler.runner import run_forever

    # Default schedules are tenant-owned: seed every active workspace once.
    await ensure_all_default_schedules()

    tasks = [listen_forever()]
    if not settings.SCHEDULER_ENABLED:
        logger.info("Scheduler disabled via SCHEDULER_ENABLED=false")
    else:
        tasks.append(run_forever())
    tasks.append(worker_forever())
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
