# Analytics Aggregation System

## Grouping axes vs time slices

Analytics rows in `ai_analytics` are flat — one row per source per date. The
**grouping axis** (`group_by`) and the **time slice** (`time_breakdown`) are
two independent query-time parameters that shape how those rows are presented.

| Dimension | What it answers | Values |
|-----------|----------------|--------|
| **Grouping axis** | *By what attribute* do we group? | `themes`, `sources`, `entities`, `sentiment`, `content_type`, `intent` |
| **Time slice** | *How* do we show dynamics? | aggregate (default) or per-date breakdown (`time_breakdown: true`) |

They are orthogonal: any axis can be shown as an aggregate over the period OR
as a per-date chronology.

### Grouping axes

| Axis | Groups by | JSONB field |
|------|-----------|-------------|
| `themes` | Semantic topics | `summary_data->'topics'`, `topic_chain_id` |
| `sources` | Source/channel | `source_id` |
| `entities` | Named objects (brands, persons, orgs) | `summary_data->'entities'` |
| `sentiment` | Mood/score | `summary_data->'sentiment_score'` |
| `content_type` | Media format | `media_types` |
| `intent` | Why the post was written | `summary_data->'intent_type'` |

### Time breakdown

When `time_breakdown: true`, each group contains per-date sub-entries:

```
# group_by=themes, time_breakdown=false
- Тема "Отпуск" — 15 упоминаний

# group_by=themes, time_breakdown=true
- Тема "Отпуск":
  - 2026-10-01: 3 упоминания
  - 2026-10-03: 5 упоминаний
```

### Entity filtering

The `entities` axis supports an optional `entity_type` filter:

| Call | Result |
|------|--------|
| `group_by=entities` | All entities (brands + persons + orgs) |
| `group_by=entities, entity_type=person` | Only persons (replaces old `monitored_users`) |
| `group_by=entities, entity_type=brand` | Only brands |

### Migration from `analyze_type`

The old `AgentScenario.analyze_type` enum (`themes`, `days`, `sources`,
`monitored_users`) is being replaced:

| Old `analyze_type` | New parameters |
|-------------------|----------------|
| `themes` | `group_by=themes` |
| `days` | `group_by=themes, time_breakdown=true` |
| `sources` | `group_by=sources` |
| `monitored_users` | `group_by=entities, entity_type=person` |

The `days` mode was never a grouping axis — it was always a time display mode.
It becomes `time_breakdown=true` on any axis.

## Обзор

Система агрегации и отчетности для AI Analytics. Предоставляет админам и пользователям удобный доступ к агрегированным метрикам, трендам и аналитике.

## Архитектура

```
┌─────────────────────────────────────────────────────────────┐
│ LAYER 1: Data Collection (existing)                        │
├─────────────────────────────────────────────────────────────┤
│ AIAnalytics:                                                │
│  - summary_data (JSON): AI анализ контента                 │
│  - response_payload (JSON): сырые ответы LLM               │
│  - request_tokens, response_tokens: использование токенов  │
│  - estimated_cost: расчетная стоимость (в центах, с точностью до 1e-6 ¢)│
│  - provider_type: провайдер LLM (openai, deepseek)         │
│  - media_types: типы медиа (text, image, video)            │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│ LAYER 2: Aggregation Service                               │
├─────────────────────────────────────────────────────────────┤
│ ReportAggregator (app/services/ai/reporting.py):           │
│  - get_sentiment_trends()      → тренды тональности        │
│  - get_top_topics()             → топ темы/ключевые слова  │
│  - get_llm_provider_stats()     → статистика провайдеров   │
│  - get_content_mix()            → распределение медиа      │
│  - get_engagement_metrics()     → метрики вовлеченности    │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│ LAYER 3: API & Admin                                       │
├─────────────────────────────────────────────────────────────┤
│ API Endpoints (app/api/v1/endpoints/dashboard.py):        │
│  - GET /analytics/aggregate/sentiment-trends               │
│  - GET /analytics/aggregate/top-topics                     │
│  - GET /analytics/aggregate/llm-stats                      │
│  - GET /analytics/aggregate/content-mix                    │
│  - GET /analytics/aggregate/engagement                     │
│                                                             │
│ Admin Widgets (TODO):                                      │
│  - Sentiment trends cards на AgentScenario/Source pages   │
│  - Top topics списки                                        │
│  - LLM cost dashboard                                      │
└─────────────────────────────────────────────────────────────┘
```

