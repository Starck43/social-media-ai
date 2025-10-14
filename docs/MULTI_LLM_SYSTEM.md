# Multi-LLM System Documentation

## Обзор

Система поддержки нескольких LLM провайдеров для анализа различных типов контента (текст, изображения, видео).

## Архитектура

### 1. Модель LLMProvider

Хранит конфигурации различных LLM провайдеров:

```python
class LLMProvider:
    id: int
    name: str  # Название провайдера
    description: str  # Описание
    provider_type: LLMProviderType  # deepseek, openai, anthropic, google, mistral, custom
    api_url: str  # API endpoint
    api_key_env: str  # Название переменной окружения с API ключом
    model_name: str  # Название модели
    capabilities: List[str]  # ["text", "image", "video"]
    config: Dict  # Дополнительная конфигурация (temperature, max_tokens, etc.)
    is_active: bool
```

### 2. Обновленная модель BotScenario

Добавлены связи с LLM провайдерами для разных типов контента:

```python
class BotScenario:
    # ... existing fields ...
    text_llm_provider_id: Optional[int]
    image_llm_provider_id: Optional[int]
    video_llm_provider_id: Optional[int]
```

### 3. LLMClient абстракция

Единый интерфейс для работы с разными LLM:

- `LLMClient` - абстрактный базовый класс
- `DeepSeekClient` - клиент для DeepSeek (текст)
- `OpenAIClient` - клиент для OpenAI (текст + изображения)
- `LLMClientFactory` - фабрика для создания клиентов

### 4. AIAnalyzerV2

Новый анализатор с поддержкой multi-LLM:

- Классифицирует контент по типам (text, image, video)
- Выбирает подходящий LLM провайдер для каждого типа
- Анализирует каждый тип контента соответствующим LLM
- Объединяет результаты в единый summary

## Использование

### Создание LLM провайдера

#### Через API:

```bash
POST /api/v1/llm/llm-providers/
{
  "name": "OpenAI GPT-4 Vision",
  "description": "OpenAI GPT-4 with vision capabilities",
  "provider_type": "openai",
  "api_url": "https://api.openai.com/v1/chat/completions",
  "api_key_env": "OPENAI_API_KEY",
  "model_name": "gpt-4-vision-preview",
  "capabilities": ["text", "image"],
  "config": {
    "temperature": 0.2,
    "max_tokens": 2000
  },
  "is_active": true
}
```

#### Через базу данных:

```sql
INSERT INTO social_manager.llm_providers 
(name, provider_type, api_url, api_key_env, model_name, capabilities, is_active)
VALUES 
('DeepSeek Default', 'deepseek', 'https://api.deepseek.com/v1/chat/completions',
 'DEEPSEEK_API_KEY', 'deepseek-chat', '["text"]'::jsonb, true);
```

### Настройка сценария с разными LLM

```python
from app.models import BotScenario, LLMProvider

# Получить провайдеры
text_provider = await LLMProvider.objects.get(name="DeepSeek Default")
image_provider = await LLMProvider.objects.get(name="OpenAI GPT-4 Vision")

# Создать сценарий
scenario = await BotScenario.objects.create(
    name="Multi-modal analysis",
    content_types=["posts", "videos"],
    analysis_types=["sentiment", "topics"],
    text_llm_provider_id=text_provider.id,
    image_llm_provider_id=image_provider.id,
    video_llm_provider_id=image_provider.id
)
```

### Переменные окружения

Добавьте API ключи в `.env`:

```bash
# Legacy (deprecated)
DEEPSEEK_API_KEY=sk-xxx

# New LLM providers
OPENAI_API_KEY=sk-xxx
ANTHROPIC_API_KEY=sk-ant-xxx
GOOGLE_API_KEY=xxx
```

## Структура результатов анализа

Результаты сохраняются в `AIAnalytics.summary_data`:

```json
{
  "multi_llm_analysis": {
    "text_analysis": {
      "sentiment_analysis": {...},
      "topic_analysis": {...},
      "engagement_analysis": {...}
    },
    "image_analysis": {
      "visual_themes": [...],
      "detected_objects": {...},
      "emotional_tone": "positive"
    },
    "video_analysis": {
      "video_types": [...],
      "content_themes": [...],
      "target_audience": "..."
    }
  },
  "unified_summary": {
    "overall_sentiment": "positive",
    "main_themes": [...],
    "key_insights": [...],
    "content_strategy_recommendations": [...]
  },
  "content_statistics": {...},
  "source_metadata": {...},
  "analysis_metadata": {
    "analysis_version": "3.0-multi-llm",
    "llm_providers_used": 2
  }
}
```

## Добавление нового LLM провайдера

### 1. Создать клиент

```python
from app.services.ai.llm_client import LLMClient

class CustomLLMClient(LLMClient):
    async def analyze(self, prompt: str, media_urls=None, **kwargs):
        # Реализация вызова API
        pass
    
    def _prepare_request(self, prompt, media_urls=None, **kwargs):
        # Подготовка запроса
        pass
    
    def _parse_response(self, response):
        # Парсинг ответа
        pass
```

### 2. Зарегистрировать в фабрике

```python
from app.services.ai.llm_client import LLMClientFactory

LLMClientFactory.register_client("custom", CustomLLMClient)
```

### 3. Добавить провайдер в базу

```python
provider = await LLMProvider.objects.create(
    name="Custom LLM",
    provider_type="custom",
    api_url="https://api.custom.com",
    api_key_env="CUSTOM_API_KEY",
    model_name="custom-model",
    capabilities=["text", "image"],
    is_active=True
)
```

## API Endpoints

### LLM Providers

- `POST /api/v1/llm/llm-providers/` - Создать провайдер
- `GET /api/v1/llm/llm-providers/` - Список провайдеров
- `GET /api/v1/llm/llm-providers/{id}` - Получить провайдер
- `PATCH /api/v1/llm/llm-providers/{id}` - Обновить провайдер
- `DELETE /api/v1/llm/llm-providers/{id}` - Удалить провайдер

Query параметры:
- `is_active` - фильтр по статусу
- `capability` - фильтр по возможности (text/image/video)

## Миграция

База данных была обновлена миграцией `20251014_000000_add_llm_providers`:

- Создана таблица `llm_providers`
- Добавлен enum `llm_provider_type`
- Добавлены поля в `bot_scenarios`: `text_llm_provider_id`, `image_llm_provider_id`, `video_llm_provider_id`
- Создан default провайдер DeepSeek

## Обратная совместимость

Старый `AIAnalyzer` автоматически использует новый `AIAnalyzerV2` если он доступен. Если сценарий не имеет настроенных LLM провайдеров, используется default провайдер для каждого типа контента.

## Промпты для разных типов контента

### Текстовый анализ
- Sentiment analysis
- Topic analysis
- Engagement analysis
- Content quality
- Audience insights

### Анализ изображений
- Visual themes
- Detected objects
- Emotional tone
- Content context
- Brand elements
- Text in images

### Анализ видео
- Video types
- Content themes
- Target audience
- Visual/audio elements
- Engagement factors

## Мониторинг

Все вызовы LLM логируются с полной информацией:
- Используемая модель
- Промпт
- Ответ API
- Время выполнения
- Ошибки

Логи доступны в `app.services.ai.analyzer_v2` и `app.services.ai.llm_client`.
