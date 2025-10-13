# Руководство по сбору и анализу контента

**Дата:** 2024-12-10

---

## 🎯 Обзор системы

Система сбора и анализа контента из социальных сетей состоит из нескольких компонентов:

1. **ContentCollector** - основной сервис сбора
2. **AIAnalyzer** - сервис анализа с помощью ИИ
3. **BotScenario** - сценарии для автоматизации
4. **Celery Tasks** - фоновые задачи по расписанию
5. **API Endpoints** - ручной запуск через API

---

## 📊 Поля моделей отвечающие за периодичность

### 1. BotScenario (app/models/bot_scenario.py)

```python
class BotScenario:
    # Интервал между запусками сценария (в минутах)
    cooldown_minutes: int = Column(Integer, default=30)
    
    # Активен ли сценарий
    is_active: bool = Column(Boolean, default=True)
```

**Назначение:**
- `cooldown_minutes` - как часто запускать сбор/анализ для источников с этим сценарием
- По умолчанию: 30 минут
- Диапазон: от 1 минуты до нескольких дней (10080 минут = неделя)

**Примеры:**
```python
# Частый мониторинг (каждые 5 минут)
cooldown_minutes = 5

# Стандартный (каждые 30 минут)
cooldown_minutes = 30

# Ежечасный
cooldown_minutes = 60

# Ежедневный
cooldown_minutes = 1440  # 24 * 60

# Еженедельный
cooldown_minutes = 10080  # 24 * 60 * 7
```

---

### 2. Source (app/models/source.py)

```python
class Source:
    # Время последней проверки источника
    last_checked: DateTime = Column(DateTime, nullable=True)
    
    # Активен ли источник
    is_active: bool = Column(Boolean, default=True)
    
    # ID назначенного сценария
    bot_scenario_id: int = Column(Integer, ForeignKey('bot_scenarios.id'))
```

**Назначение:**
- `last_checked` - когда последний раз собирали контент из этого источника
- Используется для определения нужно ли запускать сбор (проверяем cooldown)
- Обновляется автоматически после каждого успешного сбора

**Логика проверки:**
```python
# Источник готов к сбору если:
1. last_checked == None (никогда не проверяли)
   ИЛИ
2. (now - last_checked) > scenario.cooldown_minutes
```

---

## 🚀 Способы запуска сбора контента

### Способ 1: Через API (ручной запуск)

#### A. Собрать с одного источника

**Endpoint:** `POST /api/v1/collect/source`

**Request:**
```json
{
  "source_id": 1,
  "content_type": "posts",
  "analyze": true
}
```

**Пример с curl:**
```bash
curl -X POST "http://localhost:8000/api/v1/collect/source" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "source_id": 1,
    "content_type": "posts",
    "analyze": true
  }'
```

**Пример с Python:**
```python
import httpx

async with httpx.AsyncClient() as client:
    response = await client.post(
        "http://localhost:8000/api/v1/collect/source",
        headers={"Authorization": "Bearer YOUR_TOKEN"},
        json={
            "source_id": 1,
            "content_type": "posts",
            "analyze": True
        }
    )
    print(response.json())
    # {"status": "started", "source_id": 1, "message": "..."}
```

---

#### B. Собрать со всех источников платформы

**Endpoint:** `POST /api/v1/collect/platform`

**Request:**
```json
{
  "platform_id": 1,
  "source_types": ["USER", "GROUP"],
  "analyze": true
}
```

**Пример:**
```bash
curl -X POST "http://localhost:8000/api/v1/collect/platform" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "platform_id": 1,
    "source_types": ["USER", "GROUP"],
    "analyze": true
  }'
```

**Что произойдёт:**
1. Найдёт все активные источники на платформе
2. Опционально отфильтрует по типам (USER, GROUP, CHANNEL)
3. Соберёт контент с каждого источника
4. Запустит анализ если `analyze=true`
5. Обновит `last_checked` для каждого источника

---

#### C. Собрать с отслеживаемых пользователей

**Endpoint:** `POST /api/v1/collect/monitored`

**Request:**
```json
{
  "source_id": 5,
  "analyze": true
}
```

**Назначение:**
- Для источников типа GROUP/CHANNEL, которые отслеживают конкретных USER'ов
- Соберёт контент со всех связанных пользователей

---

### Способ 2: Через Celery Tasks (автоматический по расписанию)

#### Настройка Celery Beat

