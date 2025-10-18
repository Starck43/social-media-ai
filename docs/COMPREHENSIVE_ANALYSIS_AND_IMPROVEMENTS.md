# Comprehensive Analysis: Data Collection, Topic Chains & Optimizations

**Date**: Current Session  
**Author**: Factory Droid

---

## Вопросы пользователя

1. **Период в цепочках** - показывать даты реальных постов вместо дат анализа
2. **last_checked** - отображать дату последней проверки источника  
3. **LEGACY поля** - что делать с `text_llm_provider_id`, `image_llm_provider_id`, `video_llm_provider_id`
4. **Topic Matching** - как ИИ определяет новую тему vs продолжение старой
5. **Оптимизация сбора** - повторные запуски, инкрементальная загрузка
6. **API платформ** - фильтрация по дате в VK/Telegram
7. **Полный цикл** - анализ взаимодействия ИИ и платформ

---

## 1. Период в цепочках: Даты реальных постов

### Текущая ситуация

**Сейчас**:
```
18 окт. 2025 г. - 18 окт. 2025 г. | 1 анализ
```

Показываются даты **анализа** (`AIAnalytics.analysis_date`), а не даты **реальных постов**.

### Проблема

`AIAnalytics.analysis_date` = дата когда **запущен анализ**  
Реальные посты могли быть опубликованы **неделю назад** или **вчера**

### Решение

Добавить в `summary_data` поля:
- `content_date_range.earliest` - дата самого старого поста
- `content_date_range.latest` - дата самого нового поста

#### Изменения в analyzer.py

```python
def _extract_content_statistics(self, content: list[dict]) -> dict:
    """Extract statistics from collected content."""
    
    # Find date range from actual content
    post_dates = []
    for item in content:
        # Try different date fields
        pub_date = item.get('published_at') or item.get('date') or item.get('created_at')
        if pub_date:
            if isinstance(pub_date, int):  # Unix timestamp (VK)
                post_dates.append(datetime.fromtimestamp(pub_date, tz=UTC))
            elif isinstance(pub_date, str):
                post_dates.append(datetime.fromisoformat(pub_date))
    
    content_date_range = {}
    if post_dates:
        content_date_range = {
            'earliest': min(post_dates).isoformat(),
            'latest': max(post_dates).isoformat()
        }
    
    return {
        'total_posts': len(content),
        'content_date_range': content_date_range,  # NEW
        # ... existing fields
    }
```

#### Отображение в dashboard

```javascript
// В buildChainCard()
const contentDates = chain.content_date_range || {}
const dateDisplay = contentDates.earliest && contentDates.latest
    ? `${DashboardUtils.formatDate(contentDates.earliest)} - ${DashboardUtils.formatDate(contentDates.latest)}`
    : `${DashboardUtils.formatDate(chain.first_date)} - ${DashboardUtils.formatDate(chain.last_date)}`

// Show
<div class="chain-meta">
    <span><i class="fas fa-calendar me-1"></i> ${dateDisplay}</span>
    <span><i class="fas fa-chart-bar me-1"></i> ${chain.analyses_count} анализов</span>
</div>
```

### last_checked отдельно

**Где показывать**: Под заголовком цепочки как дополнительная метрика

```javascript
<div class="chain-header">
    <div class="chain-title">
        <i class="fas fa-sparkles me-2 text-primary"></i>
        ${displayTitle}
    </div>
    <div class="chain-meta">
        <span title="Период постов"><i class="fas fa-calendar me-1"></i> ${contentDateRange}</span>
        <span title="Количество анализов"><i class="fas fa-chart-bar me-1"></i> ${chain.analyses_count} анализов</span>
        ${source.last_checked ? `
        <span title="Последняя проверка источника" class="text-muted">
            <i class="fas fa-sync me-1"></i> Проверено: ${DashboardUtils.formatDateTime(source.last_checked)}
        </span>
        ` : ''}
    </div>
</div>
```

