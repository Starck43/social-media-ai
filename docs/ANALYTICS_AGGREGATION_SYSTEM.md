# Analytics Aggregation System

## Grouping axes vs time slices

Analytics rows in `ai_analytics` are flat — one row per source per date. The
**grouping axis** (`group_by`) and the **time slice** (`time_breakdown`) are
two independent query-time parameters that shape how those rows are presented.

| Dimension | What it answers | Values |
|-----------|----------------|--------|
| **Grouping axis** (`GroupingAxis`) | *By what attribute* do we group? | `days`, `themes`, `sources`, `entities`, `intent`, `topic_chains` |
| **Time slice** | *How* do we show dynamics? | aggregate (default) or per-date breakdown (`time_breakdown: true`, digest/API only) |

They are orthogonal: any axis can be shown as an aggregate over the period OR
as a per-date chronology.

> **Important:** `GroupingAxis` has 6 members. Five are digest grouping axes
> (themes, sources, entities, intent, topic_chains).
> The sixth, days, is a web-only chronology view: the digest brief never groups by it — it uses time_breakdown: true instead.
> The old `AgentScenario.analyze_type` was dropped by migration `0083`; grouping is now a query-time parameter passed to
> `group_analytics()` via `group_by` (and `time_breakdown` for per-date sub-entries).
>
> **Web UI note:** the web analytics page exposes `days` as a dedicated
> **"Хронология"** tab (chronology as its own view) and hides `topic_chains`
> from the axis switcher. Chronology is web-only; chains have their own web page and API endpoints.


### Drill-down (web-only)

`GET /app/analytics/group?axis=...&value=...` opens a flat list of matching
analyses, newest date first (ID descending breaks date ties), using the same
period/source/tenant queryset as the group cards. Rows without topic_chain_id
are included; the chain list is not used to determine group membership.
The route checks `aianalytics.view` before fetching any rows. Invalid axes,
source IDs, entity types or sentiment/media values return HTTP 400.

| Drill-down axis | Row matching rule |
| --- | --- |
| themes | Case-insensitive membership in all distinct topics; `(без темы)` matches empty/missing topics |
| sources | Exact source_id (integer), never the displayed source name |
| entities | Exact entity name; optional entity_type person/brand/org |
| sentiment | Shared sentiment_bucket(score): positive >0.6, negative <0.4, boundaries neutral |
| content_type | Containment in normalized media_types (text/image/video) |
| intent | Extracted intent_type; unknown matches missing intent |
| days | Chronology key for the selected day/week/month view |

Extraction supports current multi_llm_analysis.text_analysis and flat/legacy
stored shapes. Sentiment and content_type remain row-filter names, not enum
axes or restored switcher tabs. Existing sentiment/media cross-filters also
apply to the drill-down list. Topics now count each matching analysis once for
every distinct topic, rather than only its first topic; case variants merge
under the first display label. Entity duplicates count once per analysis/name.
This makes group counts agree with the flat list (not occurrence frequency).
Source groups retain source_id alongside their display key, so same-named
sources stay distinguishable. No migration or new API endpoint is introduced.

Group cards link to this view with URL-encoded values. Chronology group headings
link too, without nesting anchors around their existing analysis links. The
breadcrumb preserves days, source_id, entity_type, tenant_id and cross-filters.
The list shows date, source, title, sentiment and an optional chain link, with
no write buttons. The chain link is shown only when at least two analyses
exist in the opened timeline's tenant/source/period scope, and includes that
count (`Цепочка · N анализ(ов)`). Counts are computed from the same scoped rows
before group/entity/sentiment/media matching, because the chain detail is a
retrospective rather than a cross-filtered membership list. A stored chain ID
alone does not imply a continuation; singleton IDs are retained for future runs.
No per-row database query is needed for these counts.

