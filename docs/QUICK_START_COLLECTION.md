# Quick Start - Запуск сбора контента

**Быстрое руководство для запуска системы сбора и анализа**

---

## 🚀 Самый быстрый способ (API)

### 1. Запустить приложение

```bash
uvicorn app.main:app --reload
```

### 2. Собрать с одного источника

```bash
curl -X POST "http://localhost:8000/api/v1/collect/source" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "source_id": 1,
    "analyze": true
  }'
```

**Ответ:**
```json
{
  "status": "started",
  "source_id": 1,
  "message": "Content collection started in background"
}
```

### 3. Проверить результат

```bash
curl "http://localhost:8000/api/v1/analytics/source/1" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

---

## ⚡ Python скрипт (разовый запуск)

### Создать файл `collect.py`:

```python
#!/usr/bin/env python3
import asyncio
from app.services.monitoring.collector import ContentCollector
from app.models import Source

async def main():
    collector = ContentCollector()
    
    # Получить все активные источники
    sources = await Source.objects.filter(is_active=True)
    print(f"Найдено {len(sources)} источников")
    
    for source in sources:
        print(f"\n🚀 Сбор: {source.name}")
        result = await collector.collect_from_source(source, analyze=True)
        
        if result:
            print(f"✅ Собрано: {result['content_count']} элементов")
        else:
            print(f"❌ Ошибка")

if __name__ == "__main__":
    asyncio.run(main())
```

### Запустить:

```bash
python collect.py
```

---

## 🔄 Автоматический сбор (Celery)

### 1. Установить Redis

```bash
# macOS
brew install redis
brew services start redis

# Ubuntu
sudo apt install redis-server
sudo systemctl start redis
```

### 2. Настроить Celery

**Создать `app/celery/config.py`:**
```python
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
```

### 3. Запустить Celery

```bash
# Терминал 1: Worker
celery -A app.celery.config worker --loglevel=info

# Терминал 2: Beat (планировщик)
celery -A app.celery.config beat --loglevel=info
```

**Или всё вместе:**
```bash
celery -A app.celery.config worker --beat --loglevel=info
```

---

## 📊 Поля периодичности

### BotScenario

```python
# Интервал между запусками (в минутах)
cooldown_minutes = 30  # По умолчанию 30 минут

# Примеры:
cooldown_minutes = 5      # Каждые 5 минут (частый мониторинг)
cooldown_minutes = 60     # Каждый час
cooldown_minutes = 1440   # Раз в сутки
```

### Source

```python
# Время последней проверки (обновляется автоматически)
last_checked = DateTime

# Логика:
# Источник готов к сбору если:
# 1. last_checked == None (никогда не проверяли)
#    ИЛИ
# 2. (сейчас - last_checked) >= cooldown_minutes
```

---

## 💡 Примеры сценариев

### Частый мониторинг новостей (5 минут)

```python
from app.models import BotScenario

scenario = await BotScenario.objects.create(
    name="Новостной мониторинг",
    cooldown_minutes=5,
    analysis_types=["keywords", "topics"],
    action_type="NOTIFICATION",
    is_active=True
)
```

### Стандартный анализ (30 минут)

```python
scenario = await BotScenario.objects.create(
    name="Стандартный мониторинг",
    cooldown_minutes=30,
    analysis_types=["sentiment", "keywords"],
    is_active=True
)
```

### Ежедневная сводка (24 часа)

```python
scenario = await BotScenario.objects.create(
    name="Дневная сводка",
    cooldown_minutes=1440,  # 24 * 60
    analysis_types=["sentiment", "topics", "engagement"],
    action_type="NOTIFICATION",
    is_active=True
)
```

---

## 📋 Быстрый чеклист

### Подготовка:
1. ✅ Создать Platform в админке
2. ✅ Добавить Source (группа/канал)
3. ✅ Убедиться что `is_active = True`

### Разовый запуск:
```bash
# Через API
curl -X POST http://localhost:8000/api/v1/collect/source \
  -H "Authorization: Bearer TOKEN" \
  -d '{"source_id": 1, "analyze": true}'

# Или через Python
python collect.py
```

### Автоматический сбор:
```bash
# 1. Создать сценарий с cooldown_minutes
# 2. Назначить сценарий источнику
# 3. Запустить Celery
celery -A app.celery.config worker --beat
```

---

## 🔍 Проверка результатов

### В админке:
```
http://localhost:8000/admin/aianalytics/list
```

### Через API:
```bash
curl "http://localhost:8000/api/v1/analytics/source/1" \
  -H "Authorization: Bearer TOKEN"
```

### Через Python:
```python
from app.models import AIAnalytics

# Последние анализы
analytics = await AIAnalytics.objects.filter().order_by(
    AIAnalytics.created_at.desc()
).limit(10)

for a in analytics:
    print(f"#{a.id}: {a.source_id} - {a.analysis_date}")
```

---

## 🆘 Troubleshooting

### Ошибка "Source not found"
```python
# Проверить источники
sources = await Source.objects.filter(is_active=True)
print(f"Активных источников: {len(sources)}")
```

### Нет контента
```python
# Проверить last_checked
source = await Source.objects.get(id=1)
print(f"Последняя проверка: {source.last_checked}")
```

### Анализ не запускается
```python
# Проверить сценарий
if source.bot_scenario_id:
    scenario = await source.bot_scenario
    print(f"Сценарий: {scenario.name}")
    print(f"Cooldown: {scenario.cooldown_minutes} мин")
    print(f"Активен: {scenario.is_active}")
```

---

## 📚 Полная документация

См. **CONTENT_COLLECTION_GUIDE.md** для детальной информации:
- Все способы запуска
- Настройка Celery
- Примеры скриптов
- Мониторинг и отладка
- Сценарии использования

---

**Всё! Система готова к работе.** 🎉

**Следующие шаги:**
1. Создать источники в админке
2. Запустить сбор (API или скрипт)
3. Проверить результаты в AIAnalytics
4. Настроить автоматический сбор (опционально)
