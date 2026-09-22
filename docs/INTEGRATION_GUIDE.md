# Руководство по интеграции улучшений

## 1. Интеграция RichDisplayHelper в Dashboard

### Шаг 1: Добавить импорт в dashboard.py

В файле `app/api/v1/endpoints/dashboard.py` добавить:

```python
from app.services.display_helpers import RichDisplayHelper
```

### Шаг 2: Обновить эндпоинт для получения chain details

Заменить в `get_topic_chain_details`:

```python
@router.get("/topic-chains/{chain_id}", response_model=dict)
async def get_topic_chain_details(
    chain_id: str,
):
    """Получить детальную информацию о цепочке тем с rich display."""
    
    # Получить все аналитики для цепочки
    analytics = await AIAnalytics.objects.filter(
        topic_chain_id=chain_id
    ).order_by(AIAnalytics.analysis_date.asc())

    if not analytics:
        raise HTTPException(status_code=404, detail="Topic chain not found")

    # Получить источник и сценарий
    source = await Source.objects.select_related(Source.platform).get(
        id=analytics[0].source_id
    )
    
    # Получить display config из scenario
    scenario = await BotScenario.objects.get(id=source.bot_scenario_id) if source.bot_scenario_id else None
    display_config = scenario.output_display_config if scenario else {}
    
    # Форматировать каждую запись с RichDisplayHelper
    formatted_analytics = []
    for analytics_record in analytics:
        formatted = RichDisplayHelper.format_analysis_for_display(
            analytics_record,
            display_config
        )
        formatted_analytics.append(formatted)
    
    # Получить данные цепочки
    chain_data = topic_chain_service.build_topic_chain(analytics)
    
    return {
        "chain_id": chain_id,
        "source": {
            "id": source.id,
            "name": source.name,
            "platform": source.platform.name if source.platform else "unknown"
        },
        "analytics": formatted_analytics,  # С rich display!
        "chain_data": chain_data.get(chain_id, {}),
        "total_analyses": len(analytics)
    }
```

### Шаг 3: Добавить новый эндпоинт для получения formatted analytics

```python
@router.get("/analytics/{analytics_id}/formatted", response_model=dict)
async def get_formatted_analytics(
    analytics_id: int,
):
    """
    Получить одну запись analytics с rich formatting.
    
    Returns:
        Formatted analytics data with emoji, keywords, links, etc.
    """
    
    analytics = await AIAnalytics.objects.get(id=analytics_id)
    if not analytics:
        raise HTTPException(status_code=404, detail="Analytics not found")
    
    # Get source and scenario for display config
    source = await Source.objects.select_related(Source.platform).get(
        id=analytics.source_id
    )
    
    scenario = None
    if source.bot_scenario_id:
        scenario = await BotScenario.objects.get(id=source.bot_scenario_id)
    
    display_config = scenario.output_display_config if scenario else {}
    
    # Format with RichDisplayHelper
    formatted = RichDisplayHelper.format_analysis_for_display(
        analytics,
        display_config
    )
    
    return {
        "id": analytics.id,
        "source_id": analytics.source_id,
        "source_name": source.name,
        "platform": source.platform.name if source.platform else "unknown",
        **formatted
    }
```

---

## 2. Реализация topic-based режима

### analyzer.py - добавить метод analyze_content_by_topics

