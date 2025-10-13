from celery import Celery
from celery.schedules import crontab

app = Celery(
    'social_media_ai',
    broker='redis://localhost:6379/0',
    backend='redis://localhost:6379/0'
)

# Каждые 30 минут
app.conf.beat_schedule = {
    'collect-all': {
        'task': 'app.celery.tasks.collect_all_sources',
        'schedule': crontab(minute='*/30'),
    },
}
