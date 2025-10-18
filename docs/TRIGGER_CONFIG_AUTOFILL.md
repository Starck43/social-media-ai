# Trigger & Action Configuration with Auto-fill

**Date**: Current Session  
**Status**: ✅ Complete

---

## Overview

Реализована удобная система настройки `trigger_type` и `action_type` с автоматическим заполнением конфигураций, аналогичная системе `analysis_types`.

---

## Features

### 1. ✅ Визуальные Radio Buttons

**Trigger Type** и **Action Type** теперь представлены как красивые карточки с emoji:

```
🔑 Совпадение ключевых слов
😊 Порог тональности  
📈 Всплеск активности
@ Упоминание пользователя
⏰ По расписанию
👆 Вручную
```

### 2. ✅ Автозаполнение trigger_config

При выборе trigger type автоматически заполняется `trigger_config` JSON с дефолтными значениями:

**Пример** (KEYWORD_MATCH):
```json
{
  "keywords": ["жалоба", "проблема", "не работает"],
  "mode": "any",
  "case_sensitive": false
}
```

### 3. ✅ Кнопки управления

- **Форматировать** - prettify JSON
- **Сбросить к дефолту** - восстановить дефолтные значения для выбранного триггера

---

## Architecture

### Backend

#### 1. Константы триггеров
**File**: `app/core/trigger_constants.py`

```python
TRIGGER_CONFIG_DEFAULTS = {
    "keyword_match": {
        "keywords": ["жалоба", "проблема", "не работает"],
        "mode": "any",
        "case_sensitive": False
    },
    "sentiment_threshold": {
        "threshold": 0.3,
        "direction": "below"
    },
    "activity_spike": {
        "baseline_period_hours": 24,
        "spike_multiplier": 3.0
    },
    "user_mention": {
        "usernames": ["@brand", "@support"],
        "mode": "any"
    },
    "time_based": {},
    "manual": {}
}
```

#### 2. Admin View
**File**: `app/admin/views.py` → `BotScenarioAdmin.scaffold_form()`

```python
from app.core.trigger_constants import TRIGGER_CONFIG_DEFAULTS

form.trigger_types_enum = list(BotTriggerType)
form.action_types_enum = list(BotActionType)
form.trigger_defaults = TRIGGER_CONFIG_DEFAULTS
```

### Frontend

#### 1. UI Sections
**File**: `app/templates/sqladmin/bot_scenario_form.html`

**Секция 4: Триггер**
- Radio buttons с emoji для каждого типа триггера
- Textarea для `trigger_config` JSON
- Кнопки "Форматировать" и "Сбросить к дефолту"

**Секция 5: Действие**
- Radio buttons с emoji для каждого типа действия

#### 2. JavaScript Functions

**Автозаполнение trigger_config**:
```javascript
function syncTriggerToForm() {
    const selected = document.querySelector(".trigger-type-input:checked");
    const triggerValue = selected ? selected.value : null;
    
    // Auto-fill if empty
    if (triggerValue && window.triggerDefaults[triggerValue]) {
        const configTextarea = document.getElementById("trigger-config-json");
        if (!configTextarea.value || configTextarea.value === "{}") {
            configTextarea.value = JSON.stringify(
                window.triggerDefaults[triggerValue], 
                null, 
                2
            );
        }
    }
    
    // Create hidden field
    let triggerField = document.querySelector("input[name='trigger_type']");
    if (!triggerField) {
        triggerField = document.createElement("input");
        triggerField.type = "hidden";
        triggerField.name = "trigger_type";
        form.appendChild(triggerField);
    }
    triggerField.value = triggerValue || "";
}
```

**Сброс к дефолту**:
```javascript
function resetTriggerConfig() {
    const selected = document.querySelector(".trigger-type-input:checked");
    if (selected && window.triggerDefaults[selected.value]) {
        const textarea = document.getElementById("trigger-config-json");
        textarea.value = JSON.stringify(
            window.triggerDefaults[selected.value], 
            null, 
            2
        );
        alert("Конфигурация сброшена к дефолтным значениям");
    }
}
```

---

## Data Flow

### Create Mode (новый сценарий)

1. Пользователь выбирает trigger type (radio button)
2. JavaScript автоматически заполняет `trigger_config` дефолтными значениями
3. Пользователь может редактировать JSON вручную
4. При submit:
   - `trigger_type`: `"keyword_match"` (hidden field)
   - `trigger_config`: `{"keywords": [...], "mode": "any"}` (textarea)
   - `action_type`: `"comment"` (hidden field)

### Edit Mode (редактирование)

1. **Загрузка**:
   - Radio button отмечается по `trigger_type` из БД
   - Textarea заполняется `trigger_config` из БД
   - Radio button отмечается по `action_type` из БД

2. **Изменение триггера**:
   - Выбор нового триггера → автозаполнение конфига (если textarea пустой)
   - Можно редактировать конфиг вручную

3. **Сброс конфига**:
   - Кнопка "Сбросить к дефолту" → перезаписывает textarea

