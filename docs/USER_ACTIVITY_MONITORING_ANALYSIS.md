# User Activity Monitoring & Date Range Control - Analysis & Implementation Plan

**Date**: Current Session  
**Status**: 📋 Analysis & Planning

---

## Вопрос 1: Ограничение по дате получаемого контента

### Текущая ситуация

**Проверка модели Source**:
```python
class Source:
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    last_checked: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)
```

**Что есть**:
- ✅ `last_checked` - checkpoint для инкрементальной загрузки
- ✅ `params` (JSON) - может хранить любые параметры
- ❌ **НЕТ** явных полей `start_date` / `end_date` в UI админки
- ❌ **НЕТ** явных параметров в CLI

### Где можно хранить date_from/date_to

#### Вариант A: В Source.params (рекомендуется)

```python
{
    "collection": {
        "date_from": "2025-01-01T00:00:00Z",  # Начало мониторинга
        "date_to": "2025-12-31T23:59:59Z",    # Конец мониторинга (optional)
        "count": 100,
        "offset": 0
    }
}
```

**Преимущества**:
- Не нужна миграция БД
- Гибкость (JSON)
- Уже используется для других параметров

**Недостатки**:
- Нет валидации на уровне БД
- Сложнее искать по датам (JSON query)

#### Вариант B: Отдельные поля (альтернатива)

```python
class Source:
    monitoring_start_date: Mapped[datetime | None] = Column(DateTime(timezone=True), nullable=True)
    monitoring_end_date: Mapped[datetime | None] = Column(DateTime(timezone=True), nullable=True)
```

**Преимущества**:
- Валидация на уровне БД
- Легко индексировать
- Понятная схема

**Недостатки**:
- Нужна миграция
- Менее гибко

### Реализация

#### 1. Добавить в SourceAdmin форму

**Файл**: `app/admin/views/source.py` (или где определен SourceAdmin)

```python
from sqladmin import ModelView
from wtforms import DateTimeField

class SourceAdmin(ModelView, model=Source):
    # ... existing fields ...
    
    # Custom form fields for date range
    form_overrides = {
        'params': JSONField  # Enable JSON editor
    }
    
    # Add helper text
    column_descriptions = {
        'params': 'JSON configuration. Example: {"collection": {"date_from": "2025-01-01T00:00:00Z", "date_to": null}}'
    }
    
    # Or add separate form fields
    form_args = {
        'monitoring_start': {'label': 'Начало мониторинга', 'description': 'С какой даты собирать контент'},
        'monitoring_end': {'label': 'Конец мониторинга', 'description': 'До какой даты (пусто = до конца)'}
    }
```

#### 2. Обновить VKClient для использования date_from/date_to

**Файл**: `app/services/social/vk_client.py`

```python
def _build_params(self, source: Source, method: str) -> dict:
    """Build VK API request parameters."""
    
    source_params = source.params.get('collection', {}) if source.params else {}
    
    if method in ('wall.get', 'wall.getComments'):
        params_dict = {
            'owner_id': owner_id,
            'count': 100,
        }
        
        # DATE RANGE FILTERING
        # Priority: date_from/date_to > last_checked > no filter
        
        date_from = source_params.get('date_from')  # From params
        date_to = source_params.get('date_to')      # From params
        
        # Parse date_from (ISO string or datetime)
        if date_from:
            if isinstance(date_from, str):
                from datetime import datetime
                date_from = datetime.fromisoformat(date_from.replace('Z', '+00:00'))
            start_time = int(date_from.timestamp())
            params_dict['start_time'] = start_time
            logger.info(f"VK date_from filter: {date_from.isoformat()} (ts: {start_time})")
        
        # If date_from not set, use last_checked as checkpoint
        elif source.last_checked:
            start_time = int(source.last_checked.timestamp())
            params_dict['start_time'] = start_time
            logger.info(f"VK checkpoint: {source.last_checked.isoformat()} (ts: {start_time})")
        
        # Parse date_to (end boundary)
        if date_to:
            if isinstance(date_to, str):
                from datetime import datetime
                date_to = datetime.fromisoformat(date_to.replace('Z', '+00:00'))
            end_time = int(date_to.timestamp())
            params_dict['end_time'] = end_time
            logger.info(f"VK date_to filter: {date_to.isoformat()} (ts: {end_time})")
        
        base_params.update(params_dict)
```