**Визуально**:
```
✨ Обсуждение дизайна и реакции в комментариях

📅 15 окт - 18 окт | 📊 3 анализа | 🔄 Проверено: 18 окт, 14:30
```

---

## 2. LEGACY поля: text_llm_provider_id и другие

### Текущая ситуация

```python
# app/models/bot_scenario.py

# LEGACY: Individual FK fields (kept for backward compatibility)
text_llm_provider_id: Mapped[int | None] = Column(...)
image_llm_provider_id: Mapped[int | None] = Column(...)
video_llm_provider_id: Mapped[int | None] = Column(...)

# NEW: Strategy-based selection
llm_strategy: Mapped[LLMStrategyType] = LLMStrategyType.sa_column(...)
```

### Проблема

**Дублирование логики**:
- Старый подход: явно указывать provider для каждого типа
- Новый подход: стратегия автоматически выбирает provider

**Миграция данных**: Есть ли сценарии использующие старые поля?

### Анализ использования

```python
# Проверим используются ли LEGACY поля
SELECT 
    COUNT(*) as total,
    COUNT(text_llm_provider_id) as with_text_provider,
    COUNT(image_llm_provider_id) as with_image_provider,
    COUNT(video_llm_provider_id) as with_video_provider
FROM social_manager.bot_scenarios;
```

### Рекомендация

#### Вариант 1: **Полное удаление** (если не используются)

**Когда**: Если все сценарии используют `llm_strategy`

**Шаги**:
1. Создать миграцию Alembic для удаления колонок
2. Удалить поля из модели
3. Убрать из схем (schemas/scenario.py)

```python
# Migration
def upgrade():
    op.drop_column('bot_scenarios', 'text_llm_provider_id', schema='social_manager')
    op.drop_column('bot_scenarios', 'image_llm_provider_id', schema='social_manager')
    op.drop_column('bot_scenarios', 'video_llm_provider_id', schema='social_manager')

def downgrade():
    # Restore if needed
    op.add_column('bot_scenarios', 
        sa.Column('text_llm_provider_id', sa.Integer(), nullable=True),
        schema='social_manager'
    )
    # ...
```

#### Вариант 2: **Миграция с fallback** (безопаснее)

**Когда**: Если есть старые сценарии

**Шаги**:
1. Добавить migration script для копирования данных
2. Установить llm_strategy на основе старых полей
3. Пометить LEGACY поля как deprecated
4. Через N релизов удалить

```python
# Migration: Copy old provider assignments to strategy
def upgrade():
    # If text_llm_provider_id is set but llm_strategy is NULL
    # → set llm_strategy = "quality" (assume explicit choice = quality preference)
    op.execute("""
        UPDATE social_manager.bot_scenarios
        SET llm_strategy = 'quality'
        WHERE text_llm_provider_id IS NOT NULL 
          AND llm_strategy IS NULL
    """)
    
    # Mark columns as deprecated (add comment)
    op.execute("""
        COMMENT ON COLUMN social_manager.bot_scenarios.text_llm_provider_id 
        IS 'DEPRECATED: Use llm_strategy instead'
    """)
```

#### Вариант 3: **Hybrid подход** (текущий)

**Оставить как есть**, но:
- Скрыть из UI (убрать из forms)
- Использовать только для чтения (backward compatibility)
- Документировать как deprecated

```python
# В BotScenarioAdmin
class BotScenarioAdmin(ModelView):
    # Exclude LEGACY fields from forms
    form_excluded_columns = [
        'text_llm_provider_id', 
        'image_llm_provider_id', 
        'video_llm_provider_id'
    ]
    
    # Show in list but mark as deprecated
    column_labels = {
        'text_llm_provider_id': '⚠️ Text Provider (Legacy)',
        'llm_strategy': 'LLM Strategy (Recommended)'
    }
```

### **Финальная рекомендация**: Вариант 2

**План**:
1. Создать миграцию для копирования данных (если есть)
2. Установить default llm_strategy для старых записей
3. Скрыть LEGACY поля из UI
4. Добавить warning в логи если используются
5. Через 2-3 релиза полностью удалить

