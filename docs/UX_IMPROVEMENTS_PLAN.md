# План улучшения UX админки и dashboard

## Архитектура решения

### 1. Разделение промпта и JSON schema

**Текущее состояние:**
```python
text_prompt = """
Проанализируй контент...

ФОРМАТ ОТВЕТА:
{
  "analysis_title": "...",
  "events": [...]
}
"""
```

**Новая архитектура:**
```python
# В BotScenario
text_prompt = """
Проанализируй активность пользователя {username} в {platform}.
Обрати внимание на {focus_areas}.
"""

response_format = {
  "mode": "events",  # или "topics"
  "fields": {
    "events": {
      "type": "array",
      "description": "Список событий активности",
      "item_schema": {
        "type": {"enum": ["post", "comment", "like", "share"]},
        "annotation": "string",
        "sentiment": "number"
      }
    }
  }
}
```

### 2. Два режима сохранения данных

#### Режим A: Event-based (по событиям)
- **Когда:** `scope.event_based = true` или `response_format.mode = "events"`
- **Как работает:**
  - Группировка контента по дням
  - Создание записи для каждого дня
  - Цепочка по `source_id + scenario_id` + дате
  - Заголовок: "Активность за {date}"

#### Режим B: Topic-based (по темам)
- **Когда:** `scope.event_based = false` или `response_format.mode = "topics"`
- **Как работает:**
  - ИИ сам определяет темы из контента
  - Ищет существующие темы по названию/keywords
  - Если тема новая - создает новую цепочку
  - Если тема существует - добавляет в цепочку
  - Заголовок: название темы от ИИ

## Новые поля в BotScenario

```python
class BotScenario:
    # Существующие поля
    text_prompt: str  # Только описательная часть
    
    # НОВЫЕ поля для JSON schema configuration
    response_format_config: dict = {
        "mode": "events",  # "events" или "topics"
        "fields": {}  # Кастомные поля для ответа
    }
    
    output_display_config: dict = {
        "show_sentiment_emoji": True,
        "show_keywords": True,
        "show_source_links": True,
        "show_trigger_reason": True,
        "show_content_annotations": True
    }
```

## JSON Schema Builder v2

```python
class EnhancedJSONSchemaBuilder:
    
    @classmethod
    def build_full_schema(
        cls,
        prompt: str,
        analysis_types: List[str],
        content_types: List[str],
        response_format_config: dict,
        scope: dict
    ) -> dict:
        """
        Строит полный промпт с автоматическим JSON schema.
        
        Returns:
            {
                "prompt": "Полный промпт с инструкциями",
                "expected_schema": {...}  # Для валидации ответа
            }
        """
```

## Dashboard Display Enhancements

### Для sentiment
```python
sentiment_emoji = {
    "Позитивный": "😊",
    "Негативный": "😞",
    "Нейтральный": "😐",
    "Смешанный": "🤔"
}
```

### Для keywords
```html
<div class="keywords">
    <span class="badge">ключ 1</span>
    <span class="badge">ключ 2</span>
</div>
```

### Для source links
```html
<div class="sources">
    <a href="https://vk.com/wall..." target="_blank">
        <i class="fab fa-vk"></i> Пост в VK
    </a>
</div>
```

### Для trigger reason
```html
<div class="trigger-info">
    <i class="fa fa-bell"></i>
    Сработал trigger: KEYWORD_MATCH
    Причина: Найдено ключевое слово "жалоба"
</div>
```

## Порядок реализации

### Этап 1: Модель и миграция
1. ✅ Добавить `response_format_config` в BotScenario
2. ✅ Добавить `output_display_config` в BotScenario
3. ✅ Создать миграцию

### Этап 2: JSON Schema Builder
1. ✅ Создать `EnhancedJSONSchemaBuilder`
2. ✅ Интегрировать в `PromptBuilder`
3. ✅ Добавить автоматическую генерацию schema

### Этап 3: Админка
1. ✅ Обновить форму BotScenario
2. ✅ Добавить подробные descriptions для всех полей
3. ✅ Создать JSON редакторы для конфигурации

### Этап 4: Логика сохранения
1. ✅ Реализовать topic-based режим в analyzer
2. ✅ Добавить поиск существующих тем
3. ✅ Логику связывания с цепочками

### Этап 5: Dashboard
1. ✅ Создать rich display компонент
2. ✅ Добавить sentiment emoji
3. ✅ Добавить keywords badges
4. ✅ Добавить source links
5. ✅ Добавить trigger reason display

## Примеры конфигурации

### Event-based сценарий (текущий Scenario #10)

```json
{
  "response_format_config": {
    "mode": "events",
    "fields": {
      "events": {
        "type": "array",
        "description": "События активности пользователя",
        "max_items": 50,
        "item_schema": {
          "type": "string",
          "time": "datetime",
          "annotation": "string",
          "sentiment": "float",
          "keywords": "array"
        }
      }
    }
  },
  "output_display_config": {
    "show_sentiment_emoji": true,
    "show_keywords": true,
    "show_source_links": true,
    "group_by": "date"
  }
}
```

### Topic-based сценарий (автоматические темы)

```json
{
  "response_format_config": {
    "mode": "topics",
    "fields": {
      "topic_title": "string",
      "topic_description": "string",
      "related_keywords": "array",
      "is_new_topic": "boolean",
      "similar_to": "string"
    }
  },
  "output_display_config": {
    "show_sentiment_emoji": true,
    "show_keywords": true,
    "show_source_links": true,
    "show_related_topics": true,
    "group_by": "topic"
  }
}
```

## Преимущества новой архитектуры

### 1. Гибкость
- Промпты чистые и фокусируются на задаче
- JSON schema генерируется автоматически
- Легко добавлять новые поля

### 2. Переиспользование
- Один промпт для разных источников
- Scope/trigger_config управляют поведением
- response_format_config управляет структурой ответа

### 3. Понятность
- Подробные descriptions для всех полей
- Примеры и плейсхолдеры
- Валидация конфигурации

### 4. Богатое отображение
- Эмодзи для эмоций
- Badges для keywords
- Ссылки на источники
- Trigger reasons

## Следующие шаги

1. ✅ Создать миграцию для новых полей
2. ✅ Реализовать EnhancedJSONSchemaBuilder
3. ✅ Обновить PromptBuilder
4. ✅ Обновить админку
5. ✅ Реализовать topic-based режим
6. ✅ Создать rich display компоненты
7. ✅ Протестировать оба режима

---

**Дата:** 19 октября 2025  
**Статус:** Планирование завершено, готов к реализации