**Логика**:
1. Если `date_from` задан → использовать его
2. Иначе использовать `last_checked` (checkpoint)
3. Если `date_to` задан → ограничить конец периода
4. Иначе собирать до текущего момента

#### 3. CLI поддержка

**Файл**: `cli/scheduler.py` (или отдельная команда)

```python
import click
from datetime import datetime

@click.command()
@click.option('--source-id', type=int, help='Source ID to collect from')
@click.option('--date-from', type=str, help='Start date (ISO format: 2025-01-01T00:00:00Z)')
@click.option('--date-to', type=str, help='End date (ISO format or empty for now)')
async def collect_with_dates(source_id, date_from, date_to):
    """Collect content from source with date range filter."""
    
    # Parse dates
    if date_from:
        date_from_dt = datetime.fromisoformat(date_from.replace('Z', '+00:00'))
    else:
        date_from_dt = None
    
    if date_to:
        date_to_dt = datetime.fromisoformat(date_to.replace('Z', '+00:00'))
    else:
        date_to_dt = None
    
    # Get source
    source = await Source.objects.get(id=source_id)
    
    # Temporarily set date range in params
    if not source.params:
        source.params = {}
    
    source.params['collection'] = {
        **source.params.get('collection', {}),
        'date_from': date_from_dt.isoformat() if date_from_dt else None,
        'date_to': date_to_dt.isoformat() if date_to_dt else None
    }
    
    # Collect
    collector = ContentCollector()
    result = await collector.collect_from_source(source)
    
    logger.info(f"Collected {result['content_count']} items from {date_from or 'start'} to {date_to or 'now'}")
```

**Использование**:
```bash
# Собрать посты за январь 2025
python cli/collect.py --source-id 16 \
    --date-from "2025-01-01T00:00:00Z" \
    --date-to "2025-01-31T23:59:59Z"

# Собрать с определенной даты до сейчас
python cli/collect.py --source-id 16 \
    --date-from "2025-01-01T00:00:00Z"

# Собрать все (без фильтра)
python cli/collect.py --source-id 16
```

#### 4. UI в админке (расширенная форма)

Добавить кастомные поля в SourceAdmin:

```python
from wtforms import Form, DateTimeField
from wtforms.validators import Optional

class SourceForm(Form):
    # ... existing fields ...
    
    monitoring_start = DateTimeField(
        'Начало мониторинга',
        validators=[Optional()],
        description='С какой даты собирать контент. Пусто = с самого начала',
        format='%Y-%m-%d %H:%M:%S'
    )
    
    monitoring_end = DateTimeField(
        'Конец мониторинга', 
        validators=[Optional()],
        description='До какой даты собирать. Пусто = до текущего момента',
        format='%Y-%m-%d %H:%M:%S'
    )

class SourceAdmin(ModelView):
    form = SourceForm
    
    async def on_model_change(self, form, model, is_created):
        """Save date range to params JSON."""
        
        # Extract dates from form
        date_from = form.monitoring_start.data
        date_to = form.monitoring_end.data
        
        # Update params
        if not model.params:
            model.params = {}
        
        if 'collection' not in model.params:
            model.params['collection'] = {}
        
        model.params['collection']['date_from'] = date_from.isoformat() if date_from else None
        model.params['collection']['date_to'] = date_to.isoformat() if date_to else None
```

**Визуально в админке**:
```
Источник: Кигель
Платформа: ВКонтакте
...
Начало мониторинга: [2025-01-01 00:00:00] 📅
Конец мониторинга:  [                   ] 📅 (пусто = до конца)
```

---

## Вопрос 2: Автораскрытие цепочки из 1 записи

### Текущее поведение