---

## 3. Topic Matching: Как ИИ определяет новую тему vs продолжение

### Текущая реализация

**Файл**: `app/services/ai/analyzer.py`

```python
async def _find_matching_topic_chain(
    self,
    source: Source,
    current_topics: List[str],
    lookback_days: int = 7
) -> Optional[str]:
    """
    Find existing topic chain matching current analysis topics.
    
    Algorithm:
    1. Get recent analyses (last 7 days)
    2. Extract topics from each analysis
    3. Compare with current topics (string matching)
    4. If ≥50% overlap → return existing chain_id
    5. Otherwise → None (create new chain)
    """
    
    # Get recent analyses
    recent_analyses = await AIAnalytics.objects.filter(
        source_id=source.id,
        analysis_date__gte=cutoff_date
    ).order_by(AIAnalytics.analysis_date.desc()).limit(10)
    
    # Normalize topics
    current_topics_normalized = [t.lower().strip() for t in current_topics]
    
    # Check each analysis
    for analysis in recent_analyses:
        prev_topics = extract_topics(analysis.summary_data)
        prev_topics_normalized = [t.lower().strip() for t in prev_topics]
        
        # Calculate overlap
        matches = sum(1 for t in current_topics_normalized if t in prev_topics_normalized)
        match_ratio = matches / len(current_topics_normalized)
        
        if match_ratio >= 0.5:  # 50% threshold
            return analysis.topic_chain_id
    
    return None  # Create new chain
```

### Пример работы

#### Сценарий 1: Продолжение темы

**Анализ #1** (15 октября):
- Темы: ["ремонт дорог", "благоустройство", "пробки"]
- Chain ID: `chain_16_abc123`

**Анализ #2** (18 октября):
- Темы: ["ремонт дорог", "пробки", "объезды"]
- Совпадение: 2 из 3 (66%) ✅
- **Результат**: Добавляется в `chain_16_abc123`

#### Сценарий 2: Новая тема

**Анализ #1** (15 октября):
- Темы: ["ремонт дорог", "благоустройство", "пробки"]
- Chain ID: `chain_16_abc123`

**Анализ #3** (20 октября):
- Темы: ["вакцинация", "covid", "прививки"]
- Совпадение: 0 из 3 (0%) ❌
- **Результат**: Создается новая цепочка `chain_16_def456`

### Ограничения текущего подхода

1. **String matching** - простой, но не учитывает семантику
   - "автомобильные пробки" ≠ "пробки" (хотя одно и то же)
   - "ремонт" ≠ "починка дорог" (хотя связано)

2. **Fixed threshold (50%)** - не адаптируется
   - Для узких тем нужен выше порог
   - Для широких тем можно ниже

3. **Lookback days = 7** - жестко заданное окно
   - Активные источники могут иметь короткий цикл (1-2 дня)
   - Медленные источники могут обновляться раз в месяц

### Улучшения

#### Вариант A: Semantic Similarity (embeddings)

```python
from openai import AsyncOpenAI

async def _find_matching_topic_chain_semantic(
    self,
    source: Source,
    current_topics: List[str],
    similarity_threshold: float = 0.7
) -> Optional[str]:
    """Use embeddings for semantic topic matching."""
    
    # Get embeddings for current topics
    current_text = " ".join(current_topics)
    current_embedding = await self._get_embedding(current_text)
    
    # Get recent analyses
    recent_analyses = await AIAnalytics.objects.filter(...)
    
    for analysis in recent_analyses:
        prev_topics = extract_topics(analysis.summary_data)
        prev_text = " ".join(prev_topics)
        prev_embedding = await self._get_embedding(prev_text)
        
        # Cosine similarity
        similarity = cosine_similarity(current_embedding, prev_embedding)
        
        if similarity >= similarity_threshold:
            logger.info(f"Semantic match: {similarity:.2f} similarity")
            return analysis.topic_chain_id
    
    return None

async def _get_embedding(self, text: str) -> list[float]:
    """Get text embedding from OpenAI."""
    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    response = await client.embeddings.create(
        model="text-embedding-3-small",
        input=text
    )
    return response.data[0].embedding
```

