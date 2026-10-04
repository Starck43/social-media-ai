# 🎯 Новый подход к сценариям: минимализм + чат-ассистент

## Ключевая идея

**Сценарий = "линза анализа"**, а не "точка принятия решений". После рефакторинга:

```
AgentScenario (ЧТО анализировать)
├── content_types: что собирать
├── analysis_types: какие метрики извлекать
├── scope: конфиг для каждой метрики
├── analyze_type: как группировать (themes/days/sources/users)
└── base_prompt + media_overrides: инструкция для LLM

AgentTask (КОГДА и ЧТО ДЕЛАТЬ)
├── cron_expr: расписание
├── sources: какие источники
├── trigger_type/config: когда реагировать
├── action_type: что делать (comment/dm/notify)
└── guards: лимиты, cooldown, black/whitelist
```

---

## 📋 Часть 1: План для IDE-агента (рефакторинг кода)

```markdown
# Задача: Рефакторинг AgentScenario + система чат-создания сценариев

## Архитектурный принцип
- `AgentScenario` = "как анализировать" (линза)
- `AgentTask` = "когда смотреть + что делать" (реакция)
- Чат-агент = интерактивный мастер создания сценариев через tool calling

## ЭТАП 1: Очистка AgentScenario (если не сделано ранее)

### 1.1 Удалить из модели `AgentScenario` поля реакции:
- `action_type`
- `rate_limit_per_hour`, `cooldown_seconds`, `requires_approval`
- `blacklist`, `whitelist`

### 1.2 Убедиться, что эти поля уже есть в `AgentTask` (согласно AGENT_TASKS.md).
Если их там нет — добавить. Проверить `app/core/triggers.py`, что он читает 
конфиг из AgentTask, а не из Scenario.

## ЭТАП 2: Упрощение промптов в AgentScenario (если не сделано и не запланировано в .agent/tasks.md ранее)

### 2.1 Заменить 5 отдельных полей:
```
text_prompt, image_prompt, video_prompt, audio_prompt, unified_summary_prompt
```
на:
```
base_prompt          — основная инструкция (обязательно)
media_overrides      — JSON: {"image": "...", "video": "..."} (опционально)
summary_prompt       — промпт для финальной сводки (опционально)
```

### 2.2 В `app/services/ai/prompts.py::get_prompt()`:
- Брать base_prompt как основу
- Если есть media_overrides[media_type] — добавлять как секцию "## Дополнительно для {media_type}"
- Подставлять переменные из AVAILABLE_VARIABLES (см. ЭТАП 3)

## ЭТАП 3: Реестр переменных и валидация

### 3.1 Создать `app/services/ai/prompt_variables.py`:
```python
AVAILABLE_VARIABLES = {
    "text": "Собранный контент (текст постов/комментариев)",
    "platform": "Название платформы (ВКонтакте, Telegram)",
    "date_range": "Период анализа (2026-09-28 - 2026-10-05)",
    "trigger_condition": "Описание условия реакции (из AgentTask)",
    "source_name": "Название источника",
    "scenario_name": "Название сценария",
    # Scope-derived (любые ключи из scope.<type>)
    "max_keywords": "scope.keywords.max_keywords",
    "max_topics": "scope.topics.max_topics",
    "sentiment_categories": "scope.sentiment.categories (через запятую)",
}

def validate_prompt(prompt: str) -> list[str]:
    """Возвращает список неизвестных переменных {var}"""
    found = set(re.findall(r"\{(\w+)\}", prompt))
    return [v for v in found if v not in AVAILABLE_VARIABLES]
```

### 3.2 В `prompts.py::get_prompt()`:
- Детекция наличия JSON-блока в промпте через regex `\{[^{}]*"[^"]+"\s*:` вместо ключевых слов
- Если пользователь уже описал JSON-структуру — не добавлять COMMON_FIELDS автоматически
- Иначе — добавлять согласно `json_schema_builder`

### 3.3 Валидация при сохранении сценария (в Pydantic схеме):
- Вызывать `validate_prompt()` для base_prompt и media_overrides
- Возвращать warning (не error) с подсказкой: "Переменная {foo} не распознана. Доступные: ..."

## ЭТАП 4: Пресеты сценариев (scenario_templates)

Создать `app/services/ai/scenario_templates.py` с готовыми шаблонами или дополнить и изменить существующие (в админке они в шаблоне для сценариев уже добавлялись):

