# Multi-LLM Implementation Summary

## Обзор изменений

Реализована архитектура для гибкого управления LLM провайдерами с поддержкой анализа различных типов контента (текст, фото, видео) через разные AI модели.

## Основные компоненты

### 1. Новые модели

#### `LLMProvider` - Хранение конфигураций LLM
- Поддерживаемые типы: DeepSeek, OpenAI, Anthropic, Google, Mistral, Custom
- Capabilities: text, image, video, audio
- Гибкая конфигурация (temperature, max_tokens, etc.)
- API ключи хранятся в env переменных

**Файлы:**
- `app/models/llm_provider.py`
- `app/models/managers/llm_provider_manager.py`

### 2. Обновленные модели

#### `BotScenario` - Связь с LLM провайдерами
Добавлены поля:
- `text_llm_provider_id` - для текстового анализа
- `image_llm_provider_id` - для анализа изображений
- `video_llm_provider_id` - для анализа видео

**Файл:** `app/models/bot_scenario.py`

### 3. LLM Client Abstraction

#### Единый интерфейс для работы с разными LLM
- `LLMClient` - абстрактный базовый класс
- `DeepSeekClient` - клиент для DeepSeek (текст)
- `OpenAIClient` - клиент для OpenAI (текст + vision)
- `LLMClientFactory` - фабрика для создания клиентов

**Файл:** `app/services/ai/llm_client.py`

### 4. Content Classification

#### Классификация контента по типам
- `ContentClassifier` - разделяет контент на text/image/video
- Извлекает media URLs
- Подготавливает контент для LLM

**Файл:** `app/services/ai/content_classifier.py`

### 5. Prompts для разных типов

#### Специализированные промпты
- `build_text_prompt()` - для текстового анализа
- `build_image_prompt()` - для анализа изображений (что определять на фото)
- `build_video_prompt()` - для анализа видео (что определять в видео)
- `build_unified_summary_prompt()` - для объединения результатов

**Файл:** `app/services/ai/prompts.py`

### 6. AIAnalyzerV2

#### Новый анализатор с multi-LLM поддержкой
- Классифицирует контент по типам
- Выбирает подходящий LLM для каждого типа
- Параллельный анализ разными LLM
- Объединяет результаты в единый summary
- Сохраняет трассировку всех LLM вызовов

**Файл:** `app/services/ai/analyzer_v2.py`

### 7. API Endpoints

#### CRUD операции для LLM провайдеров
- `POST /api/v1/llm/llm-providers/` - создать
- `GET /api/v1/llm/llm-providers/` - список
- `GET /api/v1/llm/llm-providers/{id}` - получить
- `PATCH /api/v1/llm/llm-providers/{id}` - обновить
- `DELETE /api/v1/llm/llm-providers/{id}` - удалить

**Файлы:**
- `app/api/v1/endpoints/llm_providers.py`
- `app/schemas/llm_provider.py`

### 8. Обновленная конфигурация

#### `app/core/config.py`
Добавлены API ключи для разных провайдеров:
```python
DEEPSEEK_API_KEY: str = ""  # Legacy
OPENAI_API_KEY: str = ""
ANTHROPIC_API_KEY: str = ""
GOOGLE_API_KEY: str = ""
```

### 9. Новые enum типы

#### `app/types/models.py`
- `LLMProviderType` - типы LLM провайдеров
- `MediaType` - типы медиа контента

### 10. Миграция базы данных

**Файл:** `migrations/versions/20251014_000000_add_llm_providers.py`

Изменения:
- Создан enum `llm_provider_type`
- Создана таблица `llm_providers`
- Добавлены поля в `bot_scenarios`
- Создан default DeepSeek провайдер

## Архитектурные решения

### 1. Гибкость
- LLM провайдеры хранятся в БД, можно добавлять новые без изменения кода
- Сценарии могут использовать разные LLM для разных типов контента
- Легко добавить новый LLM через LLMClientFactory

### 2. Обратная совместимость
- Старый AIAnalyzer автоматически использует AIAnalyzerV2
- Если провайдер не указан, используется default
- Legacy настройки (DEEPSEEK_API_KEY) поддерживаются

### 3. Масштабируемость
- Каждый тип контента обрабатывается независимо
- Возможность параллельного анализа
- Поддержка custom LLM провайдеров

### 4. Трассировка
- Полное логирование всех LLM вызовов
- Сохранение промптов и ответов
- Версионирование анализа (3.0-multi-llm)

## Структура анализа

### Input (контент)
```python
content = [
    {
        "text": "...",
        "date": "...",
        "attachments": [
            {"type": "photo", "url": "..."},
            {"type": "video", "url": "..."}
        ]
    }
]
```