Цепочка свёрнута, нужно кликнуть "Показать эволюцию тем" → загрузка данных

### Новое поведение

**Если `analyses_count === 1`** → автоматически развернуть при загрузке

### Реализация

**Файл**: `app/static/js/dashboard.js`

```javascript
buildChainCard(chain, source) {
    // ... existing code ...
    
    const isAutoExpanded = chain.analyses_count === 1
    
    return `
        <div class="chain-item fade-in" id="chain-${chain.chain_id}">
            <!-- ... header ... -->
            
            <div class="mt-3">
                ${chain.analyses_count > 1 ? `
                <!-- Show toggle button only if multiple analyses -->
                <button class="btn btn-sm btn-outline-primary collapse-toggle" 
                        data-bs-toggle="collapse" 
                        data-bs-target="#evolution-${chain.chain_id}"
                        aria-expanded="false">
                    <i class="fas fa-chevron-right me-1"></i>
                    Показать эволюцию тем
                </button>
                ` : ''}
            </div>
            
            <!-- Auto-expand if single analysis -->
            <div class="collapse chain-evolution ${isAutoExpanded ? 'show' : ''}" 
                 id="evolution-${chain.chain_id}">
                <div class="analysis-timeline" id="timeline-${chain.chain_id}">
                    ${isAutoExpanded ? '<!-- Will load immediately -->' : `
                    <div class="text-center py-3">
                        <div class="spinner-border text-primary" role="status">
                            <span class="visually-hidden">Загрузка...</span>
                        </div>
                    </div>
                    `}
                </div>
            </div>
        </div>
    `
}
```

**JavaScript для автозагрузки**:

```javascript
// В topic_chains_dashboard.html или dashboard.js

// After rendering chains
document.querySelectorAll('.collapse-toggle').forEach(btn => {
    btn.addEventListener('click', async function() {
        const chainId = this.dataset.chainId
        const timeline = document.getElementById(`timeline-${chainId}`)
        
        // Load evolution on expand
        if (!timeline.dataset.loaded) {
            await TopicChainUtils.loadEvolution(chainId)
            timeline.dataset.loaded = 'true'
        }
    })
})

// Auto-load for single-analysis chains
document.querySelectorAll('.chain-evolution.show').forEach(async (element) => {
    const chainId = element.id.replace('evolution-', '')
    const timeline = document.getElementById(`timeline-${chainId}`)
    
    if (!timeline.dataset.loaded) {
        await TopicChainUtils.loadEvolution(chainId)
        timeline.dataset.loaded = 'true'
    }
})
```

**Визуально**:

**Многоэлементная цепочка** (свёрнута):
```
✨ Обсуждение дизайна
📅 15 окт - 18 окт | 📊 3 анализа

[Показать эволюцию тем ▶]
```

**Одноэлементная цепочка** (развёрнута сразу):
```
✨ Обсуждение дизайна  
📅 18 окт - 18 окт | 📊 1 анализ

━━━━━━━━━━━━━━━━━━━━━━━━
18 окт, 14:30

💡 Описание
Пользователи обсуждают дизайн...

😊 Смешанный | 📄 67 постов | ❤️ 462 реакций

Дизайн продуктов  UX/UI  Критика
━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Вопрос 3: Сценарий мониторинга активности пользователя

### Требования

**Цель**: Отслеживать активность USER:
1. Посты пользователя в своём аккаунте
2. Комментарии пользователя в других аккаунтах
3. Лайки пользователя

**Результат**: Краткие аннотации по каждому действию

### Анализ архитектуры

#### Текущая структура

**ContentType enum**:
```python
class ContentType(Enum):
    POSTS = ("posts", "Посты", "📝")
    COMMENTS = ("comments", "Комментарии", "💬")
    REACTIONS = ("reactions", "Реакции", "❤️")
```

**SourceType enum**:
```python
class SourceType(Enum):
    USER = ("user", "Пользователь", "👤")
    GROUP = ("group", "Группа", "👥")
    CHANNEL = ("channel", "Канал", "📢")
