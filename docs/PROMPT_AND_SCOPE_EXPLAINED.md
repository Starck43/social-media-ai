# Что добавляется автоматически VS что руками в промпте

**Вопрос**: Структура JSON в промпте формируется автоматически или нужно руками указывать?

**Ответ**: **ЧАСТИЧНО АВТОМАТИЧЕСКИ** - система умная и добавляет недостающее!

---

## 🔄 Как работает процесс

### **1. Ваш Text Prompt** (руками)

Вы пишете в админке:

```
Ты - аналитик социальных сетей. Проанализируй активность пользователя.

ЗАДАЧА:
Для КАЖДОГО события создай КРАТКИЙ АНОНС.

ФОРМАТ ОТВЕТА - строгий JSON:
{
  "analysis_title": "...",
  "events": [...]
}
```

### **2. Система автоматически добавляет**

#### ✅ **COMMON_FIELDS** (всегда)

Если в вашем промпте НЕТ упоминания этих полей, система **АВТОМАТИЧЕСКИ ДОБАВИТ**:

```python
# app/services/ai/json_schema_builder.py
COMMON_FIELDS = {
    'analysis_title': 'краткий заголовок анализа (3-7 слов)',
    'analysis_summary': 'развернутое описание ситуации (2-4 предложения)',
}
```

#### ✅ **Analysis Types** (из формы)

Если вы выбрали в форме `Analysis Types: ["sentiment", "keywords", "topics"]`, то система добавит:

```json
{
  "sentiment_score": "число от 0.0 до 1.0",
  "sentiment_label": "Позитивный|Нейтральный|Негативный|Смешанный",
  "keywords": ["ключевое слово 1", "..."],
  "main_topics": ["тема 1", "тема 2"]
}
```

#### ✅ **Scope параметры** (из JSON поля Scope)

Если в Scope указано:

```json
{
  "sentiment": {
    "categories": ["Позитивный", "Негативный", "Нейтральный", "Смешанный"]
  },
  "keywords": {
    "max_keywords": 10
  }
}
```

То система **подставит эти значения** в schema:

```json
{
  "sentiment_label": "одно из: 'Позитивный', 'Негативный', 'Нейтральный', 'Смешанный'",
  "keywords": "список из 10 ключевых слов"
}
```

### **3. Финальный промпт** (отправляется в LLM)

Система **объединяет**:

1. **Ваш Text Prompt**
2. **COMMON_FIELDS** (если нет в промпте)
3. **Analysis Types schema** (на основе выбора в форме)
4. **Scope конфигурация** (из JSON поля Scope)
5. **Переменные** (подставляет `{text}`, `{platform}`, и т.д.)

---

## 📝 Что нужно руками, что автоматически

### ✅ **Автоматически формируется**

| Что | Откуда | Пример |
|-----|--------|--------|
| **analysis_title** | COMMON_FIELDS | Всегда добавляется |
| **analysis_summary** | COMMON_FIELDS | Всегда добавляется |
| **sentiment_score** | Analysis Types | Если выбран "sentiment" |
| **sentiment_label** | Analysis Types + Scope | Категории из Scope |
| **keywords** | Analysis Types | Если выбран "keywords" |
| **main_topics** | Analysis Types | Если выбран "topics" |
| **Подстановка {text}** | Система | Автоматически |
| **Подстановка {platform}** | Система | Автоматически |
| **Подстановка {date_range}** | Система | Автоматически |

### 🖊️ **Нужно руками указывать**

| Что | Где | Пример |
|-----|-----|--------|
| **Кастомные поля** | Text Prompt | `"events": [...]` |
| **Структура events** | Text Prompt | `{"type": "post", "annotation": "..."}` |
| **Примеры анонсов** | Text Prompt | `✅ "Хороший анонс..."` |
| **Инструкции** | Text Prompt | `"Для КАЖДОГО события..."` |
| **event_based флаг** | Scope | `{"event_based": true}` |
| **Кастомные категории** | Scope | `{"sentiment": {"categories": [...]}}` |

---

## 🎯 Конкретно для Scenario #10

### **Что вы указываете руками в Text Prompt**:

```
ФОРМАТ ОТВЕТА - строгий JSON:
{
  "analysis_title": "...",
  "analysis_summary": "...",
  "events": [                    // ⬅️ РУКАМИ!
    {
      "type": "post|comment|like",     // ⬅️ РУКАМИ!
      "time": "...",                    // ⬅️ РУКАМИ!
      "annotation": "...",              // ⬅️ РУКАМИ!
      "sentiment": 0.0-1.0,            // ⬅️ РУКАМИ (или система добавит)
      "keywords": [...]                 // ⬅️ РУКАМИ (или система добавит)
    }
  ],
  "statistics": {               // ⬅️ РУКАМИ!
    "total_posts": 0,
    "total_comments": 0
  }
}
```

**Почему руками?** Потому что `events` и `statistics` - это **КАСТОМНЫЕ** поля, которых нет в стандартных COMMON_FIELDS.

### **Что система добавит автоматически**:

Если в вашем промпте нет этих полей, система добавит:

```json
{
  "analysis_title": "краткий заголовок (3-7 слов)",     // АВТОМАТИЧЕСКИ
  "analysis_summary": "описание (2-4 предложения)",     // АВТОМАТИЧЕСКИ
  "sentiment_score": "число от 0.0 до 1.0",            // АВТОМАТИЧЕСКИ (если analysis_types = ["sentiment"])
  "sentiment_label": "Позитивный|Нейтральный|...",     // АВТОМАТИЧЕСКИ
  "keywords": ["ключ 1", "ключ 2"],                    // АВТОМАТИЧЕСКИ (если analysis_types = ["keywords"])
  "main_topics": ["тема 1", "тема 2"]                  // АВТОМАТИЧЕСКИ (если analysis_types = ["topics"])
}
```

---

## 🔍 Как проверить что система добавит

### **Пример 1: Минимальный промпт**

**Ваш Text Prompt** (руками):

```
Проанализируй активность пользователя.
```

**Что отправится в LLM** (автоматически):

```
Проанализируй активность пользователя.

ВАЖНО: Верни результат СТРОГО в JSON формате:
{
  "analysis_title": "краткий заголовок анализа (3-7 слов)",
  "analysis_summary": "развернутое описание ситуации (2-4 предложения)",
  "sentiment_score": "число от 0.0 (негатив) до 1.0 (позитив)",
  "sentiment_label": "одно из: 'Позитивный', 'Нейтральный', 'Негативный', 'Смешанный'",
  "keywords": "список из 15 ключевых слов",
  "main_topics": "список из 5 главных тем"
}
```

**Откуда**:
- `analysis_title`, `analysis_summary` → COMMON_FIELDS
- `sentiment_*` → Analysis Types = ["sentiment"] + Scope
- `keywords` → Analysis Types = ["keywords"] + Scope
- `main_topics` → Analysis Types = ["topics"] + Scope

### **Пример 2: Полный промпт с кастомными полями**

**Ваш Text Prompt** (руками):

```
Проанализируй активность пользователя.

ФОРМАТ ОТВЕТА - строгий JSON:
{
  "analysis_title": "Активность за дату",
  "events": [
    {
      "type": "post",
      "annotation": "краткий анонс"
    }
  ]
}
```

**Что отправится в LLM** (автоматически):

```
Проанализируй активность пользователя.

ФОРМАТ ОТВЕТА - строгий JSON:
{
  "analysis_title": "Активность за дату",
  "events": [
    {
      "type": "post",
      "annotation": "краткий анонс"
    }
  ]
}
```

**Система НЕ ДОБАВИТ** COMMON_FIELDS, потому что:
- Промпт **УЖЕ СОДЕРЖИТ** JSON формат
- Обнаружено по ключевым словам: `"формат"`, `"json"`

**НО**: Если в Scope указано `"event_based": true`, то analyzer может использовать другую логику обработки!

---

## ⚙️ Scope - что это и как используется

### **Scope** - это конфигурация для анализа

**В форме** (JSON поле):