**Преимущества**:
- ✅ Семантическое понимание ("пробки" ≈ "traffic jams")
- ✅ Работает с синонимами
- ✅ Улавливает контекст

**Недостатки**:
- ❌ Дополнительные API calls → стоимость
- ❌ Медленнее чем string matching

#### Вариант B: Adaptive Threshold

```python
def _calculate_match_threshold(self, source: Source, topic_count: int) -> float:
    """Calculate dynamic threshold based on source activity and topic count."""
    
    # More topics = lower threshold (broader matching)
    if topic_count >= 5:
        base_threshold = 0.4
    elif topic_count >= 3:
        base_threshold = 0.5
    else:
        base_threshold = 0.6  # Strict for narrow topics
    
    # Adjust for source activity
    # Active sources (many posts) = stricter threshold
    if source.params.get('posts_per_day', 0) > 100:
        return min(base_threshold + 0.1, 0.8)
    
    return base_threshold
```

#### Вариант C: Weighted Topics

Не все темы равны. Главные темы должны весить больше.

```python
# В JSON schema добавить веса
SCHEMA_FIELDS = {
    'main_topics': {
        'topics': [
            {'name': 'ремонт дорог', 'weight': 0.8},  # Главная тема
            {'name': 'пробки', 'weight': 0.5},        # Второстепенная
            {'name': 'объезды', 'weight': 0.3}        # Упоминание
        ]
    }
}

# При matching учитывать веса
weighted_matches = sum(
    current_weights[topic] * prev_weights.get(topic, 0)
    for topic in current_topics
)
```

### Рекомендация

**Hybrid подход**:
1. **String matching** для быстрого первого прохода (текущий)
2. **Semantic check** для граничных случаев (40-60% overlap)
3. **Adaptive threshold** на основе активности источника

```python
async def _find_matching_topic_chain_hybrid(self, source, current_topics):
    # Quick string matching
    string_match_chain = await self._find_matching_topic_chain(source, current_topics)
    
    if string_match_chain:
        return string_match_chain  # Clear match
    
    # Borderline cases: try semantic
    semantic_chain = await self._find_matching_topic_chain_semantic(
        source, current_topics, threshold=0.7
    )
    
    return semantic_chain
```

---

## 4. Повторные запуски и инкрементальная загрузка

### Текущая реализация: Checkpoint System

**Документация**: `docs/CHECKPOINT_SYSTEM.md`

**Принцип работы**:

1. **Source.last_checked** - timestamp последнего сбора
2. **BotScenario.collection_interval_hours** - как часто собирать
3. **CheckpointManager** - проверяет нужен ли сбор

```python
from app.services.checkpoint_manager import CheckpointManager

# Проверка нужен ли сбор
if CheckpointManager.should_collect(source):
    # Collect new content ONLY
    await collect_and_analyze(source)
else:
    # Skip - checked recently
    pass
```

### VK API: Фильтрация по дате

**Есть ли в VK API фильтрация по дате?** → **ДА**

#### wall.get (посты)

```python
params = {
    'owner_id': '-12345',
    'count': 100,
    'filter': 'owner',
    # КРИТИЧНО: Фильтр по времени
    'start_time': int(last_checked.timestamp()),  # Unix timestamp
    'end_time': int(datetime.now().timestamp())
}
```

**Документация VK**: https://dev.vk.com/ru/method/wall.get

**Параметры**:
- `start_time` (int) - Начало временного интервала (Unix timestamp)
- `end_time` (int) - Конец временного интервала (Unix timestamp)

#### Пример VK запроса

