# Обзор сервисов мониторинга и AI-анализа

## Архитектура

```
┌─────────────────────────────────────────────────────────────┐
│                      API Endpoints                          │
│         /monitoring/collect/source/{id}                     │
│         /monitoring/collect/platform/{id}                   │
│         /monitoring/collect/monitored                       │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│              ContentCollector (Orchestrator)                │
│  - Координирует сбор контента                              │
│  - Вызывает social clients для каждой платформы            │
│  - Запускает AI-анализ после сбора                         │
└────────┬────────────────────────────────────────────────┬───┘
         │                                                 │
         ▼                                                 ▼
┌─────────────────────────┐                  ┌────────────────────────┐
│   Social Clients        │                  │    AIAnalyzer          │
│  - VKClient             │                  │  - DeepSeek API        │
│  - TelegramClient       │                  │  - Анализ контента     │
│  - get_social_client()  │                  │  - Сохранение в БД     │
└─────────────────────────┘                  └────────────────────────┘
```

## 1. ContentCollector (app/services/monitoring/collector.py)

### Назначение
Главный оркестратор для сбора контента из социальных сетей.

### Основные методы

#### `collect_from_source(source, content_type, analyze)`
Собирает контент из одного источника.

**Процесс:**
1. Получает Platform для источника
2. Создает социальный клиент через фабрику
3. Собирает контент (посты, комментарии и т.д.)
4. Опционально запускает AI-анализ
5. Обновляет `last_checked` у источника

**Вызывается:**
- API endpoint `/monitoring/collect/source/{id}`
- Admin action "Check now" в SourceAdmin
- Celery задачи для периодического мониторинга

#### `collect_from_platform(platform_id, source_types, analyze)`
Собирает контент со всех активных источников платформы.

**Процесс:**
1. Получает все активные источники на платформе
2. Фильтрует по типам (USER, GROUP, CHANNEL и т.д.)
3. Для каждого источника вызывает `collect_from_source()`
4. Собирает статистику (успешные/неудачные, всего элементов)

**Вызывается:**
- API endpoint `/monitoring/collect/platform/{id}`
- Celery задачи для массового мониторинга

#### `collect_monitored_users(source, analyze)`
Собирает контент от отслеживаемых пользователей источника.

**Процесс:**
1. Загружает источник с relationship `monitored_users`
2. Для каждого отслеживаемого пользователя собирает контент
3. Опционально запускает анализ

**Вызывается:**
- API endpoint `/monitoring/collect/monitored`
- Когда источник типа GROUP отслеживает конкретных USER

**Пример:** VK группа отслеживает посты 5 конкретных пользователей

### Внутренний метод `_analyze_content(content, source)`
После сбора запускает 3 типа AI-анализа:
1. **SENTIMENT** - анализ тональности
2. **TOPICS** - определение тем
3. **ACTIVITY** - паттерны активности

## 2. AIAnalyzer (app/services/ai/analyzer.py)

### Назначение
Сервис для AI-анализа собранного контента через DeepSeek API.

### Типы анализа (AnalysisType)

1. **SENTIMENT** - Анализ тональности
   - Определяет: positive/negative/neutral/mixed
   - Уровень уверенности (confidence)
   - Ключевые эмоции

2. **TOPICS** - Определение тем
   - Список основных тем
   - Главная тема
   - Вес каждой темы

3. **ACTIVITY** - Паттерны активности
   - Уровень активности: high/medium/low
   - Обнаруженные паттерны
   - Пиковые времена активности

4. **KEYWORDS** - Ключевые слова
   - Ключевые слова
   - Фразы
   - Хэштеги

5. **ENGAGEMENT** - Вовлеченность
   - Уровень вовлеченности
   - Топ посты
   - Рекомендации

### Основной метод `analyze_content(content, source, analysis_type)`

**Процесс:**
1. Подготавливает текст из контента (первые 50 элементов)
2. Формирует промпт на русском языке для DeepSeek
3. Вызывает API DeepSeek
4. Парсит JSON-ответ
5. Сохраняет результаты в `AIAnalytics`

**Вызывается:**
- ContentCollector после сбора контента
- API endpoints для ручного анализа

### Внутренние методы

#### `_prepare_text(content)`
Извлекает текст из первых 50 элементов контента.

#### `_get_prompt(analysis_type, text)`
Создает промпт на русском языке для каждого типа анализа.

**Примеры промптов:**
```python
# SENTIMENT
"Проанализируй тональность следующего контента из социальных сетей..."

# TOPICS
"Определи основные темы в следующем контенте из социальных сетей..."

# ACTIVITY
"Проанализируй паттерны активности пользователей..."
```