```

**BotScenario**:
```python
class BotScenario:
    content_types: list[str]  # ["posts", "comments", "reactions"]
    analysis_types: list[str]  # ["sentiment", "topics", "keywords"]
```

**AIAnalytics**:
```python
class AIAnalytics:
    source_id: int
    analysis_date: date        # ONE date per record
    summary_data: dict         # Aggregated data
```

#### Проблема

**Текущая модель** = 1 анализ на дату:
```
source_id=16, analysis_date=2025-10-18 → summary_data с ВСЕМИ событиями за день
```

**Но нужно**: Отдельная запись на каждое событие:
```
source_id=16, event_id=1, event_type=post, event_date=2025-10-18 10:30
source_id=16, event_id=2, event_type=comment, event_date=2025-10-18 11:45
source_id=16, event_id=3, event_type=like, event_date=2025-10-18 14:20
```

### Решения

#### Вариант A: Новая таблица UserActivity (рекомендуется для детальной активности)

**Создать**:
```python
class UserActivity(Base, TimestampMixin):
    """Individual user activity events."""
    
    __tablename__ = 'user_activities'
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("social_manager.sources.id"))
    
    # Event details
    event_type: Mapped[str] = mapped_column(String(50))  # "post", "comment", "like"
    event_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    event_external_id: Mapped[str] = mapped_column(String(255))  # Platform event ID
    
    # Target info (where action happened)
    target_type: Mapped[str | None] = mapped_column(String(50), nullable=True)  # "post", "group", "user"
    target_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    
    # Event content
    content: Mapped[dict] = mapped_column(JSON)  # Raw event data
    
    # AI Analysis
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)  # Brief annotation
    sentiment: Mapped[float | None] = mapped_column(Float, nullable=True)
    keywords: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    
    # Relationships
    source: Mapped["Source"] = relationship("Source", back_populates="activities")
