# Реализация улучшений UX админки и dashboard

## ✅ Что уже сделано

### 1. Модель BotScenario обновлена
- ✅ Добавлены поля `response_format_config` и `output_display_config`
- ✅ Создана миграция `0036_add_json_schema_fields.py`

### 2. Display Helper создан
- ✅ `app/services/display_helpers.py` с RichDisplayHelper
- ✅ Поддержка sentiment emoji, keywords badges, source links, trigger reasons

### 3. Документация
- ✅ План улучшений `docs/UX_IMPROVEMENTS_PLAN.md`

## 🔄 Что нужно доработать

### 1. Запустить миграцию
```bash
cd /Users/admin/Projects/social-media-ai
python3 -m alembic upgrade head
```

### 2. Обновить админку BotScenario

В `app/admin/views.py` добавить в `BotScenarioAdmin`:

```python
form_args = {
    # ... existing args ...
    
    'response_format_config': {
        'description': (
            'Конфигурация формата ответа от LLM. '
            'mode: "events" (по событиям) или "topics" (по темам). '
            'Пример для events: {"mode": "events", "max_events": 50}'
        )
    },
    'output_display_config': {
        'description': (
            'Настройки отображения в dashboard. '
            'show_sentiment_emoji: эмодзи для настроения, '
            'show_keywords: отображать ключевые слова, '
            'show_source_links: ссылки на источники. '
            'Пример: {"show_sentiment_emoji": true, "show_keywords": true}'
        )
    },
    'scope': {
        'description': (
            'Дополнительные параметры для анализа. '
            'event_based: true - анализ по дням (для мониторинга активности), '
            'event_based: false - анализ по темам (ИИ сам определяет темы). '
            'Пример: {"event_based": true, "max_events_per_analysis": 50}'
        )
    }
}

form_widget_args = {
    # ... existing args ...
    
    'response_format_config': {
        'rows': 6,
        'placeholder': '{\n  "mode": "events",\n  "max_events": 50\n}'
    },
    'output_display_config': {
        'rows': 6,
        'placeholder': '{\n  "show_sentiment_emoji": true,\n  "show_keywords": true,\n  "show_source_links": true\n}'
    }
}
```

### 3. Интегрировать RichDisplayHelper в dashboard

В файле с роутами dashboard (создать если нет) `app/routes/dashboard.py`:

```python
from app.services.display_helpers import RichDisplayHelper

@router.get("/topic-chains")
async def get_topic_chains():
    # ... get analytics ...
    
    # Format for rich display
    formatted_data = []
    for analytics in analytics_list:
        # Get display config from scenario
        display_config = analytics.source.bot_scenario.output_display_config if analytics.source.bot_scenario else {}
        
        # Format with helper
        formatted = RichDisplayHelper.format_analysis_for_display(
            analytics,
            display_config
        )
        formatted_data.append(formatted)
    
    return formatted_data
```

### 4. Обновить шаблон dashboard

В HTML шаблоне dashboard добавить:

```html
<!-- Sentiment with emoji -->
{% if item.sentiment %}
<div class="sentiment">
    {{ item.sentiment | safe }}
</div>
{% endif %}

<!-- Keywords -->
{% if item.keywords %}
<div class="keywords">
    {% for keyword in item.keywords %}
    <span class="{{ keyword.class }}">{{ keyword.text }}</span>
    {% endfor %}
</div>
{% endif %}

<!-- Source links -->
{% if item.source_links %}
<div class="sources">
    {% for link in item.source_links %}
    <a href="{{ link.url }}" target="_blank" class="source-link">
        <i class="{{ link.icon }}"></i> {{ link.text }}
    </a>
    {% endfor %}
</div>
{% endif %}

<!-- Trigger reason -->
{% if item.trigger_info %}
<div class="{{ item.trigger_info.class }}">
    <i class="{{ item.trigger_info.icon }}"></i>
    {{ item.trigger_info.reason }}
</div>
{% endif %}
```

## 📝 Примеры конфигурации для админки

