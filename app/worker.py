"""Worker entrypoint: process background jobs only (no scheduler).

Run: python -m app.worker
"""

import asyncio
import logging

from app.core.config import settings

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

if __name__ == "__main__":
    from app.jobs.dispatcher import worker_forever

    try:
        asyncio.run(worker_forever())
    except KeyboardInterrupt:
        pass
