# Автоматическое добавление JSON формата в промпты

**Дата**: 16.10.2025  
**Статус**: ✅ Реализовано  
**Версия**: v3.0-auto-json

---

## Проблема

При создании кастомных промптов для сценариев приходилось всегда дописывать:

```
Верни результат в JSON формате с полями: main_topics, overall_mood, highlights
```

Это было:
- ❌ Избыточно (повторялось в каждом промпте)
- ❌ Подвержено ошибкам (можно забыть)
- ❌ Неудобно для пользователей

---

## Решение

Добавлен автоматический постфикс с JSON инструкцией для всех кастомных промптов.

### Как работает:

1. **Проверка наличия**: Система проверяет есть ли в промпте ключевые слова `json`, `формате json`, `верни в формате`
2. **Автодобавление**: Если JSON инструкции нет - она добавляется автоматически
3. **Разные форматы**: Для text/image/video разные структуры JSON

---

## Реализация

### Файл: `app/services/ai/prompts.py`

```python
@staticmethod
def _ensure_json_instruction(prompt: str, media_type: MediaType) -> str:
    """
    Ensure prompt has JSON format instruction appended if not present.
    """
    from app.utils.enum_helpers import get_enum_value
    
    # Check if prompt already mentions JSON format
    prompt_lower = prompt.lower()
    if any(keyword in prompt_lower for keyword in ['json', 'формате json', 'верни в формате']):
        # Already has JSON instruction
        return prompt
    
    # Append JSON instruction based on media type
    media_value = get_enum_value(media_type)
    
    if media_value == 'text':
        json_instruction = """

ВАЖНО: Верни результат в JSON формате с полями:
{
    "main_topics": ["тема1", "тема2", "тема3"],
    "overall_mood": "описание общего настроя",
    "highlights": ["выделяющийся момент1", "выделяющийся момент2"],
    "sentiment_score": 0.0-1.0
}
"""
    elif media_value == 'image':
        json_instruction = """

ВАЖНО: Верни результат в JSON формате с полями:
{
    "visual_themes": ["тема1", "тема2", "тема3"],
    "dominant_colors": ["цвет1", "цвет2"],
    "mood": "описание визуального настроения"
}
"""
    elif media_value == 'video':
        json_instruction = """

ВАЖНО: Верни результат в JSON формате с полями:
{
    "video_types": ["тип1", "тип2"],
    "main_themes": ["тема1", "тема2"],
    "content_style": "описание стиля контента"
}
"""
    else:
        # Generic JSON instruction
        json_instruction = """

ВАЖНО: Верни результат в JSON формате.
"""
    
    return prompt + json_instruction
```

### Интеграция в PromptBuilder:

```python
@staticmethod
def get_prompt(media_type: MediaType, scenario: Optional['BotScenario'] = None, **context) -> str:
    # ... existing code ...
    
    # Use custom prompt if available
    if custom_prompt:
        # Prepare variables based on media type
        variables = PromptBuilder._prepare_variables(media_type, **context)
        prompt = PromptSubstitution.substitute(custom_prompt, variables)
        
        # 🆕 Auto-append JSON instruction if not present
        prompt = PromptBuilder._ensure_json_instruction(prompt, media_type)
        return prompt
    
    # Fallback to default prompts (already have JSON instructions)
    return PromptBuilder._get_default_prompt(media_type, **context)
```

---

## Примеры использования

### До (нужно было писать):

```
Сделай быстрый анализ содержания контента из {platform}.

Кратко (2-3 предложения):
1. О чём говорят (главные темы)
2. Что выделяется (необычное или важное)

Верни результат в JSON формате с полями: main_topics, overall_mood, highlights
```

### После (пишем только суть):

```
Сделай быстрый анализ содержания контента из {platform}.

Кратко (2-3 предложения):
1. О чём говорят (главные темы)
2. Что выделяется (необычное или важное)
```

**Система автоматически добавит**:

```
ВАЖНО: Верни результат в JSON формате с полями:
{
    "main_topics": ["тема1", "тема2", "тема3"],
    "overall_mood": "описание общего настроя",
    "highlights": ["выделяющийся момент1", "выделяющийся момент2"],
    "sentiment_score": 0.0-1.0
}
```

---

## JSON структуры по типам медиа

### TEXT (текстовый контент):