#### `_call_api(prompt)`
Отправляет запрос к DeepSeek API с параметрами:
- model: `deepseek-chat`
- temperature: `0.3` (более детерминированные ответы)
- max_tokens: `1000`

#### `_save_analysis(api_result, source, analysis_type)`
Сохраняет результаты в `AIAnalytics`:
- Парсит JSON-ответ
- Добавляет метаданные (тип анализа, дата)
- Создает запись в БД

## 3. Social Clients (app/services/social/)

### Структура

```
app/services/social/
├── base.py          # BaseClient (abstract)
├── vk_client.py     # VKClient
├── tg_client.py     # TelegramClient
└── factory.py       # get_social_client()
```

### BaseClient (базовый класс)

Определяет интерфейс для всех социальных клиентов:

```python
class BaseClient(ABC):
    @abstractmethod
    async def collect_data(self, source: Source, content_type: str) -> List[Dict]:
        """Collect data from social network"""
        pass
    
    @abstractmethod
    async def normalize_data(self, raw_data: Any) -> List[Dict]:
        """Normalize data to common format"""
        pass
```

### VKClient

Клиент для VK API:
- Использует `access_token` из Platform
- Собирает посты, комментарии
- Нормализует в общий формат

### TelegramClient

Клиент для Telegram API:
- Использует `bot_token` из Platform
- Собирает сообщения из каналов/чатов
- Нормализует в общий формат

### get_social_client(platform)

Фабрика для создания клиентов:

```python
from app.services.social.factory import get_social_client

platform = await Platform.objects.get(id=1)
client = get_social_client(platform)  # Returns VKClient or TelegramClient

content = await client.collect_data(source, "posts")
```

## Поток данных

### Типичный сценарий мониторинга

```
1. Celery задача срабатывает каждый час
   ↓
2. ContentCollector.collect_from_platform(platform_id=1)
   ↓
3. Получает все активные источники (VK группы, каналы)
   ↓
4. Для каждого источника:
   ├─→ get_social_client(platform) → VKClient
   ├─→ client.collect_data(source, "posts") → [post1, post2, ...]
   ├─→ AIAnalyzer.analyze_content(posts, source, SENTIMENT)
   ├─→ AIAnalyzer.analyze_content(posts, source, TOPICS)
   └─→ AIAnalyzer.analyze_content(posts, source, ACTIVITY)
   ↓
5. Результаты сохраняются в AIAnalytics
   ↓
6. Обновляется source.last_checked
   ↓
7. Возвращается статистика:
   {
     "total_sources": 10,
     "successful": 9,
     "failed": 1,
     "total_items": 245
   }
```

## Кто вызывает сервисы

### 1. API Endpoints (`app/api/v1/endpoints/monitoring.py`)

```python
# Ручной запуск мониторинга одного источника
POST /api/v1/monitoring/collect/source/{source_id}

# Мониторинг всей платформы
POST /api/v1/monitoring/collect/platform/{platform_id}

# Мониторинг отслеживаемых пользователей
POST /api/v1/monitoring/collect/monitored
```

**Используется:**
- Frontend для ручного запуска
- Webhooks от внешних систем
- Admin панель

### 2. Celery Tasks (`app/celery/tasks.py`)

```python
# Периодическая задача каждый час
@celery.task
async def monitor_all_sources():
    platforms = await Platform.objects.filter(is_active=True)
    for platform in platforms:
        await collector.collect_from_platform(platform.id)
```

**Используется:**
- Автоматический мониторинг по расписанию
- Background jobs
- Отложенные задачи

### 3. Admin Actions (`app/admin/views.py`)

```python
class SourceAdmin(ModelView):
    async def action_check_now(self, ids: List[int]):
        """Admin action: Check source now"""
        for source_id in ids:
            source = await Source.objects.get(id=source_id)
            await collector.collect_from_source(source)
```

**Используется:**
- Быстрая проверка из админки
- Отладка мониторинга
- Ручное управление

## Модель данных

### AIAnalytics

Хранит результаты анализа:

```json5
{
    "id": 1,
    "source_id": 5,
    "analysis_date": "2024-01-15",
    "period_type": "DAILY",
    "analysis_type": "SENTIMENT",  // ← Новое поле
    "summary_data": {
        "sentiment": "positive",
        "confidence": 0.85,
        "summary": "Преобладают позитивные отзывы",
        "key_emotions": ["радость", "удовлетворение"],
        "analysis_type": "sentiment",
        "analyzed_at": "2024-01-15"
    }
}
```