```python
async def analyze_content_by_topics(
    self,
    content: list[dict],
    source: Source,
) -> list[AIAnalytics]:
    """
    Analyze content and auto-detect topics.
    ИИ сам определяет темы и связывает с существующими цепочками.
    
    Args:
        content: List of content items
        source: Source model instance
    
    Returns:
        List of AIAnalytics records (обычно 1, но может быть несколько если обнаружены разные темы)
    """
    logger.info(f"Starting topic-based analysis for source {source.id}")
    
    # Analyze all content together
    analysis = await self.analyze_content(
        content=content,
        source=source,
        analysis_date=datetime.now(UTC).date()
    )
    
    if not analysis:
        logger.warning("Analysis returned None")
        return []
    
    # Extract topic info from LLM response
    text_analysis = analysis.summary_data.get('multi_llm_analysis', {}).get('text_analysis', {})
    
    # Get topic title (может быть в разных полях)
    topic_title = (
        text_analysis.get('topic_title') or 
        text_analysis.get('analysis_title') or
        'Общая тема'
    )
    
    # Get related keywords for similarity search
    related_keywords = text_analysis.get('related_keywords') or text_analysis.get('keywords', [])
    
    # Check if LLM marked this as new topic
    is_new_topic = text_analysis.get('is_new_topic', True)
    
    logger.info(f"Topic detected: {topic_title}, is_new: {is_new_topic}, keywords: {related_keywords[:5]}")
    
    # Search for existing topic chains if not explicitly new
    if not is_new_topic and topic_title and related_keywords:
        existing_chain = await self._find_similar_topic_chain(
            source=source,
            topic_title=topic_title,
            keywords=related_keywords,
            similarity_threshold=0.7  # Можно настраивать в scope
        )
        
        if existing_chain:
            # Link to existing chain
            analysis.topic_chain_id = existing_chain.topic_chain_id
            await analysis.save()
            logger.info(f"Linked to existing topic chain: {existing_chain.topic_chain_id}")
        else:
            # Create new chain ID
            import hashlib
            chain_id = hashlib.md5(
                f"{source.id}_{topic_title}_{datetime.now(UTC).isoformat()}".encode()
            ).hexdigest()[:16]
            analysis.topic_chain_id = chain_id
            await analysis.save()
            logger.info(f"Created new topic chain: {chain_id}")
    else:
        # Always new topic - create new chain
        import hashlib
        chain_id = hashlib.md5(
            f"{source.id}_{topic_title}_{datetime.now(UTC).isoformat()}".encode()
        ).hexdigest()[:16]
        analysis.topic_chain_id = chain_id
        await analysis.save()
        logger.info(f"Created new topic chain (forced): {chain_id}")
    
    return [analysis]


async def _find_similar_topic_chain(
    self,
    source: Source,
    topic_title: str,
    keywords: list[str],
    similarity_threshold: float = 0.7
) -> Optional[AIAnalytics]:
    """
    Find existing topic chain that's similar to current topic.
    
    Args:
        source: Source instance
        topic_title: Title of current topic
        keywords: Keywords from current analysis
        similarity_threshold: Minimum similarity score (0.0-1.0)
    
    Returns:
        AIAnalytics record from similar chain or None
    """
    # Get recent analyses from same source (last 30 days)
    cutoff_date = datetime.now(UTC) - timedelta(days=30)
    recent_analyses = await AIAnalytics.objects.filter(
        source_id=source.id,
        analysis_date__gte=cutoff_date.date()
    ).order_by(AIAnalytics.analysis_date.desc())
    
    if not recent_analyses:
        return None
    
    # Simple similarity: check title and keyword overlap
    best_match = None
    best_score = 0.0
    
    for analysis in recent_analyses:
        if not analysis.summary_data:
            continue
        
        text_analysis = analysis.summary_data.get('multi_llm_analysis', {}).get('text_analysis', {})
        existing_title = text_analysis.get('topic_title') or text_analysis.get('analysis_title', '')
        existing_keywords = text_analysis.get('keywords', [])
        
        # Calculate similarity
        # 1. Title similarity (простой string match)
        title_sim = 0.0
        if existing_title and topic_title:
            # Нормализация для сравнения
            norm_existing = existing_title.lower().strip()
            norm_current = topic_title.lower().strip()
            
            # Exact match
            if norm_existing == norm_current:
                title_sim = 1.0
            # Contains match
            elif norm_current in norm_existing or norm_existing in norm_current:
                title_sim = 0.8
            # Word overlap
            else:
                existing_words = set(norm_existing.split())
                current_words = set(norm_current.split())
                if existing_words and current_words:
                    overlap = len(existing_words & current_words)
                    total = len(existing_words | current_words)
                    title_sim = overlap / total if total > 0 else 0.0
        
        # 2. Keywords similarity (Jaccard coefficient)
        keyword_sim = 0.0
        if existing_keywords and keywords:
            existing_set = set(k.lower() for k in existing_keywords)
            current_set = set(k.lower() for k in keywords)
            
            overlap = len(existing_set & current_set)
            total = len(existing_set | current_set)
            keyword_sim = overlap / total if total > 0 else 0.0
        
        # Combined similarity (weighted average)
        combined_sim = (title_sim * 0.6) + (keyword_sim * 0.4)
        
        if combined_sim > best_score:
            best_score = combined_sim
            best_match = analysis
    
    # Return best match if above threshold
    if best_score >= similarity_threshold:
        logger.info(f"Found similar topic with score {best_score:.2f}")
        return best_match
    
    logger.info(f"No similar topic found (best score: {best_score:.2f})")
    return None
```

### scheduler.py - использовать topic-based для сценариев

Обновить логику в `run_scheduler_once`:

```python
# Определить режим на основе scope
scope = scenario.scope or {}
is_event_based = scope.get('event_based', True)  # Default = True для обратной совместимости

if is_event_based:
	# Event-based: анализ по дням
	logger.info(f"Using event-based mode (by days) for scenario {scenario.id}")
	new_analytics = await analyzer._analyze_content_by_days(
		content=content,
		source=source
	)
else:
	# Topic-based: ИИ определяет темы
	logger.info(f"Using topic-based mode (auto-detect) for scenario {scenario.id}")
	new_analytics = await analyzer.analyze_content_by_topics(
		content=content,
		source=source
	)
```

---

## 3. HTML Templates (примеры)

### dashboard_chain_detail.html