The explicit `← К группам` control and breadcrumb return to the exact main page
origin. Chain detail's primary control returns to the exact group, main page or
chain list it came from; `Все цепочки` remains secondary. Encoded `return_to`
preserves the complete query (axis/value, grouping period, days/source/entity
and cross-filters), including the group page's own parent. Only local read-only
analytics paths are accepted; external, malformed, mutation and fragment URLs
fall back to the existing local destination. Empty results show an empty state, not a redirect to chains.
Titles/labels in the new view are HTML-escaped.

Top thematic chain cards and direct `?group_by=topic_chains` groups link to
`/app/analytics/chains/{chain_id}`. The direct view is supported for existing
links but topic_chains stays out of the switcher; normal page loads still use
five group-count aggregations. The chain list's selection/filter/sort behavior
is unchanged. Its title/open links, sort form and chain-detail back link keep
the filter query. Nested source links are no longer nested inside a title link.
Chain detail honors tenant/source/period scope and reads nested/flat titles for
every timeline entry. It remains a retrospective, not a flat group filter.

### `GroupingAxis` values

| Axis | Groups by | JSONB field | Notes |
|------|-----------|-------------|-------|
| `days` | `analysis_date` | — | **Web-only view mode** ("Хронология" tab). Not a digest grouping axis. |
| `themes` | Semantic topics | `summary_data->'topics'` | |
| `sources` | Source/channel | `source_id` | |
| `entities` | По упоминаниям (бренды, персоны, организации) | `summary_data->'entities'` | Supports `entity_type` filter |
| `intent` | Why the post was written | `summary_data->'intent_type'` | |
| `topic_chains` | Cross-row theme chains | `topic_chain_id` | Dedicated chains page; not in the main switcher |

> **Notes:**
> - `days` is a **web-only chronology view** (per-date timeline), not a digest grouping axis.
>   Digest uses `time_breakdown: true` instead (see DIGEST.md).
> - `topic_chains` is **hidden from the web axis switcher** to avoid duplication with the
>   top "Тематические цепочки" card. Use the dedicated `/app/analytics/chains` page, not `?group_by=topic_chains`.

### Chain resolver

`app/services/ai/chain_resolver.py` normalizes `topic_hint` for chain matching:
- Lowercase + ё→е + punctuation strip
- Lightweight Russian stemming (no NLTK dependency)
- Trim to 255 chars

Similarity fallback: `_token_set_ratio(normalized_hint, normalized_stored_hint) >= 0.85`
using normalized token-set Sørensen–Dice overlap, limited to the lookback window (30 days).

CLI tools for chain management:

```bash
python -m cli.main chains merge --apply  # Deduplicate by normalized_label
python -m cli.main chains groups --min-size 2  # Inspect duplicate groups
```

The token-set score is Sørensen–Dice overlap: `2 * |A ∩ B| / (|A| + |B|)`
on normalized/stemmed token sets. Reordering tokens preserves a score of 1;
a single shared token in two two-token topics scores 0.5, not 1. The previous
intersection-versus-itself comparison spuriously promoted any shared word to
1 and is removed. The threshold and 30-day/source/workspace boundaries remain.
This applies to future resolution only: existing chain IDs are not silently
merged, split, backfilled or removed. Chains may start as a single analysis.

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
| `/grouped` | GET | Универсальная группировка по любой оси (themes, sources, entities, intent, topic_chains; days is web-only) |

**Общие параметры:**
- `source_id` (optional): Фильтр по источнику
- `scenario_id` (optional): Фильтр по сценарию
- `days` (int): Период анализа (1-90)
- `limit` (int): Макс. кол-во результатов (только для topics)

**Параметры для `/grouped`:**
- `group_by` (optional, default `themes`): `themes`, `sources`, `entities`, `intent`, `topic_chains`. `days` is web-only and returns 400 in this API.
- `time_breakdown` (bool, default `false`): Включить разбивку по датам внутри каждой группы
- `entity_type` (optional, только для `entities`): Фильтр упоминаний — `person`, `brand`, `org`

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

## Cross-axis filters and switcher visibility