**Файл:** `app/celery/tasks.py`

```python
from celery import Celery
from celery.schedules import crontab

app = Celery('social_media_ai')

# Настройка периодических задач
app.conf.beat_schedule = {
    'collect-active-sources': {
        'task': 'app.celery.tasks.collect_all_active_sources',
        'schedule': crontab(minute='*/30'),  # Каждые 30 минут
    },
    'collect-priority-sources': {
        'task': 'app.celery.tasks.collect_priority_sources',
        'schedule': crontab(minute='*/5'),  # Каждые 5 минут
    },
}
```

#### Celery Tasks

**Задача 1: Сбор со всех активных источников**
```python
@app.task
async def collect_all_active_sources():
    """Collect content from all active sources respecting cooldown."""
    from app.services.monitoring.collector import ContentCollector
    from app.models import Source
    from datetime import datetime, timedelta
    
    collector = ContentCollector()
    
    # Получить все активные источники
    sources = await Source.objects.filter(is_active=True)
    
    results = {'total': 0, 'collected': 0, 'skipped': 0}
    
    for source in sources:
        # Проверить cooldown
        if source.bot_scenario_id:
            scenario = await source.bot_scenario
            cooldown = timedelta(minutes=scenario.cooldown_minutes)
            
            # Пропустить если ещё в cooldown
            if source.last_checked and (datetime.utcnow() - source.last_checked) < cooldown:
                results['skipped'] += 1
                continue
        
        # Собрать контент
        result = await collector.collect_from_source(source, analyze=True)
        if result:
            results['collected'] += 1
        
        results['total'] += 1
    
    return results
```

**Задача 2: Сбор с приоритетных источников**
```python
@app.task
async def collect_priority_sources():
    """Collect from sources with high-priority scenarios (short cooldown)."""
    from app.models import Source
    
    # Найти источники с cooldown <= 10 минут
    sources = await Source.objects.filter(
        is_active=True,
        bot_scenario__is_active=True,
        bot_scenario__cooldown_minutes__lte=10
    )
    
    # ... логика сбора
```

---

#### Запуск Celery Worker и Beat

**1. Запустить Worker (обработка задач):**
```bash
celery -A app.celery.tasks worker --loglevel=info
```

**2. Запустить Beat (планировщик):**
```bash
celery -A app.celery.tasks beat --loglevel=info
```

**3. Запустить всё вместе:**
```bash
# В одном терминале - worker
celery -A app.celery.tasks worker --loglevel=info

# В другом терминале - beat
celery -A app.celery.tasks beat --loglevel=info
```

**Или в одной команде:**
```bash
celery -A app.celery.tasks worker --beat --loglevel=info
```

---

### Способ 3: Через Python код напрямую

#### Единоразовый сбор

```python
from app.services.monitoring.collector import ContentCollector
from app.models import Source

async def collect_once():
    """Единоразовый сбор с конкретного источника."""
    collector = ContentCollector()
    
    # Получить источник
    source = await Source.objects.get(id=1)
    
    # Собрать контент
    result = await collector.collect_from_source(
        source=source,
        content_type="posts",
        analyze=True
    )
    
    print(f"Собрано {result['content_count']} элементов")
    print(f"Анализ выполнен: {result['analyzed']}")
```

#### Сбор со всех активных источников

```python
async def collect_all():
    """Сбор со всех активных источников."""
    collector = ContentCollector()
    
    # Получить все активные источники
    sources = await Source.objects.filter(is_active=True)
    
    for source in sources:
        print(f"Сбор с {source.name}...")
        
        result = await collector.collect_from_source(source, analyze=True)
        
        if result:
            print(f"✅ Успешно: {result['content_count']} элементов")
        else:
            print(f"❌ Ошибка при сборе")
```

#### Сбор с учётом cooldown

```python
from datetime import datetime, timedelta

async def collect_with_cooldown():
    """Сбор с учётом cooldown периода."""
    collector = ContentCollector()
    
    sources = await Source.objects.filter(is_active=True)
    
    for source in sources:
        # Проверить cooldown
        if source.bot_scenario_id:
            scenario = await source.bot_scenario
            cooldown = timedelta(minutes=scenario.cooldown_minutes)
            
            if source.last_checked:
                time_since_check = datetime.utcnow() - source.last_checked
                
                if time_since_check < cooldown:
                    remaining = cooldown - time_since_check
                    print(f"⏳ {source.name}: cooldown ({remaining.seconds // 60} мин осталось)")
                    continue
        
        # Cooldown прошёл, можно собирать
        print(f"🚀 {source.name}: начинаю сбор...")
        await collector.collect_from_source(source, analyze=True)
```