```python
TEMPLATES = {
    "brand_monitoring": {
        "name": "Мониторинг бренда",
        "content_types": ["posts", "comments", "mentions"],
        "analysis_types": ["sentiment", "brand_mentions", "keywords"],
        "analyze_type": "themes",
        "scope": {"keywords": {"max_keywords": 10}},
        "base_prompt": "Ты — аналитик бренда. Отслеживай упоминания и тональность.",
    },
    "competitor_watch": {...},
    "customer_support": {...},
    "trend_spotter": {...},
    "toxicity_guard": {...},
}
```

## ЭТАП 5: Agent Tools для чат-агента

Добавить в `app/agent/tools/`:

### 5.1 `scenario_list()` — список сценариев workspace
### 5.2 `scenario_get(id)` — детали сценария
### 5.3 `scenario_create_from_template(template_key, name, overrides)` — из шаблона
### 5.4 `scenario_create_interactive(...)` — полный набор полей
### 5.5 `scenario_update(id, changes)` — частичное обновление
### 5.6 `scenario_clone(source_id, new_name, changes)` — клонирование
### 5.7 `scenario_delete(id)` — с подтверждением
### 5.8 `scenario_suggest_prompt(task_description)` — LLM генерирует промпт по описанию
### 5.9 `scenario_validate_prompt(prompt)` — проверка переменных

Каждый tool должен:
- Возвращать структурированный ответ (JSON)
- Использовать confirmation flow для опасных действий
- Проверять квоты через `limits.check_quota(tenant, 'scenarios')`

## Правила выполнения
- Сохранить TenantScopedMixin и фильтрацию по tenant_id
- Все изменения БД через Alembic
- Начать с ЭТАПА 1, ждать подтверждения
```

---

## 💬 Часть 2: Как чат-агент помогает создавать сценарии

### UX-паттерны взаимодействия

#### **Паттерн 1: Быстрое создание из шаблона**
```
👤: Хочу следить за упоминаниями моего бренда в VK
🤖: Нашёл подходящий шаблон "Мониторинг бренда". 
    Он включает:
    • Анализ тональности и упоминаний бренда
    • Ключевые слова (топ-10)
    • Группировка по темам
    
    Создать сценарий "Мой бренд VK" на его основе? [Да/Нет/Настроить]
👤: Да
🤖: ✅ Создан сценарий #12 "Мой бренд VK"
    Осталось привязать к задаче (расписанию). 
    Команда: /task_add или скажи "проверяй каждый час"
```

#### **Паттерн 2: Диалоговый мастер (пошаговый)**
```
👤: Создай сценарий для анализа конкурентов
🤖: Ок, пройдёмся по шагам.
    
    1️⃣ Какие типы контента собирать?
       [a] Посты и комментарии (рекомендую)
       [b] Только посты  
       [c] Всё включая видео и stories
       
👤: a
🤖: 2️⃣ Какие метрики анализировать? (можно несколько)
       • competitor — активность конкурентов
       • sentiment — тональность
       • keywords — ключевые слова
       • trends — тренды
       
👤: competitor, sentiment, trends
🤖: 3️⃣ Как группировать результаты?
       [themes] по темам
       [days] по дням ← рекомендую для конкурентов
       [sources] по источникам
       
👤: days
🤖: 4️⃣ Опиши задачу своими словами — я сгенерирую промпт.
    Например: "Сравнивать нашу компанию X с конкурентами Y и Z, 
    отслеживать их новые продукты и реакцию аудитории"
    
👤: Отслеживай конкурентов: Coca-Cola, Pepsi. Сравнивай с нашим брендом Fanta.
🤖: 📝 Сгенерировал промпт:
    ┌─────────────────────────────────────┐
    │ Ты — аналитик конкурентной среды... │
    │ (показать полностью? /show_prompt)  │
    └─────────────────────────────────────┘
    
    Переменные: {text}, {platform}, {date_range}
    Scope: {"competitor": {"targets": ["Coca-Cola","Pepsi","Fanta"]}}
    
    Сохранить? [Да/Править/Отмена]
```

#### **Паттерн 3: Клонирование + модификация**
```
👤: Скопируй сценарий "Мониторинг VK" для Telegram
🤖: Клонировал сценарий #12 → #15 "Мониторинг Telegram"
    Изменил: platform-специфичные подсказки в промпте.
    Привязать к источникам Telegram?
```

#### **Паттерн 4: Объяснение и обучение**
```
👤: Что такое scope в сценарии?
🤖: Scope — это JSON-конфигурация для каждого типа анализа.
    
    Пример:
    {
      "keywords": {"max_keywords": 10},
      "sentiment": {"categories": ["Позитив", "Негатив", "Нейтрально"]},
      "competitor": {"targets": ["BrandA", "BrandB"]}
    }
    
    Значения из scope автоматически попадают в промпт как переменные:
    {max_keywords} → 10
    {sentiment_categories} → "Позитив, Негатив, Нейтрально"
    
    Показать scope твоего сценария #12?