## Реализованные компоненты

### 1. Модель AIAnalytics (расширена)

**Новые поля:**

```python
# LLM cost tracking (для агрегации и отчетов)
request_tokens: int | None       # Input tokens used
response_tokens: int | None      # Output tokens generated
estimated_cost: Decimal | None    # Estimated cost in USD cents, NUMERIC(14,6)
provider_type: str | None        # LLM provider: openai, deepseek, etc
media_types: list[str] | None    # Types analyzed: text, image, video
```

**Миграция:** `0031_add_llm_cost_tracking_to_analytics.py`

**Индекс для быстрых запросов:**
```sql
CREATE INDEX idx_ai_analytics_provider ON public.ai_analytics(provider_type);
```

### 2. ReportAggregator Service

**Расположение:** `app/services/ai/reporting.py`

**Методы:**

#### `get_sentiment_trends(source_id, scenario_id, days, group_by)`
Возвращает тренды тональности за период:
```json
{
  "trends": [
    {
      "date": "2025-10-15",
      "avg_sentiment_score": 0.75,
      "total_analyses": 5,
      "distribution": {
        "positive": 3,
        "neutral": 1,
        "negative": 1
      }
    }
  ],
  "period_days": 7,
  "group_by": "day"
}
```

#### `get_top_topics(source_id, scenario_id, days, limit)`
Топ темы/ключевые слова:
```json
{
  "topics": [
    {
      "topic": "AI Technologies",
      "count": 12,
      "avg_sentiment": 0.8,
      "examples": ["Example text 1...", "Example text 2..."]
    }
  ],
  "period_days": 7,
  "total_topics": 10
}
```

#### `get_llm_provider_stats(source_id, scenario_id, days)`
Статистика по провайдерам LLM:
```json
{
  "providers": {
    "openai": {
      "requests": 150,
      "total_tokens": 45000,
      "request_tokens": 30000,
      "response_tokens": 15000,
      "estimated_cost_usd": 0.45,
      "avg_tokens_per_request": 300.0,
      "models": {
        "gpt-4o-mini": 120,
        "gpt-4o": 30
      }
    },
    "deepseek": {
      "requests": 50,
      "total_tokens": 10000,
      ...
    }
  },
  "summary": {
    "total_requests": 200,
    "total_cost_usd": 0.55,
    "period_days": 30
  }
}
```

#### `get_content_mix(source_id, scenario_id, days)`
Распределение типов контента:
```json
{
  "media_types": {
    "text": {
      "count": 100,
      "percentage": 75.0
    },
    "image": {
      "count": 25,
      "percentage": 18.8
    },
    "video": {
      "count": 8,
      "percentage": 6.2
    }
  },
  "total_analyses": 120,
  "total_media_items": 133
}
```

#### `get_engagement_metrics(source_id, scenario_id, days)`
Метрики вовлеченности:
```json
{
  "avg_reactions_per_post": 15.3,
  "avg_comments_per_post": 3.2,
  "total_reactions": 1530,
  "total_comments": 320,
  "total_posts_analyzed": 100
}
```

### 3. API Endpoints

**Базовый путь:** `/api/v1/dashboard/analytics/aggregate/`

**Эндпоинты:**

