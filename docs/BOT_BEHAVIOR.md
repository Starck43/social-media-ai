# Поведение AI бота - Документация

**Дата:** 2024-12-10

---

## 📋 Структура Bot Scenario

### Поля модели BotScenario

```python
class BotScenario:
    id: int
    name: str                    # Название сценария
    description: str             # Описание сценария
    content_types: List[str]     # ["posts", "comments", "videos"]
    analysis_types: List[str]    # ["sentiment", "keywords", "topics"]
    scope: dict                  # Параметры анализа
    ai_prompt: str               # Промпт для AI
    action_type: BotActionType   # Действие после анализа (опционально)
    is_active: bool              # Активен ли сценарий
    cooldown_minutes: int        # Интервал между запусками
```

---

## 🔍 Section 1: Основная информация

**Что отображается:**

1. **Название** (name) - обязательное
   - Например: "Мониторинг настроения клиентов"
   - Текстовое поле

2. **Описание** (description) - опциональное
   - Например: "Анализирует тональность отзывов и комментариев клиентов"
   - Текстовое поле (textarea)

3. **Промпт для ИИ** (ai_prompt) - обязательное
   - Инструкции для AI как анализировать контент
   - Может содержать переменные из scope: `{variable_name}`
   - Текстовое поле (textarea, большое)
   - Пример:
     ```
     Проанализируй тональность этих комментариев.
     Обращай внимание на {topics}.
     Классифицируй по категориям: {sentiment_config.categories}
     ```

4. **Активен** (is_active) - чекбокс
   - Включен/выключен сценарий

5. **Интервал проверки** (cooldown_minutes) - число
   - Как часто запускать (в минутах)
   - По умолчанию: 30 минут

**Эти поля рендерятся автоматически через SQLAdmin.**

---

## 🤖 Логика работы AI бота

### 1. Постоянный анализ (независимо от action_type)

**Что происходит всегда:**

```python
# Цикл мониторинга (каждые cooldown_minutes)
for scenario in active_scenarios:
    # 1. Собрать контент по типам
    content = collect_content(
        sources=scenario.sources,
        content_types=scenario.content_types  # posts, comments, etc.
    )
    
    # 2. Проанализировать с помощью AI
    analysis_result = ai_analyzer.analyze(
        content=content,
        analysis_types=scenario.analysis_types,  # sentiment, keywords, etc.
        scope=scenario.scope,  # параметры
        prompt=scenario.ai_prompt
    )
    
    # 3. Сохранить результат в AIAnalytics
    AIAnalytics.objects.create(
        source=source,
        scenario=scenario,
        analysis_data=analysis_result,
        sentiment_summary=analysis_result.get('sentiment'),
        keywords=analysis_result.get('keywords'),
        topics=analysis_result.get('topics'),
        # ... другие поля
    )
```

**Результаты ВСЕГДА сохраняются в таблицу `ai_analytics`** независимо от `action_type`.

---

### 2. Действие после анализа (action_type)

**action_type определяет ЧТО ДЕЛАТЬ С РЕЗУЛЬТАТОМ:**

#### Вариант A: action_type = NULL (None)
```python
# Режим "только анализ"
# ✅ Анализ выполнен
# ✅ Результат сохранён в ai_analytics
# ❌ Никаких действий не выполняется

# Использование: пассивный мониторинг, накопление данных
```

#### Вариант B: action_type = NOTIFICATION
```python
# Режим "анализ + уведомление"
# ✅ Анализ выполнен
# ✅ Результат сохранён в ai_analytics
# ✅ Создано уведомление администратору

if scenario.action_type == BotActionType.NOTIFICATION:
    Notification.objects.create(
        user=admin_user,
        type=NotificationType.TREND_ALERT,
        title=f"Анализ завершён: {scenario.name}",
        message=f"Обнаружено: {analysis_result.summary}",
        data={"analytics_id": analytics.id}
    )
```

#### Вариант C: action_type = COMMENT
```python
# Режим "анализ + комментирование"
# ✅ Анализ выполнен
# ✅ Результат сохранён в ai_analytics
# ✅ Бот оставляет комментарий под контентом

if scenario.action_type == BotActionType.COMMENT:
    # Генерируем ответ на основе анализа
    comment_text = ai_generator.generate_comment(
        analysis=analysis_result,
        prompt=scenario.ai_prompt
    )
    
    # Публикуем комментарий
    social_client.post_comment(
        post_id=content.id,
        text=comment_text
    )
```

#### Вариант D: action_type = MODERATION
```python
# Режим "анализ + модерация"
# ✅ Анализ выполнен
# ✅ Результат сохранён в ai_analytics
# ✅ Автоматическая модерация негативного контента

if scenario.action_type == BotActionType.MODERATION:
    if analysis_result.get('toxicity_score', 0) > 0.7:
        # Скрыть токсичный контент
        social_client.hide_content(content.id)
        
        # Уведомить модератора
        Notification.objects.create(
            user=moderator,
            type=NotificationType.BOT_COMMENT,
            message=f"Контент скрыт: токсичность {toxicity_score}"
        )
```

---

## 📊 Схема работы