```

### Технические инструменты агента

#### 1. **Tools (Function Calling)**
| Tool | Назначение | Требует подтверждения |
|------|-----------|----------------------|
| `scenario_list` | Список сценариев workspace | ❌ |
| `scenario_get` | Детали одного сценария | ❌ |
| `scenario_templates` | Доступные шаблоны | ❌ |
| `scenario_suggest_prompt` | LLM генерирует промпт по описанию | ❌ |
| `scenario_validate_prompt` | Проверка переменных | ❌ |
| `scenario_create` | Создание (из шаблона или полного набора) | ✅ |
| `scenario_update` | Изменение полей | ✅ |
| `scenario_clone` | Клонирование с модификациями | ✅ |
| `scenario_delete` | Удаление | ✅ |

#### 2. **Agent Memory для сценариев**
Сохранять в `agent_memory` предпочтения пользователя:
```
scope=scenario_prefs, key=default_language, value=ru
scope=scenario_prefs, key=preferred_analyze_type, value=days
scope=scenario_prefs, key=brands, value=["Fanta","Sprite"]
```
При следующем создании сценария агент **автоматически подставит** эти значения.

#### 3. **System Prompt для чат-агента (фрагмент)**
```markdown
## Создание сценариев
Когда пользователь хочет создать или изменить сценарий:

1. СНАЧАЛА узнай цель: что анализировать и зачем
2. Предложи подходящий шаблон из scenario_templates
3. Если шаблона нет — проведи через диалоговый мастер:
   - content_types → analysis_types → analyze_type → описание задачи
4. Сгенерируй промпт через scenario_suggest_prompt
5. Проверь переменные через scenario_validate_prompt
6. Покажи превью и запроси подтверждение
7. Используй scenario_create с флагом pending_confirmation

НИКОГДА не создавай сценарий молча — всегда показывай превью.
Используй agent_memory для запоминания предпочтений между сессиями.
```

#### 4. **Confirmation Flow для опасных действий**
Уже есть инфраструктура `pending_confirmation` в `agent_sessions.state`:
```json
{
  "pending_confirmation": {
    "action": "scenario_create",
    "payload": {"name": "...", "analysis_types": [...]},
    "preview": "📝 Будет создан сценарий...",
    "created_at": "..."
  }
}
```
Пользователь отвечает "да"/"нет" — агент исполняет или отменяет.

---

## 🎯 Итоговый чек-лист для агента в IDE

```markdown
# Приоритеты реализации

## MUST HAVE (этап 2 — упрощение промптов)
[ ] Заменить 5 промптов на base_prompt + media_overrides + summary_prompt
[ ] Создать prompt_variables.py с AVAILABLE_VARIABLES
[ ] Валидация переменных при сохранении
[ ] Детекция JSON по парсингу, не по ключевым словам

## SHOULD HAVE (этап 3 — чат-агент)
[ ] scenario_templates.py с 5+ пресетами
[ ] Tools: scenario_list, scenario_get, scenario_templates
[ ] Tools: scenario_create, scenario_update, scenario_clone, scenario_delete
[ ] Tool: scenario_suggest_prompt (LLM-генерация промпта)
[ ] Tool: scenario_validate_prompt
[ ] Confirmation flow для create/update/delete
[ ] Agent Memory для предпочтений (scope=scenario_prefs)
[ ] Обновить system prompt агента — секция про сценарии

## NICE TO HAVE (этап 4 — улучшение UX)
[ ] Web UI: мастер создания сценария (аналогично чат-UX)
[ ] Галерея публичных шаблонов (community templates)
[ ] A/B тестирование промптов (сохранять оба варианта)
[ ] Метрики качества сценариев (какие дают лучшие результаты)
```

---

## 💡 Ключевой инсайт

**Чат-агент становится "лицом" системы создания сценариев.** Вместо того чтобы заставлять пользователя разбираться в 15 полях админки, он:

1. **Понимает намерение** ("хочу следить за конкурентами")
2. **Подбирает шаблон** или проводит через мастер
3. **Генерирует промпт** через LLM по простому описанию
4. **Валидирует** и показывает превью
5. **Запоминает предпочтения** между сессиями
6. **Требует подтверждения** перед опасными действиями

Админка остаётся для **продвинутых пользователей** и **отладки**, а 90% сценариев будут создаваться через чат — это и есть "AI-native UX".