| Endpoint | Метод | Описание |
|----------|-------|----------|
| `/sentiment-trends` | GET | Тренды тональности |
| `/top-topics` | GET | Топ темы/категории |
| `/llm-stats` | GET | Статистика LLM провайдеров |
| `/content-mix` | GET | Распределение типов контента |
| `/engagement` | GET | Метрики вовлеченности |

**Общие параметры:**
- `source_id` (optional): Фильтр по источнику
- `scenario_id` (optional): Фильтр по сценарию
- `days` (int): Период анализа (1-90)
- `limit` (int): Макс. кол-во результатов (только для topics)

**Пример запроса:**
```bash
GET /api/v1/dashboard/analytics/aggregate/sentiment-trends?source_id=1&days=7

# Ответ:
{
  "trends": [...],
  "period_days": 7,
  "group_by": "day"
}
```

**Аутентификация:** Требуется (`get_authenticated_user`)

### 4. AIAnalyzer Integration

**Обновлен метод:** `_save_analysis()`

**Новая логика:**
1. Извлечение метрик из LLM response:
   - `prompt_tokens` → `request_tokens`
   - `completion_tokens` → `response_tokens`
2. Подсчет стоимости:
   - Простая оценка: $0.01 за 1000 токенов
   - Сохранение в центах для точности
3. Определение провайдера:
   - Из `result['request']['provider']`
4. Определение типов медиа:
   - Из названия анализа: `text_analysis`, `image_analysis`, `video_analysis`

**Пример:**
```python
# В response_payload теперь есть:
{
  "response": {
    "usage": {
      "prompt_tokens": 500,
      "completion_tokens": 150
    }
  },
  "request": {
    "provider": "openai",
    "model": "gpt-4o-mini"
  }
}

# Сохраняется как:
analytics = AIAnalytics(
  request_tokens=500,
  response_tokens=150,
  estimated_cost=0.65,  # cents (650 tokens / 1000 * $0.01/1K), NUMERIC(14,6)
  provider_type="openai",
  media_types=["text"]
)
```

## Использование

### API Примеры

**1. Получить тренды тональности для источника:**
```python
import httpx

response = httpx.get(
    "http://localhost:8000/api/v1/dashboard/analytics/aggregate/sentiment-trends",
    params={"source_id": 1, "days": 14},
    headers={"Authorization": f"Bearer {token}"}
)
trends = response.json()
```

**2. Топ темы за последнюю неделю:**
```python
response = httpx.get(
    "http://localhost:8000/api/v1/dashboard/analytics/aggregate/top-topics",
    params={"days": 7, "limit": 10},
    headers={"Authorization": f"Bearer {token}"}
)
topics = response.json()
```

**3. Стоимость LLM за месяц:**
```python
response = httpx.get(
    "http://localhost:8000/api/v1/dashboard/analytics/aggregate/llm-stats",
    params={"days": 30},
    headers={"Authorization": f"Bearer {token}"}
)
stats = response.json()
print(f"Total cost: ${stats['summary']['total_cost_usd']}")
```

### Программное использование

```python
from app.services.ai.reporting import ReportAggregator

# Инициализация
aggregator = ReportAggregator()

# Получить тренды
trends = await aggregator.get_sentiment_trends(
    source_id=1,
    days=7
)

# Топ темы
topics = await aggregator.get_top_topics(
    scenario_id=2,
    days=14,
    limit=20
)

# LLM статистика
stats = await aggregator.get_llm_provider_stats(days=30)

# Контент микс
mix = await aggregator.get_content_mix(source_id=1, days=7)

# Вовлеченность
engagement = await aggregator.get_engagement_metrics(source_id=1, days=7)
```

## Будущие улучшения

### 1. Admin Widgets (TODO)

**AgentScenarioAdmin:**
```python
# В app/admin/views.py
class AgentScenarioAdmin(BaseAdmin):
    # Добавить виджеты на detail page:
    # - Sentiment trend chart (last 7 days)
    # - Top 5 topics table
    # - LLM cost summary
    # - Content mix pie chart
```