### Scenario #10 (User Activity - Event Based)

**Scope:**
```json
{
  "event_based": true,
  "max_events_per_analysis": 50,
  "include_target_info": true,
  "sentiment": {
    "categories": ["Позитивный", "Негативный", "Нейтральный", "Смешанный"]
  },
  "keywords": {
    "max_keywords": 20
  }
}
```

**response_format_config:**
```json
{
  "mode": "events",
  "max_events": 50
}
```

**output_display_config:**
```json
{
  "show_sentiment_emoji": true,
  "show_keywords": true,
  "show_source_links": true,
  "show_trigger_reason": false,
  "group_by": "date"
}
```

### Новый сценарий: Topic Detection (Topic Based)

**Scope:**
```json
{
  "event_based": false,
  "brand_name": "Мой бренд",
  "competitors": ["Конкурент 1", "Конкурент 2"],
  "sentiment": {
    "categories": ["Позитивный", "Негативный", "Нейтральный"]
  }
}
```

**response_format_config:**
```json
{
  "mode": "topics",
  "auto_link_topics": true,
  "similarity_threshold": 0.7
}
```

**output_display_config:**
```json
{
  "show_sentiment_emoji": true,
  "show_keywords": true,
  "show_source_links": true,
  "show_related_topics": true,
  "group_by": "topic"
}
```

## 🎯 Для реализации topic-based режима

В `app/services/ai/analyzer.py` нужно добавить:

```python
async def analyze_content_by_topics(
    self,
    content: list[dict],
    source: Source,
) -> list[AIAnalytics]:
    """
    Analyze content and auto-detect topics.
    ИИ сам определяет темы и связывает с существующими цепочками.
    """
    # Analyze all content together
    analysis = await self.analyze_content(
        content=content,
        source=source
    )
    
    if not analysis:
        return []
    
    # Extract topic info from analysis
    text_analysis = analysis.summary_data.get('multi_llm_analysis', {}).get('text_analysis', {})
    topic_title = text_analysis.get('topic_title') or text_analysis.get('analysis_title')
    related_keywords = text_analysis.get('related_keywords', [])
    is_new_topic = text_analysis.get('is_new_topic', True)
    
    # Search for existing topic chains
    if not is_new_topic and topic_title:
        # Find similar topics by title and keywords
        existing_chain = await self._find_similar_topic_chain(
            source=source,
            topic_title=topic_title,
            keywords=related_keywords
        )
        
        if existing_chain:
            # Link to existing chain
            analysis.topic_chain_id = existing_chain.topic_chain_id
            await analysis.save()
            logger.info(f"Linked to existing topic chain: {existing_chain.topic_chain_id}")
    
    return [analysis]
```

## 🎨 CSS для dashboard

```css
/* Sentiment */
.sentiment {
    font-size: 1.2em;
    margin-bottom: 10px;
}

/* Keywords badges */
.keywords {
    margin: 10px 0;
}

.keywords .badge {
    margin-right: 5px;
    margin-bottom: 5px;
}

/* Source links */
.sources {
    margin: 10px 0;
}

.source-link {
    display: inline-block;
    margin-right: 10px;
    color: #0066cc;
    text-decoration: none;
}

.source-link:hover {
    text-decoration: underline;
}

/* Trigger info */
.trigger-info {
    padding: 10px;
    border-left: 4px solid #17a2b8;
    background: #e7f7f9;
    margin: 10px 0;
}
```

## 📋 Checklist для полной реализации

- [x] Модель обновлена
- [x] Миграция создана
- [x] Display Helper создан
- [ ] Миграция запущена
- [ ] Админка обновлена с descriptions
- [ ] Dashboard routes обновлены
- [ ] HTML шаблоны обновлены
- [ ] CSS добавлен
- [ ] Topic-based режим реализован
- [ ] Протестированы оба режима

---

**Автор:** Factory Droid  
**Дата:** 19 октября 2025  
**Статус:** Основа создана, требуется интеграция
