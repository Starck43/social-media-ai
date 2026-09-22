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
