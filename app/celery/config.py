"""
FROZEN — superseded by `app/scheduler` + `app/jobs` (DB-backed, no broker).

Kept only for historical reference, and no entrypoint starts Celery. The only
remaining importer is `app/api/v1/endpoints/ai.py` (the optional FastAPI admin
API); the runtime does not touch this module. It still imports cleanly with
Redis unconfigured (`REDIS_URL` has a localhost default) but cannot run
without a broker.
Do not extend: schedule work via `app/models/schedule.py` instead.
"""
from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

app = Celery(
    'social_media_ai',
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

# Каждые 30 минут
app.conf.beat_schedule = {
    'collect-all': {
        'task': 'app.celery.tasks.collect_all_sources',
        'schedule': crontab(minute='*/60'),
    },
}