```

**Пример данных**:
```json
{
  "id": 1,
  "source_id": 16,
  "event_type": "comment",
  "event_date": "2025-10-18T11:45:00Z",
  "event_external_id": "comment_12345",
  "target_type": "post",
  "target_id": "-98765_54321",
  "target_url": "https://vk.com/wall-98765_54321",
  "content": {
    "text": "Отличный дизайн!",
    "likes": 5
  },
  "ai_summary": "Пользователь положительно отозвался о дизайне в посте сообщества DesignHub",
  "sentiment": 0.8,
  "keywords": ["дизайн", "положительный отзыв"]
}
```

**Преимущества**:
- ✅ Детальная информация о каждом событии
- ✅ Легко фильтровать по типу события
- ✅ Можно построить timeline активности
- ✅ Простая структура данных

**Недостатки**:
- ❌ Новая таблица → миграция
- ❌ Много записей (масштабирование)
- ❌ Дублирование с AIAnalytics

#### Вариант B: Расширить AIAnalytics (проще, использовать существующую)

**Изменить логику**:
- `analysis_date` = дата события (не дата анализа)
- `summary_data` = данные одного события
- Добавить флаг `is_event_based = True`

**Пример**:
```python
# AIAnalytics record for single event
{
  "id": 90,
  "source_id": 16,
  "analysis_date": "2025-10-18",  # Event date
  "summary_data": {
    "event_type": "comment",
    "event_time": "2025-10-18T11:45:00Z",
    "event_id": "comment_12345",
    "target": {
      "type": "post",
      "id": "-98765_54321",
      "url": "https://vk.com/wall-98765_54321"
    },
    "content": {
      "text": "Отличный дизайн!",
      "likes": 5
    },
    "analysis_summary": "Пользователь положительно отозвался о дизайне...",
    "sentiment_score": 0.8,
    "keywords": ["дизайн", "положительный отзыв"]
  }
}
```

**Преимущества**:
- ✅ Нет новой таблицы
- ✅ Переиспользование AIAnalytics
- ✅ Совместимо с существующей логикой

**Недостатки**:
- ❌ Смешение агрегированных и event-based данных
- ❌ Нужен флаг для различения типов

#### Вариант C: Гибридный (агрегация в summary_data)

**Один анализ на день** с детализацией событий:

```json
{
  "analysis_date": "2025-10-18",
  "summary_data": {
    "analysis_title": "Активность пользователя за 18 октября",
    "analysis_summary": "Пользователь опубликовал 2 поста, оставил 5 комментариев и поставил 15 лайков",
    "events": [
      {
        "type": "post",
        "time": "2025-10-18T10:30:00Z",
        "id": "post_123",
        "content": "Новый дизайн готов!",
        "annotation": "Пользователь поделился результатами работы над дизайн-проектом",
        "sentiment": 0.7
      },
      {
        "type": "comment",
        "time": "2025-10-18T11:45:00Z",
        "id": "comment_456",
        "target": "post -98765_54321",
        "content": "Отличный дизайн!",
        "annotation": "Положительный отзыв о дизайне в сообществе DesignHub",
        "sentiment": 0.8
      }
    ],
    "statistics": {
      "total_posts": 2,
      "total_comments": 5,
      "total_likes": 15,
      "avg_sentiment": 0.75
    }
  }
}
```

**Преимущества**:
- ✅ Используется существующая структура
- ✅ Группировка по дате (меньше записей)
- ✅ Детализация доступна в events[]

**Недостатки**:
- ❌ JSON query для поиска конкретных событий
- ❌ Сложнее извлекать отдельные события

### Рекомендация: Вариант C (Гибридный)

**Почему**:
1. Не требует новой таблицы
2. Совместим с текущей архитектурой
3. Поддерживает оба режима (агрегация + детали)
4. Управляется через промпт

### Реализация Варианта C

#### 1. Обновить Prompt для event-based анализа

**Пример промпта**:

```python
USER_ACTIVITY_PROMPT = """
Проанализируй активность пользователя за период.

Собранные данные:
{content}

Для КАЖДОГО события (пост, комментарий, лайк) создай краткую аннотацию:
- Что сделал пользователь
- Где (в каком сообществе/посте)
- Тональность действия
- Ключевые темы

Верни JSON:
{{
  "analysis_title": "Активность пользователя за {date}",
  "analysis_summary": "Краткое общее описание активности за день",
  "events": [
    {{
      "type": "post|comment|like",
      "time": "ISO datetime",
      "event_id": "platform event ID",
      "target": "где произошло (сообщество/пост)",
      "content_preview": "краткий текст (до 100 символов)",
      "annotation": "краткая аннотация действия (1-2 предложения)",
      "sentiment": 0.0-1.0,
      "keywords": ["ключ1", "ключ2"]
    }}
  ],
  "statistics": {{
    "total_posts": N,
    "total_comments": N,
    "total_likes": N,
    "avg_sentiment": 0.0-1.0
  }}
}}
"""
```

#### 2. Создать специальный BotScenario

```python
# В админке создать сценарий "Мониторинг активности пользователя"

name = "Мониторинг активности пользователя"
description = "Отслеживает все действия пользователя: посты, комментарии, лайки"

# Content types
content_types = ["posts", "comments", "reactions"]

# Analysis types
analysis_types = ["sentiment", "keywords", "user_activity"]  # NEW: user_activity

# Custom prompt
text_prompt = USER_ACTIVITY_PROMPT

# Scope (optional configuration)
scope = {
    "event_based": True,  # Flag for event-based analysis
    "max_events_per_analysis": 100,
    "include_target_info": True
}
```

#### 3. Обновить Analyzer для event-based режима

**Файл**: `app/services/ai/analyzer.py`

```python
async def analyze_content(self, content, source, topic_chain_id=None):
    """Analyze content - supports both aggregated and event-based modes."""
    
    # Check if event-based mode
    bot_scenario = await self._get_bot_scenario(source)
    is_event_based = False
    
    if bot_scenario and bot_scenario.scope:
        is_event_based = bot_scenario.scope.get('event_based', False)
    
    if is_event_based:
        # EVENT-BASED MODE: Detailed per-event analysis
        return await self._analyze_events(content, source, bot_scenario)
    else:
        # AGGREGATED MODE: Single summary (existing logic)
        return await self._analyze_aggregated(content, source, bot_scenario)