`sentiment` and `content_type` are no longer GroupingAxis members. The Python
read-time enum has six members: days (web-only), themes, sources, entities,
intent and topic_chains. This change needs no database migration.

`group_analytics(..., sentiment=None, media=None)` prefilters rows before counts,
averages and date slices. Sentiment values: positive/neutral/negative; media:
text/image/video (containment in media_types, not equality of the whole list).
`reporting.sentiment_bucket(score)` is shared: >0.6 positive, <0.4 negative,
0.4 and 0.6 neutral. Missing/invalid/non-finite values match no sentiment filter.
Current nested, flat and legacy numeric scores are supported.

The main web switcher evaluates exactly five groupings: days/themes/sources/
entities/intent, with the active result reused for its badge. Tabs with fewer
than two groups are hidden unless active. Filters are applied to both grouped
results and badges, and survive axis/entity/period/workspace navigation in the
URL. Other headline widgets remain period-level metrics. Chains have their own
page and support sort=sentiment_asc (lowest available average score first,
unknown scores last); sorting preserves the active query filters.

Public grouped APIs and the main web page reject the removed axis values with
400 and a valid-set message. Saved digest tasks using either removed axis
normalize to themes, without deleting sentiment/content-mix metrics.


### Compact agent reports and narrative routing

`ReportAggregator.get_grouped_analytics()` scopes stored rows to the workspace,
source, inclusive reporting window and optional chain, then groups without an
LLM. Grouping is Python over stored JSON/JSONB data, not a new model invocation.
`report_period` and `analytics_chains` return the top 10 compact groups by default:
`{key, count, avg_sentiment}`. Chain keys are stable chain IDs. Explicit `limit=0`
returns all aggregates; positive limits select top N. Requested time breakdowns
contain only `{date, count, avg_sentiment}`. Chain detail returns daily aggregates,
not raw analyses. No summary_data, prompts or original posts enter tool output.

There is no extra narrative call in the report tools: the existing agent chat
cycle selects `resolve_default_model("text", strategy="cost_efficient")`, cheapest
combined configured input/output tariff first. The fallback fleet remains subject
to workspace tier restrictions and active provider/model checks. Digest narrative
uses `strategy="quality"` (configured defaults, never price as a quality proxy).
Existing caps and confirmation gates still apply. The system prompt requires
aggregate-first context; this change does not retroactively erase old sessions.

## Individual analysis detail

`/app/analytics/{id}` represents one saved analysis, not a complete source report.
It checks aianalytics.view and explicitly scopes the record and chain to the
accessible workspace. Group/chronology/dashboard/source/timeline entry links
preserve their complete read-only local origin via return_to. The primary back
control is contextual (list, analysis, chain, source or home). Direct historic
permalinks without days use an all-time source-list fallback, rather than losing
their chain to a default current-month window.

Themes link to the flat group view, including unchained analyses. The optional
chain link/embedded chronology appears only for >=2 records in the timeline
scope and returns to the originating analysis. Other timeline entries preserve
navigation context. Safe return destinations include numeric analysis/source
pages and URL-safe chain IDs; external/mutation/encoded unsafe paths are rejected.

The detail context shows the source, platform, record date, saved publication
window, material count and saved analysis timestamp. Saved HTTP(S) originals
are optional links, not guessed from an analysis ID. New analyses retain up to
50 unique original URLs handed over by collection; staging/replay preserves
permalink, author IDs and metric metadata before staged rows are retired.

### Metric availability and meaning

The display renderer uses None/— for missing, invalid, partial or unverified
values. Numeric 0 is displayed when the saved metric coverage confirms every
material's value was available. metric_coverage records known/total material
counts for new analyses. Normalizers flag unavailable API placeholders instead
of treating them as measured zeros. Staging JSON fields use typed JSON binds
and preserve these flags during deferred replay.

Legacy records without coverage retain displayed nonzero saved values with a
completeness disclaimer; historical zero placeholders are conservatively hidden.
They are not backfilled or silently rewritten. Summed daily author counts in a
period_rollup are not shown as distinct authors across the whole period.
Author/account counts describe available material-author identifiers, not readers.

