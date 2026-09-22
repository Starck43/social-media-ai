"""Unified runtime entrypoint: scheduler + worker + channels (M2) in one process.

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
    from app.jobs.dispatcher import worker_forever
    from app.scheduler.bootstrap import ensure_default_schedules
    from app.scheduler.runner import run_forever

    await ensure_default_schedules()

    if not settings.SCHEDULER_ENABLED:
        logger.info("Scheduler disabled via SCHEDULER_ENABLED=false")
        await worker_forever()
        return

    await asyncio.gather(
        run_forever(),
        worker_forever(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