---

## 🔄 Полный цикл работы системы

### Шаг 1: Создание источников

```python
from app.models import Source, Platform

# Создать источник
source = await Source.objects.create(
    platform_id=1,
    name="Новостная группа VK",
    source_type="GROUP",
    external_id="-123456789",
    is_active=True
)
```

### Шаг 2: Назначение сценария

```python
from app.models import BotScenario

# Создать сценарий
scenario = await BotScenario.objects.create(
    name="Мониторинг каждые 30 минут",
    description="Стандартный мониторинг",
    analysis_types=["sentiment", "keywords"],
    content_types=["posts", "comments"],
    cooldown_minutes=30,
    is_active=True
)

# Назначить источнику
await Source.objects.update(
    source.id,
    bot_scenario_id=scenario.id
)
```

### Шаг 3: Запуск автоматического сбора

**Вариант A: Celery (рекомендуется)**
```bash
# Запустить worker и beat
celery -A app.celery.tasks worker --beat --loglevel=info
```

**Вариант B: Cron**
```bash
# Добавить в crontab
*/30 * * * * /usr/bin/python /path/to/app/scripts/collect_content.py
```

**Вариант C: systemd timer**
```ini
# /etc/systemd/system/collect-content.timer
[Unit]
Description=Collect social media content every 30 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=30min

[Install]
WantedBy=timers.target
```

### Шаг 4: Мониторинг

```python
# Проверить статус источников
from app.models import Source

sources = await Source.objects.get_statistics()
print(f"Всего источников: {sources['total']}")
print(f"Активных: {sources['active']}")
print(f"Никогда не проверялись: {sources['never_checked']}")
print(f"Со сценарием: {sources['with_scenario']}")
```

---

## 📋 Сценарии использования

### Сценарий 1: Мониторинг новостей (частый)

```python
scenario = await BotScenario.objects.create(
    name="Мониторинг новостей",
    cooldown_minutes=5,  # Каждые 5 минут
    analysis_types=["keywords", "topics"],
    content_types=["posts"],
    ai_prompt="Найди важные новости: {content}",
    action_type="NOTIFICATION",  # Уведомлять о новых постах
)
```

**Применение:**
- Новостные группы
- Официальные каналы
- Важные источники информации

---

### Сценарий 2: Анализ настроения (средний)

```python
scenario = await BotScenario.objects.create(
    name="Анализ настроения клиентов",
    cooldown_minutes=60,  # Каждый час
    analysis_types=["sentiment", "toxicity"],
    content_types=["posts", "comments"],
    ai_prompt="Проанализируй тональность: {content}",
    action_type=None,  # Только анализ, без действий
)
```

**Применение:**
- Мониторинг отзывов
- Анализ комментариев
- Изучение настроений аудитории

---

### Сценарий 3: Ежедневная сводка (редкий)

```python
scenario = await BotScenario.objects.create(
    name="Ежедневная сводка",
    cooldown_minutes=1440,  # Раз в сутки
    analysis_types=["sentiment", "keywords", "topics", "engagement"],
    content_types=["posts", "comments", "reactions"],
    ai_prompt="Создай ежедневную сводку: {content}",
    action_type="NOTIFICATION",  # Отправить сводку
)
```

**Применение:**
- Дейли репорты
- Аналитические сводки
- Статистика за день

---

## 🛠️ Утилиты и скрипты

### Скрипт для разового запуска

**Файл:** `scripts/collect_once.py`
```python
#!/usr/bin/env python3
"""Единоразовый сбор контента."""

import asyncio
from app.services.monitoring.collector import ContentCollector
from app.models import Source

async def main():
    collector = ContentCollector()
    
    # Собрать со всех активных источников
    sources = await Source.objects.filter(is_active=True)
    
    print(f"Найдено {len(sources)} активных источников")
    
    for source in sources:
        print(f"Сбор с {source.name}...")
        result = await collector.collect_from_source(source, analyze=True)
        
        if result:
            print(f"  ✅ Собрано: {result['content_count']} элементов")
        else:
            print(f"  ❌ Ошибка")

if __name__ == "__main__":
    asyncio.run(main())
```