async def _analyze_events(self, content, source, bot_scenario):
    """Analyze each event individually with annotations."""
    
    # Format content for prompt
    events_text = self._format_events_for_prompt(content)
    
    # Build prompt with events
    prompt = bot_scenario.text_prompt or DEFAULT_USER_ACTIVITY_PROMPT
    prompt_filled = prompt.format(
        content=events_text,
        date=date.today().isoformat()
    )
    
    # Call LLM
    result = await self._call_llm(prompt_filled, source)
    
    # Parse response
    parsed = result.get('parsed', {})
    
    # Save to AIAnalytics with detailed events
    return await self._save_analysis(
        analysis_results={'user_activity': result},
        unified_summary=None,
        source=source,
        content_stats={
            'total_events': len(content),
            'event_types': self._count_event_types(content)
        },
        platform_name=await self._get_platform_name(source),
        bot_scenario=bot_scenario
    )

def _format_events_for_prompt(self, content):
    """Format events for LLM prompt."""
    
    events_list = []
    for i, item in enumerate(content, 1):
        event_type = item.get('event_type', 'unknown')
        event_time = item.get('date', 'unknown')
        event_text = item.get('text', '')
        target = item.get('target', {})
        
        events_list.append(
            f"{i}. [{event_type.upper()}] {event_time}\n"
            f"   Цель: {target.get('name', 'N/A')}\n"
            f"   Текст: {event_text[:200]}\n"
        )
    
    return "\n".join(events_list)
```

#### 4. VK API для сбора активности пользователя

**Расширить VKClient**:

```python
class VKClient(BaseClient):
    
    async def collect_user_activity(self, source: Source) -> list[dict]:
        """Collect all user activities: posts + comments + likes."""
        
        activities = []
        
        # 1. User's own posts
        posts = await self._collect_user_posts(source)
        activities.extend(posts)
        
        # 2. User's comments in other groups/posts
        comments = await self._collect_user_comments(source)
        activities.extend(comments)
        
        # 3. User's likes (if accessible)
        # Note: VK API has limited access to likes
        # May need user token with specific permissions
        
        # Sort by date
        activities.sort(key=lambda x: x.get('date', 0), reverse=True)
        
        return activities
    
    async def _collect_user_posts(self, source: Source) -> list[dict]:
        """Get posts from user's wall."""
        
        params = {
            'owner_id': source.external_id,  # User ID
            'count': 100,
            'filter': 'owner'
        }
        
        response = await self._request('wall.get', params)
        posts = response.get('items', [])
        
        # Normalize
        normalized = []
        for post in posts:
            normalized.append({
                'event_type': 'post',
                'event_id': f"post_{post['id']}",
                'date': datetime.fromtimestamp(post['date']),
                'text': post.get('text', ''),
                'likes': post.get('likes', {}).get('count', 0),
                'target': {
                    'type': 'own_wall',
                    'id': source.external_id
                }
            })
        
        return normalized
    
    async def _collect_user_comments(self, source: Source) -> list[dict]:
        """Get user's comments (requires search or subscriptions)."""
        
        # VK limitation: No direct "get all user comments" method
        # Options:
        # 1. newsfeed.search with from_id filter
        # 2. Track specific groups/pages where user comments
        # 3. Use execute API with multiple requests
        
        # Example using newsfeed.search
        params = {
            'q': f'@id{source.external_id}',  # Search mentions
            'count': 100
        }
        
        response = await self._request('newsfeed.search', params)
        items = response.get('items', [])
        
        # Filter comments by user
        comments = []
        for item in items:
            if item.get('from_id') == int(source.external_id):
                comments.append({
                    'event_type': 'comment',
                    'event_id': f"comment_{item['id']}",
                    'date': datetime.fromtimestamp(item['date']),
                    'text': item.get('text', ''),
                    'target': {
                        'type': 'post',
                        'id': f"{item.get('owner_id')}_{item.get('post_id')}",
                        'url': f"https://vk.com/wall{item.get('owner_id')}_{item.get('post_id')}"
                    }
                })
        
        return comments