```html
{% extends "base.html" %}

{% block content %}
<div class="chain-detail">
    <h2>{{ chain_data.title }}</h2>
    
    <div class="chain-source">
        <strong>Источник:</strong> {{ source.name }} ({{ source.platform }})
    </div>
    
    <div class="analytics-list">
        {% for item in analytics %}
        <div class="analytics-card">
            <!-- Title with date -->
            <h3>{{ item.title }}</h3>
            <p class="date">{{ item.date }}</p>
            
            <!-- Sentiment with emoji -->
            {% if item.sentiment %}
            <div class="sentiment">
                {{ item.sentiment }}
            </div>
            {% endif %}
            
            <!-- Summary -->
            <p class="summary">{{ item.summary }}</p>
            
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
                <strong>Источники:</strong>
                {% for link in item.source_links %}
                <a href="{{ link.url }}" target="_blank" class="source-link">
                    <i class="{{ link.icon }}"></i> {{ link.text }}
                </a>
                {% endfor %}
            </div>
            {% endif %}
            
            <!-- Trigger info -->
            {% if item.trigger_info %}
            <div class="{{ item.trigger_info.class }}">
                <i class="{{ item.trigger_info.icon }}"></i>
                {{ item.trigger_info.reason }}
            </div>
            {% endif %}
            
            <!-- Stats -->
            {% if item.stats %}
            <div class="stats">
                <span>📝 Постов: {{ item.stats.posts }}</span>
                <span>❤️ Реакций: {{ item.stats.reactions }}</span>
                <span>💬 Комментариев: {{ item.stats.comments }}</span>
            </div>
            {% endif %}
        </div>
        {% endfor %}
    </div>
</div>
{% endblock %}
```

---

## 4. CSS Styles

```css
/* Analytics Card */
.analytics-card {
    background: #fff;
    border: 1px solid #e0e0e0;
    border-radius: 8px;
    padding: 20px;
    margin-bottom: 20px;
    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}

.analytics-card h3 {
    margin-top: 0;
    color: #333;
}

.analytics-card .date {
    color: #666;
    font-size: 0.9em;
    margin-bottom: 10px;
}

/* Sentiment */
.sentiment {
    font-size: 1.2em;
    margin: 10px 0;
    padding: 8px 12px;
    background: #f5f5f5;
    border-radius: 4px;
    display: inline-block;
}

/* Keywords */
.keywords {
    margin: 15px 0;
}

.keywords .badge {
    display: inline-block;
    padding: 4px 10px;
    margin: 0 5px 5px 0;
    border-radius: 12px;
    font-size: 0.85em;
}

.badge-primary {
    background: #007bff;
    color: white;
}

.badge-secondary {
    background: #6c757d;
    color: white;
}

/* Source Links */
.sources {
    margin: 15px 0;
    padding: 10px;
    background: #f8f9fa;
    border-left: 4px solid #007bff;
}

.source-link {
    display: inline-block;
    margin-right: 15px;
    color: #007bff;
    text-decoration: none;
}

.source-link:hover {
    text-decoration: underline;
}

.source-link i {
    margin-right: 5px;
}

/* Trigger Info */
.trigger-info {
    padding: 12px;
    margin: 15px 0;
    border-left: 4px solid #17a2b8;
    background: #e7f7f9;
    border-radius: 4px;
}

.trigger-info i {
    margin-right: 8px;
    color: #17a2b8;
}

/* Stats */
.stats {
    margin-top: 15px;
    padding-top: 15px;
    border-top: 1px solid #e0e0e0;
}

.stats span {
    margin-right: 20px;
    color: #666;
}
```

---

## 5. Тестирование

### Тест 1: Event-based режим

```bash
# 1. Проверить что scope.event_based = true
# 2. Запустить анализ
python -m cli.scheduler run --source-url https://vk.com/username --once

# 3. Проверить результат
# - Должны быть записи для каждого дня
# - Заголовки с датами: "Активность за 17 октября 2025"
# - В dashboard эмодзи, keywords, ссылки
```

### Тест 2: Topic-based режим

```python
# 1. Обновить Scenario #10
await BotScenario.objects.update_by_id(
    10,
    scope={"event_based": False}
)

# 2. Запустить анализ
python -m cli.scheduler run --source-url https://vk.com/username --once

# 3. Проверить результат
# - ИИ должен определить темы
# - Похожие темы должны связываться
# - topic_chain_id должен быть одинаковым для похожих тем
```

### Тест 3: Rich Display

```bash
# 1. Открыть dashboard
# 2. Перейти к topic chain
# 3. Проверить наличие:
#    - ✅ Эмодзи для sentiment
#    - ✅ Keywords как badges
#    - ✅ Ссылки на источники
#    - ✅ Статистика (посты, реакции, комментарии)
```

---

## 6. Checklist реализации

### Backend
- [x] RichDisplayHelper создан
- [ ] Импорт добавлен в dashboard.py
- [ ] Эндпоинт `get_formatted_analytics` создан
- [ ] Метод `analyze_content_by_topics` добавлен в analyzer
- [ ] Метод `_find_similar_topic_chain` реализован
- [ ] Логика в scheduler.py обновлена для выбора режима

### Frontend
- [ ] HTML шаблон обновлён с rich display элементами
- [ ] CSS стили добавлены
- [ ] JavaScript для интерактивности (если нужно)

### Testing
- [ ] Протестирован event-based режим
- [ ] Протестирован topic-based режим
- [ ] Проверено rich display в dashboard
- [ ] Проверена работа similarity search

---

**Дата:** 19 октября 2025  
**Статус:** Руководство готово  
**Следующий шаг:** Начать реализацию по чеклисту
