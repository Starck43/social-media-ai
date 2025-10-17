# Analysis Types & Scope System

**Дата**: Current Session  
**Статус**: ✅ Fixed and Documented

---

## Проблема

В админ-панели при редактировании сценария (BotScenario) поле `scope` (JSON textarea) показывало конфиги типов анализа (например, `topics_config` или `topics`), хотя эти конфиги должны управляться через checkboxes для `analysis_types`.

**Симптомы**:
- Textarea `scope-json` содержал `{"topics": {...}}` вместо того, чтобы быть пустым
- При изменении checkboxes analysis_types конфиги не добавлялись/удалялись автоматически
- JavaScript выдавал ошибки о missing `window.analysisDefaults` и `window.allAnalysisTypes`

---

## Архитектура

### Модель BotScenario

```python
class BotScenario(Base):
    # 1. Список типов анализа (отдельное поле!)
    analysis_types: list[str] = Column(JSON)  # ["sentiment", "topics", "keywords"]
    
    # 2. Конфигурации для анализа (БЕЗ analysis_types!)
    scope: dict[str, Any] = Column(JSON)  # {"sentiment": {...}, "topics": {...}, custom_var: "value"}
```

### Разделение ответственности

**`analysis_types`** (отдельное поле):
- Список активных типов анализа
- Управляется через checkboxes в UI
- Определяет какие анализы будут выполнены

**`scope`** (JSON конфигурация):
- Конфиги для выбранных analysis types (например, `sentiment: {detect_sarcasm: true}`)
- Кастомные переменные для промптов (например, `brand_name: "MyBrand"`)
- НЕ содержит сам список analysis_types

### Формат конфигов в scope

**Новый формат** (без `_config` суффикса):
```json
{
  "sentiment": {
    "detect_sarcasm": true,
    "emotion_analysis": true
  },
  "topics": {
    "max_topics": 5,
    "identify_emerging": true
  },
  "brand_name": "MyBrand"
}
```

**Старый формат** (с `_config` суффиксом) - поддерживается для обратной совместимости:
```json
{
  "sentiment_config": {...},
  "topics_config": {...}
}
```

---

## Решение

### 1. Backend (app/admin/views.py)

Добавлено в `BotScenarioAdmin.scaffold_form()`:

```python
from app.core.analysis_constants import ANALYSIS_TYPE_DEFAULTS

# Provide analysis defaults and all types for JavaScript
form.analysis_defaults = ANALYSIS_TYPE_DEFAULTS
form.all_analysis_types = [at.value for at in AnalysisType]
```

**Что это дает**:
- `form.analysis_defaults` → `window.analysisDefaults` (дефолтные конфиги из `analysis_constants.py`)
- `form.all_analysis_types` → `window.allAnalysisTypes` (список всех возможных типов: ["sentiment", "topics", ...])

### 2. Frontend (bot_scenario_form.html)

**Изменение 1: Очистка scope при загрузке**

При загрузке страницы (edit mode):
1. Загружаем `analysis_types` из БД → ставим checkboxes
2. Загружаем `scope` из БД → **очищаем его от лишних конфигов**:
   - Удаляем конфиги для НЕвыбранных analysis types
   - Сохраняем конфиги для выбранных analysis types (могут быть кастомизированы)
   - Сохраняем все кастомные переменные (не являющиеся analysis configs)

```javascript
// Очистка scope при загрузке
const cleanedScope = {};
for (const [key, value] of Object.entries(initialScope)) {
    const isAnalysisConfig = window.allAnalysisTypes.includes(key);
    const isOldFormatConfig = key.endsWith('_config') && 
        window.allAnalysisTypes.includes(key.replace('_config', ''));
    
    if (isAnalysisConfig || isOldFormatConfig) {
        // Это analysis config
        const analysisType = isOldFormatConfig ? key.replace('_config', '') : key;
        const isSelected = initialAnalysisTypes.includes(analysisType);
        
        if (isSelected) {
            // Тип выбран - сохраняем config (может быть кастомизирован)
            cleanedScope[analysisType] = value;
        }
    } else {
        // Это кастомная переменная, всегда оставляем
        cleanedScope[key] = value;
    }
}
```

