# Проблемы логики сохранения аналитики

**Дата**: 2025-10-18  
**Проблемы**:
1. ❌ Дата анализа некорректная (3:00 вместо реального времени)
2. ❌ Только 1 запись в аналитике вместо множества (по дням)

---

## 🔍 Анализ текущей логики

### **Проблема #1: Дата анализа = date.today()**

**Файл**: `app/services/ai/analyzer.py`, строка ~697

```python
async def _save_analysis(...):
    # ...
    
    # Check if analysis already exists for today
    existing_analysis = await AIAnalytics.objects.filter(
        source_id=source.id,
        analysis_date=date.today(),  # ⬅️ ПРОБЛЕМА!
        period_type=PeriodType.DAILY
    ).first()
    
    # ...
    
    # Create analytics record
    analytics = await AIAnalytics.objects.create(
        source_id=source.id,
        analysis_date=date.today(),  # ⬅️ ПРОБЛЕМА!
        period_type=PeriodType.DAILY,
        # ...
    )
```

**Что происходит**:
- `date.today()` = текущая дата БЕЗ времени (только дата)
- При сохранении в БД PostgreSQL конвертирует в `2025-10-18 00:00:00`
- При отображении с timezone: `2025-10-18 00:00:00 UTC` → `2025-10-18 03:00:00 MSK` (UTC+3)

**Должно быть**:
- Использовать `datetime.now(UTC).date()` для явного указания timezone
- Или хранить `analysis_date` как DATE тип (без времени)

---

### **Проблема #2: Одна запись на весь период**

**Текущая логика**:
```python
# 1. Собирается контент (100 постов за весь период)
content = await collector.collect(source)

# 2. Все 100 постов анализируются ОДНИМ запросом к LLM
analysis = await analyzer.analyze_content(content, source)

# 3. Создаётся ОДНА запись AIAnalytics с analysis_date = TODAY
analytics = await AIAnalytics.objects.create(
    source_id=source.id,
    analysis_date=date.today(),  # Сегодня
    summary_data={
        "content_statistics": {
            "total_posts": 100,
            "date_range": {
                "first": "2024-01-29",  # Самый старый пост
                "last": "2025-10-17"     # Самый новый пост
            }
        }
    }
)
```

**Проблема**:
- Собирается контент за **ВСЮ ИСТОРИЮ** (с 2024-01-29 по 2025-10-17 = 627 дней!)
- Создаётся только **1 запись** с датой TODAY (2025-10-18)
- Timeline на dashboard показывает только 1 точку

**Должно быть**:
- Группировать контент **по дням**
- Создавать **отдельную запись AIAnalytics** на каждый день с активностью
- Или: анализировать инкрементально (только новые посты с last_checked)

---

## 📊 Текущая ситуация Source #19

### **Что собрано**:
```
Total posts: 100
Date range: 2024-01-29 → 2025-10-17 (627 дней!)
```

### **Что сохранено**:
```sql
SELECT id, analysis_date, created_at
FROM social_manager.ai_analytics
WHERE source_id = 19;

-- Результат:
id | analysis_date | created_at
---+---------------+---------------------------
93 | 2025-10-18    | 2025-10-18 18:20:51 UTC
```

**Проблема**: Только **1 запись** за сегодня, хотя данные охватывают 627 дней!

---

## ✅ Правильная логика

### **Вариант A: Группировка по дням**

**Что делать**:
1. Собрать контент
2. **Сгруппировать по дням** (по полю `published_at` или `date`)
3. Для **каждого дня** создать отдельную запись AIAnalytics

**Код**:
```python
async def analyze_content_by_days(self, content, source, bot_scenario):
    """Group content by days and create separate analytics for each day."""
    
    # Группировка по дням
    from collections import defaultdict
    from datetime import datetime, timezone
    
    content_by_day = defaultdict(list)
    
    for item in content:
        pub_date = item.get('published_at') or item.get('date') or item.get('created_at')
        
        # Convert Unix timestamp to datetime
        if isinstance(pub_date, int):
            pub_date = datetime.fromtimestamp(pub_date, tz=timezone.utc)
        elif isinstance(pub_date, str):
            pub_date = datetime.fromisoformat(pub_date.replace('Z', '+00:00'))
        
        # Group by date (without time)
        day = pub_date.date() if pub_date else date.today()
        content_by_day[day].append(item)
    
    logger.info(f"Grouped content into {len(content_by_day)} days")
    
    # Анализ для каждого дня
    analytics_list = []
    
    for day, day_content in sorted(content_by_day.items()):
        logger.info(f"Analyzing {len(day_content)} items for {day}")
        
        # Анализ контента за день
        analysis = await self.analyze_content(day_content, source, bot_scenario)
        
        # Сохранение с ПРАВИЛЬНОЙ датой
        analytics = await self._save_analysis(
            analysis_results=analysis,
            source=source,
            analysis_date=day,  # ⬅️ Дата КОНТЕНТА, не TODAY!
            # ...
        )
        
        analytics_list.append(analytics)
    
    return analytics_list
```