**Запуск:**
```bash
python scripts/collect_once.py
```

---

### Скрипт для сбора с cooldown

**Файл:** `scripts/collect_with_cooldown.py`
```python
#!/usr/bin/env python3
"""Сбор контента с учётом cooldown."""

import asyncio
from datetime import datetime, timedelta
from app.services.monitoring.collector import ContentCollector
from app.models import Source

async def main():
    collector = ContentCollector()
    sources = await Source.objects.filter(is_active=True)
    
    stats = {'total': 0, 'collected': 0, 'skipped': 0, 'errors': 0}
    
    for source in sources:
        stats['total'] += 1
        
        # Проверить cooldown
        if source.bot_scenario_id:
            scenario = await source.bot_scenario
            if scenario:
                cooldown = timedelta(minutes=scenario.cooldown_minutes)
                
                if source.last_checked:
                    time_since = datetime.utcnow() - source.last_checked
                    
                    if time_since < cooldown:
                        remaining_min = (cooldown - time_since).seconds // 60
                        print(f"⏳ {source.name}: cooldown ({remaining_min} мин)")
                        stats['skipped'] += 1
                        continue
        
        # Собрать
        print(f"🚀 {source.name}: сбор...")
        result = await collector.collect_from_source(source, analyze=True)
        
        if result:
            print(f"  ✅ {result['content_count']} элементов")
            stats['collected'] += 1
        else:
            print(f"  ❌ Ошибка")
            stats['errors'] += 1
    
    print("\nИтого:")
    print(f"  Всего источников: {stats['total']}")
    print(f"  Собрано: {stats['collected']}")
    print(f"  Пропущено (cooldown): {stats['skipped']}")
    print(f"  Ошибок: {stats['errors']}")

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 🔍 Мониторинг и отладка

### Проверить статус источников

```python
from app.models import Source
from datetime import datetime, timedelta

# Источники готовые к сбору
ready_sources = []
cooldown_sources = []

sources = await Source.objects.filter(is_active=True)

for source in sources:
    if source.bot_scenario_id:
        scenario = await source.bot_scenario
        cooldown = timedelta(minutes=scenario.cooldown_minutes)
        
        if source.last_checked:
            time_since = datetime.utcnow() - source.last_checked
            
            if time_since >= cooldown:
                ready_sources.append(source)
            else:
                cooldown_sources.append((source, cooldown - time_since))
        else:
            ready_sources.append(source)  # Никогда не проверяли

print(f"Готовы к сбору: {len(ready_sources)}")
print(f"В cooldown: {len(cooldown_sources)}")
```

### Посмотреть результаты анализа

```python
from app.models import AIAnalytics

# Последние 10 анализов
analytics = await AIAnalytics.objects.filter().order_by(
    AIAnalytics.created_at.desc()
).limit(10)

for a in analytics:
    print(f"\nАнализ #{a.id}")
    print(f"  Источник: {a.source_id}")
    print(f"  Дата: {a.analysis_date}")
    print(f"  Модель: {a.llm_model}")
    
    if 'scenario_metadata' in a.summary_data:
        meta = a.summary_data['scenario_metadata']
        print(f"  Сценарий: {meta['scenario_name']}")
        print(f"  Типы анализа: {meta['analysis_types']}")
```

---

## ✅ Checklist для запуска

### Базовая настройка:
- [ ] Создать Platform (VK, Telegram, etc.)
- [ ] Добавить credentials для платформы
- [ ] Создать Source (группа, канал, пользователь)
- [ ] Проверить что source.is_active = True

### Настройка сценария (опционально):
- [ ] Создать BotScenario
- [ ] Установить cooldown_minutes
- [ ] Выбрать analysis_types
- [ ] Написать ai_prompt
- [ ] Назначить сценарий источнику

### Ручной запуск:
- [ ] Запустить API
- [ ] Получить auth token
- [ ] Вызвать POST /collect/source
- [ ] Проверить результат в AIAnalytics

### Автоматический запуск:
- [ ] Настроить Celery
- [ ] Запустить worker
- [ ] Запустить beat
- [ ] Проверить логи

---

## 📚 Дополнительные материалы

- **BOT_BEHAVIOR.md** - логика работы action_type
- **AI_SERVICES_UPDATE.md** - документация AI сервисов
- **SCENARIO_IMPLEMENTATION.md** - детали сценариев

---

**Готово! Система полностью готова к сбору и анализу контента.** ✅