**Изменение 2: Управление конфигами через checkboxes**

При изменении checkboxes (`syncScopeToForm()`):
1. **Автодобавление**: При выборе нового analysis type → добавляем дефолтный конфиг из `window.analysisDefaults`
2. **Автоудаление**: При снятии галочки → удаляем конфиг из scope
3. **Сохранение**: Если конфиг уже есть (кастомизирован) → не перезаписываем

```javascript
// AUTO-ADD default configs ТОЛЬКО после первоначальной загрузки
if (!window.isInitialLoad) {
    selectedAnalysisTypes.forEach(type => {
        if (!scope[type] && window.analysisDefaults[type]) {
            scope[type] = window.analysisDefaults[type];
        }
    });
}

// REMOVE configs for unselected analysis types
window.allAnalysisTypes.forEach(type => {
    if (!selectedAnalysisTypes.includes(type)) {
        delete scope[type];
        delete scope[`${type}_config`]; // старый формат
    }
});
```

---

## Поток данных

### Create mode (создание нового сценария)

1. Пользователь выбирает analysis types через checkboxes
2. JavaScript автоматически добавляет дефолтные конфиги в scope
3. Пользователь может редактировать scope JSON вручную (добавлять кастомные переменные)
4. При submit:
   - `analysis_types`: `["sentiment", "topics"]` (скрытое поле)
   - `scope`: `{"sentiment": {...}, "topics": {...}, "brand": "MyBrand"}` (textarea)

### Edit mode (редактирование существующего сценария)

1. **Загрузка**:
   - Чекбоксы отмечаются по `analysis_types` из БД
   - Scope очищается от конфигов невыбранных типов
   - Textarea показывает только релевантные данные

2. **Пользователь добавляет новый analysis type**:
   - Ставит галочку → JavaScript добавляет дефолтный конфиг
   - Может отредактировать конфиг в textarea

3. **Пользователь убирает analysis type**:
   - Снимает галочку → JavaScript удаляет конфиг из scope

4. **Пользователь редактирует scope вручную**:
   - Может изменять конфиги выбранных типов
   - Может добавлять кастомные переменные
   - НЕ должен менять `analysis_types` (управляется checkboxes)

### Load Preset (загрузка пресета)

1. Нажатие на кнопку пресета
2. JavaScript загружает:
   - `content_types` → ставит галочки в content checkboxes
   - `analysis_types` → ставит галочки в analysis checkboxes
   - `scope` → заполняет textarea (конфиги + кастомные переменные)

---

## Примеры использования

### Пример 1: Создание сценария с sentiment analysis

**Действия пользователя**:
1. Выбрать checkbox "Sentiment Analysis"
2. (опционально) Изменить конфиг в scope JSON

**Результат в scope**:
```json
{
  "sentiment": {
    "categories": ["Позитивный", "Негативный", "Нейтральный", "Смешанный"],
    "detect_sarcasm": false,
    "emotion_analysis": true,
    "confidence_threshold": 0.7
  }
}
```

### Пример 2: Кастомизация конфига

**Действия**:
1. Выбрать "Topics Analysis"
2. Вручную изменить в scope:
```json
{
  "topics": {
    "categories": ["Политика", "Спорт"],
    "max_topics": 3
  }
}
```

**Результат**: При submit сохранится кастомный конфиг (не перезапишется дефолтным)

### Пример 3: Добавление кастомной переменной

**Действия**:
1. Выбрать "Brand Mentions"
2. Добавить в scope:
```json
{
  "brand_mentions": {
    "brand_names": [],
    "track_sentiment": true
  },
  "our_brand_name": "MyCompany",
  "competitor_brands": ["Competitor1", "Competitor2"]
}
```

**Результат**: Analysis конфиги + кастомные переменные сохраняются вместе

### Пример 4: Удаление analysis type

**Исходный scope**:
```json
{
  "sentiment": {...},
  "topics": {...},
  "keywords": {...}
}
```