```python
async def collect_vk_posts_incremental(source: Source) -> list[dict]:
    """Collect only NEW VK posts since last_checked."""
    
    # Get checkpoint
    last_checked = source.last_checked or datetime.now(UTC) - timedelta(days=7)
    
    # VK API params with time filter
    params = {
        'owner_id': f"-{source.external_id}",
        'count': 100,
        'filter': 'owner',
        'start_time': int(last_checked.timestamp()),  # Only posts AFTER this
        'access_token': settings.VK_SERVICE_ACCESS_TOKEN,
        'v': '5.199'
    }
    
    # Call VK API
    url = 'https://api.vk.com/method/wall.get'
    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params)
        data = response.json()
    
    posts = data.get('response', {}).get('items', [])
    
    logger.info(f"Collected {len(posts)} NEW posts since {last_checked}")
    
    # Update checkpoint
    await source_manager.update_last_checked(source.id)
    
    return posts
```

### Telegram API: Фильтрация по дате

**Есть ли в Telegram API фильтрация?** → **ДА (через offset_id)**

#### messages.getHistory

Telegram использует **message ID offset**, а не timestamp.

```python
from telethon import TelegramClient

async def collect_telegram_incremental(source: Source) -> list:
    """Collect only NEW Telegram messages."""
    
    # Get last message ID from checkpoint
    last_message_id = source.params.get('last_message_id', 0)
    
    # Telethon client
    client = TelegramClient('session', api_id, api_hash)
    await client.connect()
    
    # Get messages AFTER last_message_id
    messages = await client.get_messages(
        entity=source.external_id,  # Channel/Group ID
        limit=100,
        offset_id=last_message_id,  # Only messages > this ID
        reverse=True  # From old to new
    )
    
    # Save new checkpoint
    if messages:
        new_last_id = messages[-1].id
        await source_manager.update_by_id(
            source.id,
            params={**source.params, 'last_message_id': new_last_id}
        )
    
    logger.info(f"Collected {len(messages)} NEW Telegram messages")
    
    return messages
```

**Документация Telegram**: https://docs.telethon.dev/en/stable/modules/client.html#telethon.client.messages.MessageMethods.get_messages

**Параметры**:
- `offset_id` (int) - Message ID offset (только сообщения > этого ID)
- `limit` (int) - Количество сообщений (макс 100)
- `reverse` (bool) - Порядок (True = от старых к новым)

### Корректность итоговых значений

**Вопрос**: Как корректно агрегировать метрики?

#### Проблема

**Scenario**: 
- День 1: собрали 50 постов, 200 лайков
- День 2: собрали 20 **новых** постов, 80 лайков
- **Итого должно быть**: 70 постов, 280 лайков

**НО** если просто суммировать analyses:
- Анализ 1: 50 постов, 200 лайков
- Анализ 2: 20 постов, 80 лайков
- **Сумма**: 70 постов, 280 лайков ✅

#### Решение: Incremental счетчики

```python
# В content_statistics
{
    'total_posts': 20,  # NEW posts in this analysis
    'total_reactions': 80,  # NEW reactions
    'cumulative_posts': 70,  # Total across all analyses
    'cumulative_reactions': 280  # Total reactions
}
```

**Расчет cumulative**:

```python
async def _calculate_cumulative_stats(self, source: Source, new_stats: dict) -> dict:
    """Calculate cumulative statistics."""
    
    # Get latest analysis
    latest = await AIAnalytics.objects.filter(
        source_id=source.id
    ).order_by(AIAnalytics.analysis_date.desc()).first()
    
    prev_cumulative = {}
    if latest and latest.summary_data:
        prev_stats = latest.summary_data.get('content_statistics', {})
        prev_cumulative = {
            'posts': prev_stats.get('cumulative_posts', 0),
            'reactions': prev_stats.get('cumulative_reactions', 0)
        }
    
    # Add new to cumulative
    return {
        'total_posts': new_stats['total_posts'],  # This analysis
        'total_reactions': new_stats['total_reactions'],
        'cumulative_posts': prev_cumulative['posts'] + new_stats['total_posts'],
        'cumulative_reactions': prev_cumulative['reactions'] + new_stats['total_reactions'],
        'avg_reactions_per_post': (
            prev_cumulative['reactions'] + new_stats['total_reactions']
        ) / (prev_cumulative['posts'] + new_stats['total_posts'])
    }
```