**Результат**:
```sql
SELECT id, analysis_date, created_at
FROM social_manager.ai_analytics
WHERE source_id = 19
ORDER BY analysis_date DESC;

-- Результат:
id | analysis_date | created_at
---+---------------+---------------------------
101| 2025-10-17    | 2025-10-18 18:20:51 UTC
100| 2025-10-16    | 2025-10-18 18:20:50 UTC
99 | 2025-10-15    | 2025-10-18 18:20:49 UTC
...
50 | 2024-02-01    | 2025-10-18 18:20:20 UTC
49 | 2024-01-29    | 2025-10-18 18:20:19 UTC
```

**Timeline на dashboard**:
```
source_19_scenario_10
├─ 2024-01-29: [анализ за этот день]
├─ 2024-01-30: [анализ за этот день]
├─ ...
├─ 2025-10-16: [анализ за этот день]
└─ 2025-10-17: [анализ за этот день]
```

---

### **Вариант B: Инкрементальный анализ (только новое)**

**Что делать**:
1. Использовать `last_checked` для фильтрации
2. Собирать только **новые посты**
3. Создавать запись **только если есть новый контент**

**Код**:
```python
# В VKClient уже реализовано
if source.last_checked:
    params_dict['start_time'] = int(source.last_checked.timestamp())
    # Собирёт только посты ПОСЛЕ last_checked

# В analyzer
if not content:
    logger.info("No new content to analyze")
    return None

# Анализ только нового контента
analysis = await self.analyze_content(content, source)

# Сохранение с датой СЕГОДНЯ (т.к. это инкрементальное обновление)
analytics = await self._save_analysis(
    analysis_date=date.today(),  # OK для инкрементального
    # ...
)

# Обновить last_checked
source.last_checked = datetime.now(UTC)
await source.save()
```

**Результат**:
- **1 запись в день** (если был новый контент)
- Timeline растёт **постепенно**

---

## 🎯 Рекомендация

### **Для Scenario #10 (User Activity)**

**Использовать**: **Вариант A (группировка по дням)**

**Почему**:
1. Event-based анализ требует детализации по событиям
2. Пользователь хочет видеть активность **по дням**
3. Timeline должен показывать эволюцию за весь период

**Реализация**:
1. Добавить метод `analyze_content_by_days()` в `AIAnalyzer`
2. В `ContentCollector.collect_from_source()` вызвать группировку
3. Для каждого дня создать отдельную запись AIAnalytics

---

### **Для других сценариев**

**Использовать**: **Вариант B (инкрементальный)**

**Почему**:
1. Агрегированный анализ (не event-based)
2. Достаточно 1 запись в день
3. Экономия LLM запросов

---

## 🛠️ Исправление кода

### **1. Добавить параметр `analysis_date` в `_save_analysis`**

**Файл**: `app/services/ai/analyzer.py`

```python
async def _save_analysis(
    self,
    analysis_results: dict[str, Any],
    unified_summary: Optional[dict[str, Any]],
    source: Source,
    content_stats: dict[str, Any],
    platform_name: str,
    bot_scenario: Optional['BotScenario'] = None,
    topic_chain_id: Optional[str] = None,
    parent_analysis_id: Optional[int] = None,
    analysis_date: Optional[date] = None,  # ⬅️ NEW
) -> AIAnalytics:
    """Save comprehensive analysis results to database."""
    
    # Use provided date or default to today
    if analysis_date is None:
        analysis_date = date.today()
    
    # Check if analysis already exists for THIS date (not just today)
    existing_analysis = await AIAnalytics.objects.filter(
        source_id=source.id,
        analysis_date=analysis_date,  # ⬅️ CHANGED
        period_type=PeriodType.DAILY
    ).first()
    
    # ...
    
    # Create analytics record
    analytics = await AIAnalytics.objects.create(
        source_id=source.id,
        analysis_date=analysis_date,  # ⬅️ CHANGED
        # ...
    )
```

### **2. Добавить метод группировки по дням**

**Файл**: `app/services/ai/analyzer.py`