```
┌─────────────────────────────────────────────────────┐
│ BotScenario (активный)                              │
│ - name: "Мониторинг негатива"                       │
│ - content_types: ["posts", "comments"]              │
│ - analysis_types: ["sentiment", "toxicity"]         │
│ - action_type: NOTIFICATION                         │
└─────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────┐
│ ШАГ 1: Сбор контента (каждые cooldown_minutes)     │
│                                                     │
│ sources = scenario.sources                          │
│ content = fetch_posts_and_comments(sources)         │
└─────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────┐
│ ШАГ 2: AI анализ                                    │
│                                                     │
│ result = ai_analyzer.analyze(                       │
│     content=content,                                │
│     analysis_types=["sentiment", "toxicity"],       │
│     prompt=scenario.ai_prompt                       │
│ )                                                   │
│                                                     │
│ result = {                                          │
│   "sentiment": {"positive": 20, "negative": 5},     │
│   "toxicity": {"score": 0.8, "toxic_items": [...]} │
│ }                                                   │
└─────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────┐
│ ШАГ 3: Сохранение результата (ВСЕГДА)              │
│                                                     │
│ AIAnalytics.objects.create(                         │
│     source=source,                                  │
│     scenario=scenario,                              │
│     analysis_data=result,                           │
│     sentiment_summary=result["sentiment"]           │
│ )                                                   │
└─────────────────────────────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────┐
│ ШАГ 4: Действие (зависит от action_type)           │
│                                                     │
│ if action_type == NOTIFICATION:                     │
│     ✅ Создать уведомление администратору           │
│                                                     │
│ if action_type == MODERATION:                       │
│     ✅ Скрыть токсичный контент                     │
│     ✅ Уведомить модератора                         │
│                                                     │
│ if action_type == None:                             │
│     ✅ Ничего не делать (только анализ)             │
└─────────────────────────────────────────────────────┘
```

---

## 🎯 Типичные сценарии использования

### 1. Пассивный мониторинг
```python
BotScenario(
    name="Мониторинг настроения",
    analysis_types=["sentiment"],
    action_type=None,  # Только сохраняем данные
)
```
**Результат:** Накапливаются данные в ai_analytics, можно строить графики

### 2. Активное уведомление
```python
BotScenario(
    name="Оповещение о кризисах",
    analysis_types=["sentiment", "toxicity"],
    action_type=BotActionType.NOTIFICATION,
)
```
**Результат:** Анализ + уведомление админа при негативе

### 3. Автоматическая модерация
```python
BotScenario(
    name="Авто-модерация",
    analysis_types=["toxicity"],
    action_type=BotActionType.MODERATION,
)
```
**Результат:** Анализ + автоматическое скрытие токсичных постов

### 4. Умный бот-помощник
```python
BotScenario(
    name="Помощь клиентам",
    analysis_types=["sentiment", "intent"],
    action_type=BotActionType.COMMENT,
)
```
**Результат:** Анализ намерений + автоматический ответ

---

## ⚙️ Параметры в действии

### Пример полного сценария:

```python
{
    "name": "Мониторинг отзывов о продукте",
    "description": "Отслеживает упоминания нового продукта и анализирует реакцию",
    
    "content_types": ["posts", "comments"],
    
    "analysis_types": ["sentiment", "keywords", "brand_mentions"],
    
    "scope": {
        "sentiment_config": {
            "categories": ["positive", "negative", "neutral"],
            "confidence_threshold": 0.7
        },
        "keywords_config": {
            "keywords": ["iPhone 15", "новый айфон", "последняя модель"]
        },
        "brand_mentions_config": {
            "brand_names": ["Apple", "iPhone"],
            "track_sentiment": true
        }
    },
    
    "ai_prompt": """
        Проанализируй отзывы о {keywords_config.keywords}.
        Определи тональность и выдели ключевые моменты.
        Обрати внимание на упоминания брендов: {brand_mentions_config.brand_names}
    """,
    
    "action_type": "NOTIFICATION",  # Уведомлять о результатах
    
    "is_active": true,
    "cooldown_minutes": 60  # Проверять каждый час
}
```

**Что произойдёт:**
1. Каждый час собираются посты и комментарии
2. AI анализирует их на тональность, ищет ключевые слова, отслеживает бренды
3. Результат сохраняется в ai_analytics
4. Администратор получает уведомление о результатах анализа

---

## 💡 Важные моменты

### 1. Анализ ВСЕГДА сохраняется
- Независимо от action_type
- Данные копятся в таблице ai_analytics
- Можно строить аналитику и графики

### 2. action_type - это ДОПОЛНИТЕЛЬНОЕ действие
- `None` = только анализ и сохранение
- `NOTIFICATION` = анализ + уведомление
- `COMMENT` = анализ + комментарий от бота
- `MODERATION` = анализ + модерация контента
- `REPLY` = анализ + ответ в личку
- `POST` = анализ + создание поста
- `REACTION` = анализ + реакция на контент

### 3. Параллельная работа
- Если action_type = NOTIFICATION:
  - ✅ Сохраняется анализ в ai_analytics
  - ✅ ПАРАЛЛЕЛЬНО создаётся уведомление
  - Оба действия выполняются

### 4. Ошибки не блокируют сохранение
```python
try:
    # Сначала сохраняем анализ
    analytics = save_analysis(result)
except Exception as e:
    log_error(e)

# Потом выполняем action (если не сохранился анализ - action не выполнится)
if analytics and action_type:
    execute_action(action_type, analytics)
```

---

## 📝 Итого

**"Основная информация" содержит:**
- Название сценария
- Описание
- **AI промпт** (самое важное - инструкции для AI)
- Чекбокс "Активен"
- Интервал проверки

**action_type определяет:**
- Что делать с результатом анализа
- `None` = пассивный мониторинг
- `NOTIFICATION` = активное оповещение
- `COMMENT/MODERATION/etc.` = активные действия бота

**Данные всегда сохраняются в ai_analytics**, action_type только добавляет дополнительные действия.