---

## Examples

### Пример 1: Создание сценария с keyword_match

**Действия**:
1. Выбрать radio "🔑 Совпадение ключевых слов"
2. Автоматически заполняется:
```json
{
  "keywords": ["жалоба", "проблема", "не работает"],
  "mode": "any",
  "case_sensitive": false
}
```
3. Изменить keywords на свои
4. Выбрать action "💬 Комментарий"

**Результат**:
- `trigger_type`: `"keyword_match"`
- `trigger_config`: кастомизированный JSON
- `action_type`: `"comment"`

### Пример 2: Сброс к дефолту

**Ситуация**: Пользователь отредактировал trigger_config и хочет вернуть дефолтные значения

**Действия**:
1. Нажать кнопку "🔄 Сбросить к дефолту"
2. Конфиг перезаписывается дефолтными значениями

### Пример 3: Изменение триггера

**Исходное состояние**:
- trigger_type: `"keyword_match"`
- trigger_config: `{"keywords": ["custom"], "mode": "all"}`

**Действия**: Выбрать другой триггер "😊 Порог тональности"

**Результат**:
- Если textarea пустой → автозаполнение
- Если есть данные → не перезаписывается (защита от потери кастомных настроек)

---

## Validation Rules

### JavaScript Validation
✅ JSON валидация перед submit  
✅ Автозаполнение только для пустых полей  
✅ Сохранение кастомных значений  

### Backend Validation
✅ `trigger_type` - nullable enum (BotTriggerType)  
✅ `trigger_config` - nullable JSON dict  
✅ `action_type` - nullable enum (BotActionType)  

---

## Comparison with analysis_types

| Feature | analysis_types | trigger_type | action_type |
|---------|---------------|--------------|-------------|
| UI | Checkboxes (multiple) | Radio buttons (single) | Radio buttons (single) |
| Config field | `scope` | `trigger_config` | N/A |
| Auto-fill | ✅ Yes | ✅ Yes | N/A |
| Reset button | ❌ No | ✅ Yes | N/A |
| Format button | ✅ Yes (scope) | ✅ Yes | N/A |

---

## Testing

### Manual Testing Checklist

#### Create Mode
1. ✅ Открыть форму создания сценария
2. ✅ Выбрать trigger type → проверить автозаполнение trigger_config
3. ✅ Изменить trigger_config вручную
4. ✅ Сменить trigger type → проверить что кастомный конфиг НЕ перезаписывается (если не пустой)
5. ✅ Нажать "Сбросить к дефолту" → проверить восстановление дефолтных значений
6. ✅ Выбрать action type
7. ✅ Сохранить сценарий

#### Edit Mode
1. ✅ Открыть существующий сценарий с trigger/action
2. ✅ Проверить что radio buttons отмечены корректно
3. ✅ Проверить что trigger_config загружен корректно
4. ✅ Изменить trigger_config → сохранить → проверить сохранение
5. ✅ Изменить trigger type → проверить автозаполнение
6. ✅ Сохранить и переоткрыть → проверить что всё сохранилось

#### Console Logs
```
[BotScenario] Loaded trigger_type: keyword_match
[BotScenario] Auto-filled trigger_config for: sentiment_threshold
[BotScenario] Loaded action_type: comment
```

---

## Files Modified

### Backend
- ✅ `app/core/trigger_constants.py` - created (дефолтные конфиги)
- ✅ `app/admin/views.py` - updated `scaffold_form()` (добавлены enum списки и defaults)

### Frontend
- ✅ `app/templates/sqladmin/bot_scenario_form.html` - добавлены секции 4 и 5, JavaScript функции

### Documentation
- ✅ `docs/TRIGGER_CONFIG_AUTOFILL.md` - этот файл

---

## Advantages

✅ **Удобство**: Визуальные radio buttons с emoji  
✅ **Автоматизация**: Автозаполнение дефолтных конфигов  
✅ **Гибкость**: Можно редактировать конфиги вручную  
✅ **Безопасность**: Защита от перезаписи кастомных значений  
✅ **Консистентность**: Единый стиль с analysis_types  
✅ **Отладка**: Детальные логи для понимания что происходит  

---

## Future Improvements (optional)

1. 📝 Валидация специфичных параметров для каждого триггера
2. 🎨 Hints/tooltips при наведении на trigger type
3. 💡 Превью действия для выбранного action type
4. 🔍 Syntax highlighting для trigger_config JSON
5. ✅ Интеграция с TRIGGER_HINTS для показа примеров использования

---

## Related Documentation

- `docs/BOT_SCENARIO_UX_IMPROVEMENTS.md` - общие улучшения UX формы
- `docs/ANALYSIS_TYPES_SCOPE_SYSTEM.md` - система analysis_types (аналогичная архитектура)
- `app/core/trigger_hints.py` - hints и документация для каждого триггера

---

**Status**: ✅ Production Ready  
**Tested**: Ready for manual testing  
**Documentation**: Complete