```json
{
    "main_topics": ["тема1", "тема2", "тема3"],
    "overall_mood": "описание общего настроя",
    "highlights": ["выделяющийся момент1", "выделяющийся момент2"],
    "sentiment_score": 0.0-1.0
}
```

### IMAGE (изображения):

```json
{
    "visual_themes": ["тема1", "тема2", "тема3"],
    "dominant_colors": ["цвет1", "цвет2"],
    "mood": "описание визуального настроения"
}
```

### VIDEO (видео):

```json
{
    "video_types": ["тип1", "тип2"],
    "main_themes": ["тема1", "тема2"],
    "content_style": "описание стиля контента"
}
```

---

## Когда JSON инструкция НЕ добавляется

Система НЕ добавит JSON инструкцию если в промпте уже есть:

- ✅ `json` (любой регистр)
- ✅ `формате json`
- ✅ `верни в формате`
- ✅ `JSON`
- ✅ `JSON формате`

Примеры:

```
"Проанализируй и верни в JSON формате"  → НЕ добавится (уже есть)
"Ответь простым текстом"                → ДОБАВИТСЯ
"Сделай быстрый анализ"                 → ДОБАВИТСЯ
```

---

## Преимущества

### ✅ Для пользователей:
- Короче и понятнее промпты
- Меньше ошибок (забыли указать формат)
- Легче создавать новые сценарии

### ✅ Для системы:
- Гарантированный JSON формат ответа
- Унифицированная структура данных
- Легче парсить и обрабатывать результаты

### ✅ Для разработки:
- Меньше копипасты
- Проще поддерживать (изменения в одном месте)
- Консистентность форматов

---

## Тестирование

### Создайте тестовый сценарий:

1. В админ-панели создайте новый сценарий
2. В поле "Text Prompt" напишите только:
   ```
   Проанализируй контент из {platform} и скажи о чем говорят
   ```
3. Сохраните и запустите анализ
4. Проверьте в AIAnalytics.prompt_text что JSON инструкция добавилась

### Проверка в коде:

```python
from app.services.ai.prompts import PromptBuilder
from app.types import MediaType

# Test prompt without JSON
custom_prompt = "Проанализируй контент"

# Get final prompt
final_prompt = PromptBuilder._ensure_json_instruction(custom_prompt, MediaType.TEXT)

print(final_prompt)
# Должно содержать "ВАЖНО: Верни результат в JSON формате..."
```

---

## Совместимость

### ✅ Работает с:
- Кастомными промптами из сценариев
- Всеми типами медиа (TEXT, IMAGE, VIDEO, AUDIO)
- Подстановкой переменных {platform}, {text}, etc.
- Старыми промптами (с явной JSON инструкцией)

### ❌ НЕ затрагивает:
- Дефолтные промпты (уже имеют JSON инструкции)
- Unified summary промпты
- Промпты с явной JSON инструкцией

---

## FAQ

### Q: Что если я хочу свой формат JSON?

**A**: Напишите свою JSON инструкцию в промпте. Система определит что у вас уже есть JSON и не добавит свою.

### Q: Можно ли отключить автодобавление?

**A**: Да, просто включите слово "json" в промпт. Система решит что вы уже указали формат.

### Q: Что если LLM не вернет JSON?

**A**: Это проблема LLM, а не промпта. Убедитесь что модель поддерживает JSON mode или используйте модели с лучшей instruction-following способностью.

### Q: Работает ли с unified summary?

**A**: Пока нет. Unified summary имеет свою логику. Можно добавить аналогично.

---

## Roadmap

### Планы на будущее:

- [ ] Добавить для unified_summary_prompt
- [ ] Поддержка AUDIO медиа типа
- [ ] Конфигурируемые JSON схемы через настройки
- [ ] Валидация ответа LLM на соответствие схеме
- [ ] Retry с исправленным промптом если JSON невалидный

---

## Changelog

### v3.0-auto-json (16.10.2025)
- ✅ Добавлен метод `_ensure_json_instruction()`
- ✅ Интеграция в `PromptBuilder.get_prompt()`
- ✅ Поддержка TEXT, IMAGE, VIDEO
- ✅ Автоопределение наличия JSON в промпте
- ✅ Документация и примеры

---

**Автор**: Factory Droid  
**Дата создания**: 16.10.2025  
**Последнее обновление**: 16.10.2025