**Действия**: Снять галочку "Topics Analysis"

**Результат**:
```json
{
  "sentiment": {...},
  "keywords": {...}
}
```

---

## Validation Rules

### Backend validation (app/models/bot_scenario.py)

✅ `analysis_types` - список строк (JSON array)
✅ `scope` - словарь (JSON object)
✅ Оба поля nullable

### Frontend validation (JavaScript)

✅ **Автоматическая очистка**: Конфиги невыбранных типов удаляются
✅ **Автоматическое добавление**: При выборе нового типа добавляется дефолтный конфиг
✅ **Сохранение кастомизаций**: Не перезаписываем существующие конфиги
✅ **JSON валидация**: При ошибке парсинга создается пустой объект

### Правила для разработчиков

1. **НЕ храните `analysis_types` в `scope`** - это отдельное поле
2. **Используйте новый формат** конфигов (без `_config` суффикса)
3. **Добавляйте дефолты** в `app/core/analysis_constants.py`
4. **Обновляйте `ANALYSIS_TYPE_DEFAULTS`** при добавлении нового analysis type

---

## Files Modified

### Backend
- ✅ `app/admin/views.py` - добавлены `analysis_defaults` и `all_analysis_types` в scaffold_form

### Frontend
- ✅ `app/templates/sqladmin/bot_scenario_form.html` - добавлена логика очистки scope

### Documentation
- ✅ `docs/ANALYSIS_TYPES_SCOPE_SYSTEM.md` - этот файл

---

## Testing

### Manual Testing

1. **Откройте существующий сценарий**:
```
http://0.0.0.0:8000/admin/bot-scenario/edit/6
```

2. **Проверьте**:
   - ✅ Scope JSON НЕ содержит конфиги невыбранных analysis types
   - ✅ Checkboxes отмечены для analysis_types из БД
   - ✅ Console показывает логи: "Keeping selected analysis config", "Removed unselected analysis config"

3. **Добавьте новый analysis type**:
   - Поставьте галочку на "Trends"
   - ✅ В scope JSON должен появиться `"trends": {...}` с дефолтными значениями

4. **Удалите analysis type**:
   - Снимите галочку с любого типа
   - ✅ Его конфиг должен исчезнуть из scope JSON

5. **Добавьте кастомную переменную**:
   - Вручную добавьте в scope: `"my_variable": "test"`
   - ✅ При изменении checkboxes переменная должна сохраниться

### Automated Testing

```python
# TODO: Добавить unit тесты для:
# - Очистки scope от невыбранных analysis configs
# - Автодобавления дефолтных конфигов
# - Сохранения кастомных переменных
# - Обработки старого формата (_config)
```

---

## FAQ

### Q: Почему scope показывает `topics_config`?

**A**: Это старый формат. JavaScript автоматически конвертирует его в новый формат (`topics`) при загрузке страницы.

### Q: Можно ли добавить analysis config вручную в scope?

**A**: Нет, analysis configs управляются через checkboxes. Добавляйте только кастомные переменные (не совпадающие с именами analysis types).

### Q: Что если я хочу изменить дефолтный конфиг?

**A**: Два способа:
1. Изменить глобально в `app/core/analysis_constants.py`
2. Изменить для конкретного сценария вручную в scope JSON после выбора checkbox

### Q: Как добавить новый analysis type?

**A**: 
1. Добавить enum в `app/types/enums/analysis_types.py`
2. Добавить дефолтный конфиг в `app/core/analysis_constants.py` → `ANALYSIS_TYPE_DEFAULTS`
3. Обновить checkbox в `bot_scenario_form.html` (автоматически из enum)

---

## Changelog

### Current Session
- ✅ Fixed: `scaffold_form` теперь передает `analysis_defaults` и `all_analysis_types`
- ✅ Fixed: JavaScript очищает scope от невыбранных analysis configs при загрузке
- ✅ Fixed: Сохраняются кастомизированные конфиги для выбранных types
- ✅ Added: Документация системы

---

**Author**: Factory Droid  
**Date**: Current Session