Sentiment is a finite score in [0,1], labeled using the shared 0.4/0.6 boundaries.
The UI shows the score out of 1, not an audience percentage or model-confidence
claim. Parsed, flat and current nested stored payloads have compatible extraction.

The detail separates reactions/material, comments/material, views/material and
total views. Averages require a known total and positive material denominator.
No misleading percentage ER or sum of views plus interactions is shown. The
legacy engagement_rate stored formula is retained for compatibility elsewhere.
VK's existing combined reactions include comments/reposts; the page discloses
this and does not add comments a second time.

Empty highlights (a saved list) mean no highlighted moments in the saved result;
an absent field means not calculated or not saved. An absent summary is not proof
that analysis never ran. Titles, summaries and URL-bearing labels are escaped;
invalid sentiment scores and non-HTTP(S)/credential-bearing original URLs are
not rendered as trusted values or links. The shared admin metric renderer also
shows unknown values as — rather than converting them back to zero.


### Display titles

All web entry points (flat group drill-down, day chronology, individual detail,
chain timeline and dashboard recent analyses) use `render_analysis().display_title`:

1. First valid saved `analysis_title` (root, text analysis including `parsed`,
   unified summary including `parsed`, or legacy `ai_analysis`).
2. First sentence of a saved nonempty summary, normalized to a single line and
   shortened to at most 120 characters. Add `…` only when the excerpt is truncated;
   common abbreviations and decimal numbers are not treated as sentence boundaries.
3. First saved topic (`main_topics`, `topics`, `key_topics`; string or topic/name/key object).
4. `Материалы источника «{source_name}»`, or `Материалы источника` if the name is unavailable.

Short nonempty saved titles remain valid (no arbitrary minimum length). Full
summaries remain unchanged in storage and on the detail page. `entities` is
labelled **По упоминаниям** in tabs, breadcrumbs, digest headings and digest/task
selectors; `group_by=entities` and `entity_type=person|brand|org` are unchanged.

The technical row ID remains in the URL, not a headline. The current topic filter
and chain label are not substituted for the record's own content. Date and source
remain separate metadata; no LLM calls or historical JSON rewrite are needed.
Missing summary is described as `Сводка для этой записи не сохранена`, never as
proof that analysis did not run. Chronology entries without a summary stay clickable.

Validation: `997 passed, 1 skipped, 10 warnings in 224.24s (0:03:44)`.
Focused tests: 136 passed. Collection check: 998 tests collected. Regression tests compare
headings across group, chronology, detail, chain timeline and dashboard,
including missing titles/summaries, nested payloads, escaped topic text,
summary-derived headings/truncation, Russian abbreviations/decimal numbers,
mention-axis labels in the UI/digest and unchanged entities API parameters.
No migrations, production data rewrite or API changes.


### Shared analytics presentation and filters

The web app uses shared outline SVG icons (no emoji in the grouping switcher),
server-rendered active grouping/type/period links, light/dark semantic card styles
and consistent sentiment badges: a label plus `0.80 / 1` (an average for groups).
Plain-text digest sections use `Тональность`, not `sent:`.

Sentiment is a four-choice link group (any/positive/neutral/negative), not a select.
It is available on aggregate, drill-down and chain-list views, with scope, period,
mention type and validated return destination preserved. Mention types include
an explicit "Все" reset. Media is no longer a visible selector; the backend/API
still support legacy `media` URLs. Such a URL displays a clear reset action so an
active media constraint is not silently hidden. No scenario/media processing changes.

Theme preference is `light|dark|system`, shared across web/auth/admin/dashboard
entry points. System mode follows OS changes without converting them into a saved
explicit theme; selectors are available on desktop and mobile. The shared components
use the existing slate/cyan palette, keyboard focus, 44px controls and reduced motion.
See `docs/design/analytics_ui_refactor.md` for implementation and verification scope.
