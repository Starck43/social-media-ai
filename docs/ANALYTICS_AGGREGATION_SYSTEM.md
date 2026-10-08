# Analytics Aggregation System

## Grouping axes vs time slices

Analytics rows in `ai_analytics` are flat — one row per source per date. The
**grouping axis** (`group_by`) and the **time slice** (`time_breakdown`) are
two independent query-time parameters that shape how those rows are presented.

| Dimension | What it answers | Values |
|-----------|----------------|--------|
| **Grouping axis** (`GroupingAxis`) | *By what attribute* do we group? | `days`, `themes`, `sources`, `entities`, `sentiment`, `content_type`, `intent`, `topic_chains` |
| **Time slice** | *How* do we show dynamics? | aggregate (default) or per-date breakdown (`time_breakdown: true`, digest/API only) |

They are orthogonal: any axis can be shown as an aggregate over the period OR
as a per-date chronology.

> **Important:** `GroupingAxis` has 8 members. Seven are digest grouping axes 
> (themes, sources, entities, sentiment, content_type, intent, topic_chains). 
> The eighth, days, is a web-only chronology view: the digest brief never groups by it — it uses time_breakdown: true instead. 
> The old `AgentScenario.analyze_type` was dropped by migration `0083`; grouping is now a query-time parameter passed to 
> `group_analytics()` via `group_by` (and `time_breakdown` for per-date sub-entries).
>
> **Web UI note:** the web analytics page exposes `days` as a dedicated
> **"Хронология"** tab (chronology as its own view) and hides `topic_chains`
> from the axis switcher. Both remain fully functional via direct URL or API.

### `GroupingAxis` values

| Axis | Groups by | JSONB field | Notes |
|------|-----------|-------------|-------|
| `days` | `analysis_date` | — | **Web-only view mode** ("Хронология" tab). Not a digest grouping axis. |
| `themes` | Semantic topics | `summary_data->'topics'` | |
| `sources` | Source/channel | `source_id` | |
| `entities` | Named objects (brands, persons, orgs) | `summary_data->'entities'` | Supports `entity_type` filter |
| `sentiment` | Mood/score | `summary_data->'sentiment_score'` | |
| `content_type` | Media format | `media_types` | |
| `intent` | Why the post was written | `summary_data->'intent_type'` | |
| `topic_chains` | Cross-row theme chains | `topic_chain_id` | Hidden from web axis switcher; accessible via direct URL |

> **Notes:**
> - `days` is a **web-only chronology view** (per-date timeline), not a digest grouping axis.
>   Digest uses `time_breakdown: true` instead (see DIGEST.md).
> - `topic_chains` is **hidden from the web axis switcher** to avoid duplication with the
>   top "Тематические цепочки" card. Access via direct URL `?group_by=topic_chains` or the
>   dedicated `/analytics/chains` page.

### Chain resolver

`app/services/ai/chain_resolver.py` normalizes `topic_hint` for chain matching:
- Lowercase + ё→е + punctuation strip
- Lightweight Russian stemming (no NLTK dependency)
- Trim to 255 chars

Similarity fallback: `token_set_ratio(normalized_hint, chain.normalized_label) >= 0.85` 
via `difflib.SequenceMatcher`, limited to lookback window (30 days).

CLI tools for chain management:

```bash
python -m cli.main chains merge --apply  # Deduplicate by normalized_label
python -m cli.main chains groups --min-size 2  # Inspect duplicate groups
```

The `normalized_label` column (migration 0085) stores the normalized form for 
fast similarity comparisons.

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
| `days` | Web-only chronology view (`?group_by=days`), not a digest grouping axis |
| `sources` | `group_by=sources` |
| `monitored_users` | `group_by=entities, entity_type=person` |