```json
{
  "event_based": true,              // ⬅️ Флаг: режим детализации по событиям
  "max_events_per_analysis": 50,    // ⬅️ Ограничение количества
  "include_target_info": true,       // ⬅️ Включать инфу о target
  "annotation_length": "brief",      // ⬅️ Длина аннотации
  
  "sentiment": {                     // ⬅️ Конфиг для sentiment анализа
    "categories": ["Позитивный", "Нейтральный", "Негативный", "Смешанный"]
  },
  "keywords": {                      // ⬅️ Конфиг для keywords анализа
    "max_keywords": 10
  },
  "topics": {                        // ⬅️ Конфиг для topics анализа
    "max_topics": 5
  }
}
```

### **Как используется Scope**:

#### 1. **В analyzer.py**

```python
# app/services/ai/analyzer.py
async def analyze_content(self, content, source, bot_scenario):
    # Проверяет флаг event_based
    is_event_based = False
    if bot_scenario and bot_scenario.scope:
        is_event_based = bot_scenario.scope.get('event_based', False)
    
    if is_event_based:
        # Режим детализации по событиям
        return await self._analyze_events(content, source, bot_scenario)
    else:
        # Режим агрегации (обычный)
        return await self._analyze_aggregated(content, source, bot_scenario)
```

#### 2. **В JSON schema builder**

```python
# app/services/ai/json_schema_builder.py
def build_schema(cls, analysis_types, scope):
    schema = {}
    
    # Добавить COMMON_FIELDS
    schema.update(cls.COMMON_FIELDS)
    
    # Для каждого analysis_type
    for analysis_type in analysis_types:
        # Получить конфиг из scope
        type_config = scope.get(analysis_type, {})  # ⬅️ Берёт из Scope!
        
        # Пример: для keywords
        max_keywords = type_config.get('max_keywords', 15)  # ⬅️ Из Scope!
        schema['keywords'] = f"список из {max_keywords} ключевых слов"
```

#### 3. **В промпте** (переменные)

Если в Text Prompt используете переменную:

```
Максимум ключевых слов: {max_keywords}
```

То система подставит значение из `scope.keywords.max_keywords`:

```
Максимум ключевых слов: 10
```

---

## 🎯 Итоговая схема для Scenario #10

### **В форме указываете**:

#### **Content Types** (checkboxes/JSON):
```json
["posts", "comments", "reactions"]
```
→ Определяет **ЧТО собирать** (VKClient)

#### **Analysis Types** (checkboxes/JSON):
```json
["sentiment", "keywords", "topics"]
```
→ Определяет **КАКИЕ поля добавить** в JSON schema (автоматически)

#### **Scope** (JSON):
```json
{
  "event_based": true,
  "max_events_per_analysis": 50,
  "include_target_info": true,
  
  "sentiment": {
    "categories": ["Позитивный", "Нейтральный", "Негативный", "Смешанный"]
  },
  "keywords": {
    "max_keywords": 10
  }
}
```
→ Определяет:
1. **Режим анализа** (`event_based: true`)
2. **Конфигурацию** для каждого analysis_type
3. **Переменные** для промпта

#### **Text Prompt** (текст):
```
Ты - аналитик. Проанализируй активность.

ФОРМАТ ОТВЕТА - строгий JSON:
{
  "analysis_title": "...",
  "events": [...]  // ⬅️ КАСТОМНОЕ ПОЛЕ (руками!)
}
```
→ Определяет:
1. **Инструкции** для LLM
2. **Кастомные поля** (events, statistics)
3. **Примеры** анонсов
4. **Формат** ответа

### **Система автоматически**:

1. **Подставляет переменные**:
   - `{text}` → Собранный контент
   - `{platform}` → "ВКонтакте"
   - `{date_range}` → "2025-10-15 - 2025-10-18"

2. **Добавляет COMMON_FIELDS** (если нет в промпте):
   - `analysis_title`
   - `analysis_summary`

3. **Добавляет Analysis Types schema**:
   - `sentiment_score`, `sentiment_label` (из "sentiment")
   - `keywords` (из "keywords")
   - `main_topics` (из "topics")

4. **Использует Scope для конфигурации**:
   - `sentiment.categories` → категории для sentiment_label
   - `keywords.max_keywords` → количество keywords
   - `event_based` → переключает режим анализа

---

## ✅ Рекомендация для Scenario #10