## Настройка

### Environment Variables

```bash
# DeepSeek API
DEEPSEEK_API_KEY=sk-xxxxx

# VK API
VK_ACCESS_TOKEN=stored_in_platform_table

# Telegram API
TELEGRAM_BOT_TOKEN=stored_in_platform_table
```

### Celery Beat Schedule

```python
# celeryconfig.py
beat_schedule = {
    'monitor-all-sources': {
        'task': 'app.celery.tasks.monitor_all_sources',
        'schedule': crontab(minute=0, hour='*/1'),  # Каждый час
    },
}
```

## Примеры использования

### Пример 1: Ручной запуск мониторинга

```python
from app.services.monitoring.collector import ContentCollector
from app.models import Source

collector = ContentCollector()

# Один источник
source = await Source.objects.get(id=5)
result = await collector.collect_from_source(
    source=source,
    content_type="posts",
    analyze=True
)
print(f"Collected {result['content_count']} items")

# Вся платформа
result = await collector.collect_from_platform(
    platform_id=1,
    analyze=True
)
print(f"Success: {result['successful']}/{result['total_sources']}")
```

### Пример 2: Только AI-анализ существующего контента

```python
from app.services.ai.analyzer import AIAnalyzer
from app.types.models import AnalysisType

analyzer = AIAnalyzer()

content = [
    {"text": "Отличный продукт, всем рекомендую!"},
    {"text": "Очень доволен покупкой"},
    {"text": "Качество на высоте"}
]

source = await Source.objects.get(id=5)

# Анализ тональности
analytics = await analyzer.analyze_content(
    content=content,
    source=source,
    analysis_type=AnalysisType.SENTIMENT
)

print(analytics.summary_data)
# {
#   "sentiment": "positive",
#   "confidence": 0.92,
#   "summary": "Преобладают положительные отзывы о качестве"
# }
```

### Пример 3: Получение результатов анализа

```python
from app.models import AIAnalytics
from app.types.models import AnalysisType

# Последняя аналитика по источнику
analytics = await (
    AIAnalytics.objects
    .filter(source_id=5, analysis_type=AnalysisType.SENTIMENT)
    .order_by(AIAnalytics.analysis_date.desc())
    .first()
)

print(analytics.summary_data['sentiment'])  # "positive"
print(analytics.summary_data['confidence'])  # 0.85
```

## Расширение системы

### Добавление нового типа анализа

1. Добавить в `AnalysisType` enum:
```python
class AnalysisType(Enum):
    HASHTAG_TRENDS = "hashtag_trends"
```

2. Добавить промпт в `AIAnalyzer._get_prompt()`:
```python
AnalysisType.HASHTAG_TRENDS: f"""
Определи трендовые хэштеги в следующем контенте...
"""
```

3. Вызвать в `ContentCollector._analyze_content()`:
```python
await self.ai_analyzer.analyze_content(
    content, source, AnalysisType.HASHTAG_TRENDS
)
```

### Добавление новой социальной сети

1. Создать `app/services/social/instagram_client.py`:
```python
from .base import BaseClient

class InstagramClient(BaseClient):
    async def collect_data(self, source, content_type):
        # Implement Instagram API logic
        pass
```

2. Обновить фабрику в `factory.py`:
```python
from .instagram_client import InstagramClient

def get_social_client(platform: Platform):
    if platform.type == PlatformType.INSTAGRAM:
        return InstagramClient(platform)
```

3. Добавить `PlatformType.INSTAGRAM` в enum

## Логирование

Все сервисы используют стандартный Python logging:

```python
import logging

logger = logging.getLogger(__name__)

logger.info("Analysis saved for source {source.id}")
logger.warning("No content to analyze")
logger.error("Error analyzing content", exc_info=True)
```

Логи помогают отслеживать:
- Начало/конец сбора данных
- Ошибки API
- Результаты анализа
- Performance метрики

## Итоги

**ContentCollector** - главный оркестратор, координирует весь процесс мониторинга

**AIAnalyzer** - специализированный сервис для AI-анализа через DeepSeek

**Social Clients** - адаптеры для работы с API разных платформ

**Вызывается из:**
- API endpoints (ручной запуск)
- Celery tasks (автоматический мониторинг)
- Admin actions (управление из админки)

**Результаты сохраняются в:**
- `AIAnalytics` - результаты AI-анализа
- `Source.last_checked` - время последней проверки