`time_breakdown` adds per-date sub-entries *within* any axis group. The web
analytics page uses the default aggregate view over the selected date window;
the digest can enable `time_breakdown: true` for a per-day chronology.

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
│  - estimated_cost: расчетная стоимость (USD cents, NUMERIC(14,6))│
│  - provider_type: провайдер LLM (openai, deepseek)         │
│  - media_types: типы медиа (text, image, video)            │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│ LAYER 2: Aggregation Service                               │
├─────────────────────────────────────────────────────────────┤
│ group_analytics(group_by, time_breakdown, ...)            │
│   → универсальная группировка по любой оси                 │
│                                                             │
│ generate_digest_brief()                                    │
│   → краткая сводка для дайджеста                           │
│                                                             │
│ ReportAggregator + специализированные агрегации           │
│ (app/services/ai/grouping.py + reporting.py):             │
│  - get_toxicity_summary()      → сводка по токсичности     │
│  - toxicity, hashtags, brands, viral content                │
│  - influencers, competitors, intent, demographics           │
│  Specialized sections use DIGEST_SPECIALIZED/reporting.py.  │
│  Sources/chains/chronology use group_analytics().            │
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
│  - GET /analytics/aggregate/grouped                        │
│  - GET /analytics/topic-chains                             │
│  - GET /analytics/topic-chains/{chain_id}                  │
│  - GET /analytics/topic-chains/{chain_id}/evolution        │
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
| `/grouped` | GET | Универсальная группировка по любой оси (themes, sources, entities, sentiment, content_type, intent, topic_chains; days is web-only) |

**Общие параметры:**
- `source_id` (optional): Фильтр по источнику
- `scenario_id` (optional): Фильтр по сценарию
- `days` (int): Период анализа (1-90)
- `limit` (int): Макс. кол-во результатов (только для topics)

**Параметры для `/grouped`:**
- `group_by` (optional, default `themes`): `themes`, `sources`, `entities`, `sentiment`, `content_type`, `intent`, `topic_chains`. `days` is web-only and returns 400 in this API.
- `time_breakdown` (bool, default `false`): Включить разбивку по датам внутри каждой группы
- `entity_type` (optional, только для `entities`): Фильтр сущностей — `person`, `brand`

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
  estimated_cost=0.65,  # USD cents: 650 tokens / 1000 * $0.01/1K * 100; NUMERIC(14,6)
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

### Out of scope by design (one-VPS philosophy)

Per `docs/design/competitive_analysis.md` reject list:
- Redis cache
- Materialized views
- WebSocket / real-time dashboard
- PDF/Excel export

These are explicitly rejected for single-VPS deployments at our scale.

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
│           └── dashboard.py          # Обновлен: +7 endpoints (incl. /grouped, /topic-chains*)
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
# Expected repository/deployed head: 0086 (head)
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
2. Обновить документацию DIGEST.md и ARCHITECTURE.md под текущую схему

## Structured output, data boundaries and request audit

- All three analysis clients (OpenAI-compatible, Anthropic and custom) validate
  configured outputs with Pydantic. Required fields, nested object/array item
  types, enum values, nullability and scalar bounds from the supported schema
  subset are checked. Schema-invalid outputs return an error and empty `parsed`
  data; they are not saved as successful analyses or dedup successes. Token
  usage remains attached to the response. Optional omitted fields remain omitted,
  and additional fields survive unless `additionalProperties=false`.
- Generated schemas include typed common fields (`topic_hint`, `is_meaningful`,
  `confidence`, title and analysis summary) so validation does not erase the
  relevance or chain signals. Legacy `summary` and analysis-type required fields
  retain their contract. The unified summary has its own contract, not the
  per-media scenario schema.
- `{text}` is framed as untrusted third-party data on custom and default prompt
  paths. Payload/scope cannot replace this system-owned variable. Known control
  sequences are removed and boundary tags escaped. This is defense in depth,
  not a promise that prompt injection is impossible.
- Every saved row includes `response_payload.request` even with DEBUG disabled:
  tenant/source/scenario IDs, detached methodology/configuration and scope,
  allowlisted task parameters, trigger configuration, resolved stage models and
  hashes. `request.prompt_hash` is SHA-256 over canonical methodology JSON,
  independent of post text; `request.prompts[stage].prompt_hash` hashes each exact
  rendered prompt (including unified summary when used). Arbitrary task keys
  such as credentials are not copied. Historical rows are not backfilled.
- Native provider-specific strict JSON Schema modes, automatic reanalysis and
  the separate chat-learning `extract_json` helper are not changed here.