---

## 5. Полный цикл взаимодействия

### Архитектура сбора и анализа

```
┌─────────────────────────────────────────────────────────────┐
│                    ContentScheduler                          │
│  (Runs every N hours via CLI/cron)                          │
└───────────────┬─────────────────────────────────────────────┘
                │
                ▼
┌───────────────────────────────────────────────────────────────┐
│              CheckpointManager                                │
│  • Get sources needing collection                             │
│  • Check last_checked + collection_interval_hours             │
│  • Filter: only sources needing update                        │
└───────────────┬───────────────────────────────────────────────┘
                │
                ▼
┌───────────────────────────────────────────────────────────────┐
│            ContentCollector                                   │
│  • For each source:                                           │
│    - Get SocialClient (VK/Telegram/etc)                       │
│    - Call collect_data()                                      │
└───────────────┬───────────────────────────────────────────────┘
                │
                ▼
┌───────────────────────────────────────────────────────────────┐
│         SocialClient (VKClient/TelegramClient)                │
│  • Build API params with checkpoint filters:                  │
│    - VK: start_time = last_checked.timestamp()               │
│    - Telegram: offset_id = last_message_id                   │
│  • Call platform API                                          │
│  • Return normalized content                                  │
└───────────────┬───────────────────────────────────────────────┘
                │
                ▼
┌───────────────────────────────────────────────────────────────┐
│                  AIAnalyzer                                   │
│  • Classify content by media type                             │
│  • Select LLM providers (via LLMResolver)                     │
│  • Run parallel analyses:                                     │
│    - Text analysis                                            │
│    - Image analysis (if images present)                       │
│    - Video analysis (if videos present)                       │
│  • Create unified summary                                     │
│  • Auto-detect topic chains (string matching)                 │
│  • Save AIAnalytics to DB                                     │
└───────────────┬───────────────────────────────────────────────┘
                │
                ▼
┌───────────────────────────────────────────────────────────────┐
│             CheckpointManager.save()                          │
│  • Update Source.last_checked = now()                         │
│  • Update Source.params (cursors, offsets)                    │
│  • Log collection result                                      │
└───────────────────────────────────────────────────────────────┘
```

### Пример: Полный сценарий VK группы

```python
# 1. SCHEDULER запускается каждый час
python cli/scheduler.py run -i 60

# 2. CheckpointManager проверяет источники
sources_to_collect = await CheckpointManager.get_sources_needing_collection()
# → Source ID 16 (last_checked: 2h ago, interval: 1h) ✅

# 3. ContentCollector инициирует сбор
collector = ContentCollector()
await collector.collect_from_source(source_16)

# 4. VKClient собирает НОВЫЕ посты
vk_client = VKClient(platform)
posts = await vk_client.collect_data(source_16, 'posts')
# API call:
# https://api.vk.com/method/wall.get?owner_id=-16&start_time=1729000000

# VK response: 15 new posts (since last_checked)

# 5. AIAnalyzer анализирует контент
analyzer = AIAnalyzer()
analysis = await analyzer.analyze_content(posts, source_16)

# 5a. Классификация контента
# → 12 text posts, 3 posts with images

# 5b. Выбор LLM providers
# text: GPT-4o-mini ($0.15/1M tokens) - from llm_strategy="cost_efficient"
# image: GPT-4o ($2.50/1M tokens) - for vision

# 5c. Parallel анализ
text_result = await llm.analyze_text(posts)
# Topics: ["благоустройство", "субботник", "парк"]

# 5d. Topic matching
existing_chain = await _find_matching_topic_chain(source_16, topics)
# → Found: chain_16_abc123 (match: 66%)

# 5e. Сохранение
await AIAnalytics.objects.create(
    source_id=16,
    summary_data={
        'analysis_title': 'Субботник в парке',
        'analysis_summary': 'Жители обсуждают организацию субботника...',
        'multi_llm_analysis': {...},
        'content_statistics': {
            'total_posts': 15,
            'total_reactions': 89,
            'cumulative_posts': 150,  # Across all analyses
            'cumulative_reactions': 890
        }
    },
    topic_chain_id='chain_16_abc123',  # Linked to existing chain
    estimated_cost=12  # cents
)

# 6. Checkpoint обновляется
await source_manager.update_last_checked(16, timestamp=now())
# Source.last_checked = 2024-10-18 14:30:00
```