### Processing
1. **Classification** - разделение по типам (text/image/video)
2. **LLM Selection** - выбор провайдера для каждого типа
3. **Analysis** - анализ каждого типа своим LLM
4. **Unification** - объединение результатов в summary

### Output (AIAnalytics)
```json
{
  "multi_llm_analysis": {
    "text_analysis": {...},
    "image_analysis": {...},
    "video_analysis": {...}
  },
  "unified_summary": {
    "overall_sentiment": "...",
    "main_themes": [...],
    "key_insights": [...],
    "content_strategy_recommendations": [...]
  },
  "analysis_metadata": {
    "analysis_version": "3.0-multi-llm",
    "llm_providers_used": 3
  }
}
```

## Использование

### Пример 1: Настройка LLM провайдеров

```python
# Создать провайдер для текста
text_provider = await LLMProvider.objects.create(
    name="DeepSeek Chat",
    provider_type="deepseek",
    api_url="https://api.deepseek.com/v1/chat/completions",
    api_key_env="DEEPSEEK_API_KEY",
    model_name="deepseek-chat",
    capabilities=["text"],
    is_active=True
)

# Создать провайдер для изображений
image_provider = await LLMProvider.objects.create(
    name="GPT-4 Vision",
    provider_type="openai",
    api_url="https://api.openai.com/v1/chat/completions",
    api_key_env="OPENAI_API_KEY",
    model_name="gpt-4-vision-preview",
    capabilities=["text", "image"],
    config={"temperature": 0.1, "max_tokens": 3000},
    is_active=True
)
```

### Пример 2: Настройка сценария

```python
scenario = await BotScenario.objects.create(
    name="Комплексный анализ контента",
    content_types=["posts", "videos", "comments"],
    analysis_types=["sentiment", "topics", "engagement"],
    text_llm_provider_id=text_provider.id,
    image_llm_provider_id=image_provider.id,
    video_llm_provider_id=image_provider.id,
    ai_prompt="Проанализируй контент с фокусом на {analysis_types}..."
)

# Привязать к источнику
source.bot_scenario_id = scenario.id
await source.save()
```

### Пример 3: Анализ контента

```python
from app.services.ai.analyzer import AIAnalyzer

analyzer = AIAnalyzer()  # Автоматически использует V2

# Анализировать контент
result = await analyzer.analyze_content(
    content=collected_content,
    source=source
)

# Результат содержит анализ от всех LLM + unified summary
print(result.summary_data['unified_summary'])
```

## Файлы проекта

### Новые файлы
```
app/
├── models/
│   ├── llm_provider.py                     # Модель LLMProvider
│   └── managers/
│       └── llm_provider_manager.py         # Manager для LLMProvider
├── services/
│   └── ai/
│       ├── analyzer_v2.py                  # Новый анализатор
│       ├── llm_client.py                   # LLM клиенты
│       ├── content_classifier.py           # Классификатор контента
│       └── prompts.py                      # Промпты для разных типов
├── api/
│   └── v1/
│       └── endpoints/
│           └── llm_providers.py            # API endpoints
└── schemas/
    └── llm_provider.py                     # Pydantic схемы

migrations/
└── versions/
    └── 20251014_000000_add_llm_providers.py  # Миграция БД

docs/
└── MULTI_LLM_SYSTEM.md                     # Документация
```

### Обновленные файлы
```
app/
├── models/
│   ├── __init__.py                         # +LLMProvider
│   └── bot_scenario.py                     # +llm_provider_id поля
├── types/
│   └── models.py                           # +LLMProviderType, MediaType
├── core/
│   └── config.py                           # +API ключи
├── services/
│   └── ai/
│       └── analyzer.py                     # Интеграция с V2
└── api/
    └── v1/
        └── entry.py                        # +llm_providers router
```

## Следующие шаги

1. **Admin панель** - добавить UI для управления LLM провайдерами
2. **Тестирование** - протестировать с реальными данными
3. **Мониторинг** - настроить метрики использования LLM
4. **Оптимизация** - кэширование результатов, rate limiting
5. **Расширение** - добавить Anthropic, Google, Mistral клиенты

## Преимущества

✅ **Гибкость** - легко добавлять новые LLM без изменения кода
✅ **Специализация** - каждый тип контента обрабатывается оптимальным LLM
✅ **Масштабируемость** - независимый анализ разных типов контента
✅ **Целостность** - сохранена обратная совместимость
✅ **Трассировка** - полный audit trail всех LLM вызовов
✅ **Объединение** - unified summary из разных источников анализа

## Важные замечания

⚠️ **API ключи** - должны быть в .env файле
⚠️ **Миграция** - обязательно выполнить `alembic upgrade head`
⚠️ **Permissions** - созданы автоматически для LLMProvider CRUD
⚠️ **Default провайдер** - создается автоматически при миграции