```

#### 5. Dashboard для отображения событий

**Расширить evolution timeline**:

```javascript
buildEvolutionTimeline(evolution) {
    return evolution.map(item => {
        const events = item.events || []  // NEW: events array
        
        if (events.length > 0) {
            // Event-based display
            return `
                <div class="timeline-item">
                    <div class="timeline-date">
                        ${DashboardUtils.formatDateTime(item.analysis_date)}
                    </div>
                    <div class="timeline-content">
                        <h6>${item.analysis_title || 'Активность пользователя'}</h6>
                        <p class="text-muted">${item.analysis_summary}</p>
                        
                        <!-- Events list -->
                        <div class="events-list mt-3">
                            ${events.map(event => `
                                <div class="event-item border-start border-3 ps-3 mb-2">
                                    <div class="d-flex justify-content-between">
                                        <strong>${this.getEventIcon(event.type)} ${event.type}</strong>
                                        <small class="text-muted">${DashboardUtils.formatDateTime(event.time)}</small>
                                    </div>
                                    <p class="mb-1">${event.annotation}</p>
                                    ${event.target ? `
                                    <small class="text-muted">
                                        <i class="fas fa-external-link-alt"></i> ${event.target}
                                    </small>
                                    ` : ''}
                                </div>
                            `).join('')}
                        </div>
                    </div>
                </div>
            `
        } else {
            // Fallback to standard display
            return this.buildStandardTimelineItem(item)
        }
    }).join('')
}

getEventIcon(eventType) {
    const icons = {
        'post': '📝',
        'comment': '💬',
        'like': '❤️',
        'share': '🔄'
    }
    return icons[eventType] || '📌'
}
```

**Визуально**:
```
━━━━━━━━━━━━━━━━━━━━━━━━
18 окт, 2025

Активность пользователя за 18 октября

Пользователь опубликовал 2 поста, оставил 5 комментариев и поставил 15 лайков

┃ 📝 post                    10:30
┃ Пользователь поделился результатами 
┃ работы над дизайн-проектом
┃ 🔗 Своя стена

┃ 💬 comment                 11:45
┃ Положительный отзыв о дизайне 
┃ в сообществе DesignHub
┃ 🔗 https://vk.com/wall-98765_54321

┃ ❤️ like                    14:20
┃ Оценил пост о новых трендах в UX
┃ 🔗 https://vk.com/wall-12345_6789

━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Итоговая рекомендация

### Вопрос 1: Date Range ✅

**Реализовать**:
1. Использовать `Source.params.collection.date_from` / `date_to`
2. Добавить поля в SourceAdmin (UI)
3. CLI опции `--date-from` / `--date-to`
4. Обновить VKClient для поддержки

**Приоритет**: High

### Вопрос 2: Автораскрытие ✅

**Реализовать**:
- JavaScript check `if (analyses_count === 1) → auto-expand`
- Скрыть кнопку "Показать эволюцию" если 1 запись

**Приоритет**: Medium (простое изменение)

### Вопрос 3: User Activity Monitoring 🎯

**Выбрать**: **Вариант C (Гибридный)**

**Реализовать**:
1. Новый analysis_type: `user_activity`
2. Custom prompt для event-based анализа
3. Структура `summary_data.events[]` для детализации
4. VKClient расширение для сбора активности
5. Dashboard отображение событий

**Управление через промпт**: ✅ Да!
- Промпт указывает: "Для КАЖДОГО события создай аннотацию"
- Scope: `{"event_based": true}`
- AI вернёт структурированный JSON с events[]

**Приоритет**: High (требует расширения архитектуры)

---

**Status**: 📋 Plan Ready  
**Next Step**: Согласовать приоритеты и начать имплементацию

**Author**: Factory Droid