**SourceAdmin:**
```python
# В app/admin/views.py
class SourceAdmin(BaseAdmin):
    # Добавить виджеты:
    # - Sentiment trend line chart
    # - Engagement metrics cards
    # - Recent topics list
```

### 2. Кэширование (опционально)

Для больших датасетов можно добавить Redis кэш:

```python
# В reporting.py
from app.utils.cache import cache_result

@cache_result(ttl=3600)  # 1 hour cache
async def get_sentiment_trends(...):
    # Heavy aggregation logic
    ...
```

### 3. Материализованные view (для scale)

Если количество analytics > 100k, можно создать materialized views:

```sql
CREATE MATERIALIZED VIEW analytics_daily_summary AS
SELECT 
    source_id,
    analysis_date,
    COUNT(*) as total_analyses,
    AVG((summary_data->'sentiment_score')::float) as avg_sentiment,
    SUM(request_tokens) as total_request_tokens,
    SUM(response_tokens) as total_response_tokens,
    SUM(estimated_cost) as total_cost
FROM public.ai_analytics
GROUP BY source_id, analysis_date;

-- Refresh периодически
REFRESH MATERIALIZED VIEW analytics_daily_summary;
```

### 4. Экспорт отчетов

Добавить endpoints для экспорта:
- CSV export
- PDF reports
- Excel workbooks

### 5. Real-time updates

Добавить WebSocket для live dashboard:
```python
# app/api/v1/endpoints/websockets.py
@router.websocket("/ws/analytics")
async def analytics_websocket(websocket: WebSocket):
    # Stream updates to dashboard
    ...
```

## Структура файлов

```
app/
├── models/
│   └── ai_analytics.py              # Расширенная модель
├── services/
│   └── ai/
│       ├── analyzer.py               # Обновлен _save_analysis()
│       └── reporting.py              # ✨ NEW: ReportAggregator
├── api/
│   └── v1/
│       └── endpoints/
│           └── dashboard.py          # Обновлен: +5 endpoints
└── admin/
    └── views.py                      # TODO: widgets

migrations/
└── versions/
    └── 0031_add_llm_cost_tracking_to_analytics.py  # ✨ NEW

docs/
└── ANALYTICS_AGGREGATION_SYSTEM.md   # ✨ NEW: Эта документация
```

## Тестирование

### Проверка миграции
```bash
cd /Users/admin/Projects/social-media-ai
alembic current
# Должно показать: 0031 (head)
```

### Проверка сервиса
```bash
python3 -c "
from app.services.ai.reporting import ReportAggregator
aggregator = ReportAggregator()
print('✅ ReportAggregator loaded')
"
```

### Проверка API
```bash
# Запустить сервер
uvicorn app.main:app --reload

# Тест endpoints (после аутентификации)
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v1/dashboard/analytics/aggregate/sentiment-trends?days=7"

curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v1/dashboard/analytics/aggregate/top-topics?limit=10"

curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v1/dashboard/analytics/aggregate/llm-stats?days=30"
```

## Ключевые метрики для мониторинга

1. **Sentiment Score:** Средняя тональность (-1 до 1)
2. **Topic Coverage:** Кол-во уникальных тем
3. **LLM Cost:** Расходы в USD
4. **Token Efficiency:** Средние токены на запрос
5. **Provider Distribution:** Доля использования провайдеров
6. **Content Mix:** Распределение text/image/video
7. **Engagement Rate:** Реакции и комментарии на пост

## Заключение

Система агрегации предоставляет полный набор инструментов для:
- ✅ Мониторинга трендов тональности
- ✅ Анализа популярных тем
- ✅ Контроля затрат на LLM
- ✅ Оптимизации контент-микса
- ✅ Отслеживания вовлеченности

**Следующие шаги:**
1. Добавить admin widgets для визуализации
2. Создать scheduled task для pre-aggregation (опционально)
3. Добавить export функции (CSV/PDF)
4. Внедрить кэширование для performance
