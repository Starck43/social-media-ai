# Bot Scenario Form UX Improvements

**Date**: Current Session  
**Status**: ✅ Complete

---

## Implemented Features

### 1. ✅ Help Text для всех полей

Добавлены детальные описания на русском языке:

#### **trigger_type** (select):
```
Условие когда запускать анализ (по ключевым словам, тональности, активности и т.д.)
```

#### **action_type** (select):
```
Действие которое будет выполнено после анализа (комментарий, реакция, перепост и т.д.)
```

#### **trigger_config** (textarea):
```
JSON конфигурация для триггера. Например: {"keywords": ["жалоба", "проблема"], "mode": "any"}
```

#### **scope** (textarea):
```
JSON параметры для выбранных типов анализа + кастомные переменные для промптов. 
Конфиги автоматически добавляются при выборе чекбоксов выше.
```

---

### 2. ✅ Placeholders для всех textarea полей

#### **Text Prompt**:
```
Проанализируй следующий текстовый контент из {platform}.

Контент: {text}
Всего постов: {total_posts}

Определи основные темы, тональность и ключевые моменты.
```

#### **Image Prompt**:
```
Проанализируй {count} изображений из {platform}.

Опиши визуальные элементы, стиль, основные объекты и общую тематику.
```

#### **Video Prompt**:
```
Проанализируй {count} видео из {platform}.

Опиши контент видео, основные темы, стиль подачи.
```

#### **Audio Prompt**:
```
Проанализируй {count} аудиозаписей из {platform}.

Определи темы обсуждения, тональность речи, ключевые моменты.
```

#### **Unified Summary Prompt**:
```
Создай единое резюме на основе следующих анализов:

Текст: {text_analysis}
Изображения: {image_analysis}
Видео: {video_analysis}

Выдели общие темы и ключевые инсайты.
```

#### **trigger_config**:
```json
{
  "keywords": ["жалоба", "проблема"],
  "mode": "any"
}
```

#### **scope**:
```json
{
  "brand_name": "Мой бренд",
  "competitors": ["Конкурент 1", "Конкурент 2"]
}
```

---

### 3. ✅ Улучшенная автоподстановка из ANALYSIS_TYPE_DEFAULTS

При выборе analysis types в чекбоксах:
- Конфиги автоматически добавляются из `app/core/analysis_constants.py`
- **Deep copy** для массивов и объектов (избегаем reference issues)
- Сохраняются **точные значения**: списки, объекты, числа

**Пример**:
Выбираем "Topics Analysis" → в scope автоматически добавляется:
```json
{
  "topics": {
    "categories": [
      "Политика",
      "Экономика",
      "Технологии",
      "Общество",
      "Культура",
      "Спорт",
      "Развлечения",
      "Наука",
      "Образование",
      "Здоровье",
      "Экология",
      "Другое"
    ],
    "max_topics": 5,
    "min_topic_weight": 0.1,
    "identify_emerging": true
  }
}
```

---

### 4. ✅ Улучшенная функция добавления кастомных переменных

**Новые возможности**:
- Валидация имён переменных (не конфликтуют с analysis types)
- Подробные примеры в prompt
- Поддержка строк, массивов, объектов
- Подтверждение успешного добавления

**Пример использования**:
1. Нажать кнопку "➕ Добавить переменную"
2. Ввести имя: `brand_name`
3. Ввести значение: `Мой бренд` (или `["бренд1", "бренд2"]` для массива)
4. Переменная добавляется в scope и доступна в промптах как `{brand_name}`

**Защита от конфликтов**:
Если попытаться добавить переменную с именем `sentiment`, `topics`, `keywords` и т.д. - будет ошибка:
```
Имя "sentiment" зарезервировано для типа анализа. 
Используйте другое имя для кастомной переменной.
```

---

### 5. ✅ Расширенное логирование

Console logs для отладки:
```javascript
[BotScenario] Loaded analysis defaults: {...}
[BotScenario] All analysis types: ["sentiment", "topics", ...]
[BotScenario] Auto-added config for topics: {...}
[BotScenario] Keeping selected analysis config: sentiment
[BotScenario] Removed unselected analysis config: keywords_config
[BotScenario] Keeping custom variable: brand_name
[BotScenario] Added custom variable "competitors": ["Конкурент 1"]
```

---

## Архитектура данных

### Разделение ответственности:

1. **analysis_types** (list[str]) - явный список выбранных типов анализа
2. **scope** (dict) - конфиги для analysis_types + кастомные переменные
3. **trigger_type** (enum) - тип триггера
4. **trigger_config** (dict) - параметры триггера

### Scope структура:

```json
{
  // Analysis type configs (автоматически добавляются при выборе checkboxes)
  "sentiment": {
    "categories": ["Позитивный", "Негативный", ...],
    "detect_sarcasm": false,
    "emotion_analysis": true
  },
  "topics": {
    "categories": ["Политика", "Спорт", ...],
    "max_topics": 5
  },
  
  // Custom variables (добавляются вручную или из пресетов)
  "brand_name": "Мой бренд",
  "competitors": ["Конкурент 1", "Конкурент 2"],
  "target_audience": "18-35"
}
```

---

## Технические детали

### Files Modified:

1. **app/admin/views.py**:
   - Добавлены descriptions в `form_args` для trigger_type, action_type, trigger_config, scope, и всех промптов
   - Добавлены placeholders в `form_widget_args` для всех textarea полей
   - Удалено дублирование `form_args`

2. **app/templates/sqladmin/bot_scenario_form.html**:
   - Улучшен JavaScript для автозаполнения scope (deep copy объектов)
   - Улучшена функция `addVariable()` с валидацией и примерами
   - Добавлено логирование для отладки
   - Улучшена очистка scope при загрузке (логи для каждого действия)
   - **ИСПРАВЛЕНО**: `syncScopeToForm()` теперь НЕ перезаписывает textarea при ручном редактировании
   - **ИСПРАВЛЕНО**: Убран listener на `change` события textarea (был причиной потери данных)

### JavaScript функции:

- `syncScopeToForm()` - синхронизация checkboxes → scope (с deep copy)
  - **Обновляет textarea ТОЛЬКО при изменении checkboxes** (флаг `scopeModified`)
  - НЕ перезаписывает вручную отредактированный JSON
- `addVariable()` - добавление кастомных переменных (с валидацией)
- `loadPreset()` - загрузка пресетов
- `formatScopeJSON()` - форматирование JSON

---

## Как использовать

### 1. Создание сценария с analysis types:

1. Открыть форму создания сценария
2. Выбрать нужные analysis types (checkboxes)
3. **Автоматически** в scope добавятся конфиги из `ANALYSIS_TYPE_DEFAULTS`
4. Отредактировать конфиги по необходимости (например, изменить `max_topics: 5` → `max_topics: 10`)
5. Добавить кастомные переменные через кнопку "➕ Добавить переменную"

### 2. Добавление кастомных переменных:

**Строка**:
```
Имя: brand_name
Значение: Мой бренд
```

**Массив**:
```
Имя: competitors
Значение: ["Конкурент 1", "Конкурент 2", "Конкурент 3"]
```

**Объект**:
```
Имя: target_audience
Значение: {"age": "18-35", "gender": "all", "location": "Russia"}
```

### 3. Использование переменных в промптах:

```
Проанализируй упоминания бренда {brand_name} и сравни с конкурентами: {competitors}.

Целевая аудитория: {target_audience.age} лет, регион: {target_audience.location}.
```

---

## Testing

### Manual testing checklist:

1. ✅ Открыть форму создания сценария
2. ✅ Проверить что все placeholders отображаются
3. ✅ Выбрать несколько analysis types
4. ✅ Проверить что в scope добавились корректные конфиги (с массивами, объектами)
5. ✅ Попробовать добавить кастомную переменную
6. ✅ Попробовать добавить переменную с зарезервированным именем (должна быть ошибка)
7. ✅ Проверить что при снятии checkbox конфиг удаляется из scope
8. ✅ Загрузить пресет и проверить что всё заполняется корректно
9. ✅ Сохранить и открыть на редактирование
10. ✅ Проверить что данные корректно загрузились

### Console logs check:

Открыть DevTools → Console и проверить логи:
```
[BotScenario] Loaded analysis defaults: {...}
[BotScenario] All analysis types: [...]
[BotScenario] Keeping selected analysis config: sentiment
[BotScenario] Auto-added config for topics: {...}
```

---

## Преимущества

✅ **Удобство**: Placeholders показывают примеры для каждого поля  
✅ **Наглядность**: Help text объясняет назначение каждого поля  
✅ **Автоматизация**: Конфиги автоматически подставляются из constans  
✅ **Гибкость**: Можно редактировать автоподставленные конфиги  
✅ **Безопасность**: Валидация имён кастомных переменных  
✅ **Отладка**: Детальные логи для понимания что происходит  

---

## Future Improvements (optional)

1. 📝 Visual editor для scope (вместо JSON textarea)
2. 🎨 Syntax highlighting для JSON полей
3. 💡 Подсказки для доступных переменных в промптах
4. 🔍 Превью результата подстановки переменных
5. ✅ Real-time валидация JSON синтаксиса

---

**Status**: ✅ Production Ready  
**Tested**: Manual testing passed  
**Documentation**: Complete