### Оптимизации по нагрузке

#### 1. **Rate Limiting**

```python
# VK: max 3 requests/second
from app.core.rate_limiter import RateLimiter

vk_limiter = RateLimiter(max_calls=3, period=1.0)

async def call_vk_api(params):
    async with vk_limiter:
        response = await httpx.get(url, params=params)
        return response.json()
```

#### 2. **Batch Processing**

```python
# Collect from multiple sources in parallel (with limits)
import asyncio

async def collect_batch(sources: list[Source]):
    # Process 5 sources at a time
    for batch in chunks(sources, 5):
        tasks = [collector.collect_from_source(s) for s in batch]
        await asyncio.gather(*tasks)
        await asyncio.sleep(1)  # Cooldown between batches
```

#### 3. **Content Filtering**

```python
# Don't analyze EVERY post - filter by engagement
def filter_low_engagement(posts: list[dict], min_reactions: int = 5) -> list[dict]:
    """Only analyze posts with sufficient engagement."""
    return [
        p for p in posts 
        if p.get('likes', {}).get('count', 0) + p.get('comments', {}).get('count', 0) >= min_reactions
    ]

# In collector
posts = await vk_client.collect_data(source, 'posts')
filtered = filter_low_engagement(posts, min_reactions=10)
# Analyze only engaged posts → save LLM costs
await analyzer.analyze_content(filtered, source)
```

#### 4. **Smart Collection Intervals**

```python
# Dynamic interval based on source activity
def calculate_optimal_interval(source: Source) -> int:
    """Calculate collection interval based on source activity."""
    
    # Get recent activity
    recent_analytics = await AIAnalytics.objects.filter(
        source_id=source.id
    ).order_by(AIAnalytics.analysis_date.desc()).limit(5)
    
    avg_posts = sum(
        a.summary_data.get('content_statistics', {}).get('total_posts', 0)
        for a in recent_analytics
    ) / len(recent_analytics)
    
    # Active source (many posts) → check often
    if avg_posts > 50:
        return 1  # Every hour
    elif avg_posts > 10:
        return 6  # Every 6 hours
    else:
        return 24  # Once a day
    
# Update scenario
await BotScenario.objects.update_by_id(
    scenario_id,
    collection_interval_hours=calculate_optimal_interval(source)
)
```

---

## Summary: Рекомендации

### Immediate Actions (High Priority)

1. **✅ Даты постов в dashboard**
   - Добавить `content_date_range` в content_statistics
   - Отображать в цепочках вместо дат анализа
   - Показывать `last_checked` отдельно

2. **✅ LEGACY поля**
   - Вариант 2: Миграция с fallback
   - Скрыть из UI
   - Удалить через 2-3 релиза

3. **✅ Topic Matching улучшения**
   - Добавить adaptive threshold
   - Реализовать semantic similarity для граничных случаев
   - Weighted topics в следующей итерации

### Medium Priority

4. **📋 Оптимизация сбора**
   - Проверить все коллекторы используют checkpoint
   - Добавить batch processing для многих источников
   - Реализовать content filtering по engagement

5. **📋 Cumulative statistics**
   - Добавить cumulative счетчики
   - Показывать total vs incremental в dashboard

### Future Enhancements

6. **🔮 Semantic embeddings**
   - OpenAI embeddings для topic matching
   - Cached embeddings для экономии

7. **🔮 Smart intervals**
   - Dynamic collection_interval_hours
   - Auto-adjust на основе активности

---

**Статус**: Анализ завершен  
**Следующий шаг**: Согласовать приоритеты с пользователем