### **НЕ ПОЛАГАЙТЕСЬ** на автоматику

**Почему?** Потому что для event-based анализа нужна **специфическая структура** JSON с полями `events[]` и `statistics`, которых НЕТ в стандартных COMMON_FIELDS.

### **УКАЖИТЕ РУКАМИ** в Text Prompt:

```
ФОРМАТ ОТВЕТА - строгий JSON:
{
  "analysis_title": "Активность пользователя за <дата>",
  "analysis_summary": "Общее резюме...",
  "events": [                    // ⬅️ ОБЯЗАТЕЛЬНО РУКАМИ!
    {
      "type": "post|comment|like",
      "time": "ISO datetime",
      "event_id": "ID события",
      "target": {...},
      "annotation": "...",
      "sentiment": 0.0-1.0,
      "keywords": [...]
    }
  ],
  "statistics": {...}             // ⬅️ ОБЯЗАТЕЛЬНО РУКАМИ!
}
```

**Почему так?** Потому что:
1. Система знает про `sentiment`, `keywords`, `topics` (стандартные)
2. Система **НЕ ЗНАЕТ** про `events` и `statistics` (кастомные)
3. Если не указать руками → LLM может вернуть неправильный формат

---

## 🔍 Как проверить что добавляется

### **Способ 1: Логи**

Запустить с DEBUG уровнем:

```bash
export LOG_LEVEL=DEBUG
python -m cli.main collect run --source-id 16 --verbose
```

Искать в логах:
```
Final prompt sent to LLM:
[... весь промпт ...]
```

### **Способ 2: Код**

Добавить `logger.debug()` в `prompts.py`:

```python
# app/services/ai/prompts.py
@staticmethod
def get_prompt(media_type, scenario, **context):
    # ... existing code ...
    
    # После формирования промпта
    logger.debug(f"Final prompt for {media_type}: {prompt}")
    
    return prompt
```

---

## 📊 Таблица: Что откуда берётся

| Поле в JSON | Откуда | Автоматически? |
|-------------|--------|----------------|
| **analysis_title** | COMMON_FIELDS или Text Prompt | ✅ Автоматически (если нет в промпте) |
| **analysis_summary** | COMMON_FIELDS или Text Prompt | ✅ Автоматически (если нет в промпте) |
| **sentiment_score** | Analysis Types = ["sentiment"] | ✅ Автоматически |
| **sentiment_label** | Analysis Types + Scope | ✅ Автоматически |
| **keywords** | Analysis Types = ["keywords"] + Scope | ✅ Автоматически |
| **main_topics** | Analysis Types = ["topics"] + Scope | ✅ Автоматически |
| **events** | Text Prompt (кастомное) | ❌ Руками! |
| **statistics** | Text Prompt (кастомное) | ❌ Руками! |
| **{text}** | Система (подставляет контент) | ✅ Автоматически |
| **{platform}** | Система (название платформы) | ✅ Автоматически |
| **{date_range}** | Система (период анализа) | ✅ Автоматически |
| **event_based режим** | Scope.event_based | ✅ Автоматически (в analyzer.py) |

---

## 🎓 Выводы

### 1. **Стандартные поля** → Автоматически

Если используете стандартные analysis types (`sentiment`, `keywords`, `topics`), то система **сама добавит** соответствующие поля в JSON schema.

### 2. **Кастомные поля** → Руками

Если нужны специфические поля (например, `events[]` для event-based анализа), то **ОБЯЗАТЕЛЬНО** указывать их в Text Prompt.

### 3. **Scope** → Конфигурация

`Scope` НЕ добавляет поля в JSON, а **настраивает** их:
- `event_based: true` → переключает режим анализа
- `sentiment.categories` → какие категории использовать
- `keywords.max_keywords` → сколько keywords извлекать

### 4. **Переменные** → Автоматически

Переменные `{text}`, `{platform}`, `{date_range}` **всегда** подставляются системой.

---

**Для Scenario #10**: Используйте **ПОЛНЫЙ промпт** из `SCENARIO_10_QUICK_COPY_PASTE.md`, где все кастомные поля (`events`, `statistics`) указаны руками!

**Автор**: Factory Droid  
**Дата**: Current Session