```python
async def analyze_content_by_days(
    self,
    content: list[dict],
    source: Source,
    bot_scenario: Optional['BotScenario'] = None
) -> list[AIAnalytics]:
    """
    Group content by days and analyze each day separately.
    
    Args:
        content: List of content items
        source: Source being analyzed
        bot_scenario: Optional bot scenario
    
    Returns:
        List of AIAnalytics records (one per day)
    """
    from collections import defaultdict
    from datetime import datetime, timezone
    
    # Group content by day
    content_by_day = defaultdict(list)
    
    for item in content:
        # Extract publication date
        pub_date = item.get('published_at') or item.get('date') or item.get('created_at')
        
        # Convert to datetime
        if isinstance(pub_date, int):
            pub_date = datetime.fromtimestamp(pub_date, tz=timezone.utc)
        elif isinstance(pub_date, str):
            pub_date = datetime.fromisoformat(pub_date.replace('Z', '+00:00'))
        
        # Group by date
        day = pub_date.date() if pub_date else date.today()
        content_by_day[day].append(item)
    
    logger.info(f"Grouped {len(content)} items into {len(content_by_day)} days for source {source.id}")
    
    # Analyze each day
    analytics_list = []
    
    for day, day_content in sorted(content_by_day.items()):
        logger.info(f"Analyzing {len(day_content)} items for {source.id} on {day}")
        
        try:
            # Analyze content for this day
            analytics = await self.analyze_content(
                content=day_content,
                source=source,
                bot_scenario=bot_scenario,
                analysis_date=day  # ⬅️ PASS DATE TO analyze_content
            )
            
            analytics_list.append(analytics)
            
        except Exception as e:
            logger.error(f"Error analyzing day {day} for source {source.id}: {e}")
            continue
    
    logger.info(f"Created {len(analytics_list)} analytics records for source {source.id}")
    
    return analytics_list
```

### **3. Обновить `analyze_content` для приёма даты**

```python
async def analyze_content(
    self,
    content: list[dict],
    source: Source,
    bot_scenario: Optional['BotScenario'] = None,
    topic_chain_id: Optional[str] = None,
    analysis_date: Optional[date] = None  # ⬅️ NEW
) -> AIAnalytics:
    # ...
    
    # Save analysis
    analysis = await self._save_analysis(
        analysis_results=analysis_results,
        unified_summary=unified_summary,
        source=source,
        content_stats=content_stats,
        platform_name=platform_name,
        bot_scenario=bot_scenario,
        topic_chain_id=topic_chain_id,
        analysis_date=analysis_date  # ⬅️ PASS TO _save_analysis
    )
    
    return analysis
```

### **4. Использовать в collector**

**Файл**: `app/services/monitoring/collector.py`

```python
async def collect_from_source(self, source, content_type="posts", analyze=True):
    # ... collect content ...
    
    if analyze and content:
        # Check if scenario requires day-by-day analysis
        is_event_based = False
        if source.bot_scenario and source.bot_scenario.scope:
            is_event_based = source.bot_scenario.scope.get('event_based', False)
        
        if is_event_based:
            # Analyze by days for event-based scenarios
            analytics_list = await self.ai_analyzer.analyze_content_by_days(
                content=content,
                source=source,
                bot_scenario=source.bot_scenario
            )
            logger.info(f"Created {len(analytics_list)} day-by-day analytics for source {source.id}")
        else:
            # Regular aggregated analysis
            analytics = await self.ai_analyzer.analyze_content(
                content=content,
                source=source,
                bot_scenario=source.bot_scenario
            )
            logger.info(f"Created aggregated analytics for source {source.id}")
    
    # ...
```

---

## 🔧 Быстрый фикс для Source #19

### **1. Удалить текущую запись**

```sql
DELETE FROM social_manager.ai_analytics WHERE source_id = 19;
```

### **2. Установить Scope с event_based**

```sql
UPDATE social_manager.bot_scenarios
SET scope = '{"event_based": true, "max_events_per_analysis": 50, "include_target_info": true}'::jsonb
WHERE id = 10;
```

### **3. Запустить анализ с группировкой**

```bash
# После реализации analyze_content_by_days
python -m cli.scheduler run --once
```

**Ожидаемый результат**:
```sql
SELECT COUNT(*), MIN(analysis_date), MAX(analysis_date)
FROM social_manager.ai_analytics
WHERE source_id = 19;

-- Результат:
count | min         | max
------+-------------+-------------
50+   | 2024-01-29  | 2025-10-17
```

---

## 📋 Итоговый чек-лист

### **Проблемы**:
- [ ] ❌ Дата анализа = `date.today()` → показывает 00:00 (3:00 MSK)
- [ ] ❌ Только 1 запись за весь период → нет timeline

### **Решения**:
- [ ] ✅ Добавить параметр `analysis_date` в `_save_analysis`
- [ ] ✅ Реализовать `analyze_content_by_days()`
- [ ] ✅ Проверять `scope.event_based` в collector
- [ ] ✅ Группировать по дням для event-based сценариев

### **Тестирование**:
- [ ] Удалить старую запись source 19
- [ ] Установить `scope.event_based = true`
- [ ] Запустить анализ
- [ ] Проверить количество записей (должно быть много)
- [ ] Проверить timeline на dashboard

---

**Автор**: Factory Droid  
**Дата**: 2025-10-18
