# Структура данных AI аналитики

**Дата**: 16.10.2025  
**Версия**: v3.0-multi-llm

---

## Поля AI Analytics

В таблице `ai_analytics` есть два ключевых JSON поля:

### 1. `summary_data` - Обработанные данные анализа

**Назначение**: Структурированные результаты анализа, готовые для использования в дашбордах и отчетах.

**Что хранится**:
- ✅ Результаты анализа всех типов медиа (text, image, video)
- ✅ Статистика контента (посты, реакции, комментарии)
- ✅ Метаданные анализа (версия, timestamp, источник)
- ✅ Метаданные сценария (id, name, types)

**Структура** (version 3.0-multi-llm):

```json
{
  "multi_llm_analysis": {
    "text_analysis": {
      "main_topics": ["тема1", "тема2"],
      "overall_mood": "описание настроения",
      "highlights": ["момент1", "момент2"],
      "sentiment_score": 0.5  // опционально
    },
    "image_analysis": {
      "visual_themes": ["тема1"],
      "dominant_colors": ["цвет1"],
      "mood": "настроение"
    },
    "video_analysis": {
      "video_types": ["тип1"],
      "main_themes": ["тема1"],
      "content_style": "стиль"
    }
  },
  "unified_summary": {
    "overall_sentiment": "positive/negative/neutral",
    "main_themes": ["тема1", "тема2"],
    "key_insights": ["инсайт1", "инсайт2"]
  },
  "content_statistics": {
    "total_posts": 67,
    "total_reactions": 462,
    "total_comments": 0,
    "avg_reactions_per_post": 6.89,
    "avg_comments_per_post": 0.0,
    "avg_text_length": 73.82,
    "date_range": {
      "first": "2012-07-12T00:35:46",
      "last": "2022-01-25T19:04:15"
    }
  },
  "source_metadata": {
    "source_type": "user",
    "platform": "ВКонтакте",
    "source_name": "Станислав"
  },
  "analysis_metadata": {
    "analysis_version": "3.0-multi-llm",
    "analysis_timestamp": "2025-10-16T10:32:02.204937+00:00",
    "content_samples_analyzed": 67,
    "llm_providers_used": 1
  },
  "scenario_metadata": {
    "scenario_id": 6,
    "scenario_name": "Экспресс-анализ",
    "analysis_types": ["sentiment", "topics"],
    "content_types": ["posts"]
  }
}
```

**Использование**:
- ✅ Dashboard visualizations (графики, таблицы)
- ✅ Reporting (отчеты, экспорт)
- ✅ API responses (public endpoints)
- ✅ Trend analysis (анализ трендов)
- ✅ Aggregations (агрегация данных)

---

### 2. `response_payload` - Сырые ответы от LLM

**Назначение**: Полные необработанные ответы от LLM API для debugging, auditing и retry.

**Что хранится**:
- ✅ Полный ответ от каждого LLM провайдера
- ✅ Метаданные запроса (model, tokens, timing)
- ✅ Сырой JSON от LLM (может быть невалидным)
- ✅ Информация для debugging

**Структура**:

```json
{
  "text_analysis": {
    "id": "chatcmpl-abc123",
    "model": "Llama-4-Maverick-17B-128E-Instruct",
    "object": "chat.completion",
    "created": 1234567890,
    "choices": [
      {
        "index": 0,
        "finish_reason": "stop",
        "logprobs": null,
        "message": {
          "role": "assistant",
          "content": "{\n  \"main_topics\": [\"главные темы\"],\n  \"overall_mood\": \"настрой\",\n  \"highlights\": [\"моменты\"]\n}"
        }
      }
    ],
    "usage": {
      "completion_tokens": 45,
      "prompt_tokens": 111,
      "total_tokens": 156,
      "completion_tokens_details": null,
      "prompt_tokens_details": null
    },
    "system_fingerprint": "fp",
    "time_to_first_token": 0.xxx,
    "total_latency": 0.158,
    "total_tokens_per_sec": xxx
  },
  "image_analysis": {
    // Аналогично для изображений
  },
  "video_analysis": {
    // Аналогично для видео
  }
}
```

**Использование**:
- ✅ Debugging (отладка промптов)
- ✅ Auditing (аудит качества ответов)
- ✅ Retry logic (повторная обработка)
- ✅ Cost analysis (детальный анализ стоимости)
- ✅ Performance monitoring (мониторинг производительности)
- ✅ Model comparison (сравнение моделей)

---

## Ключевые отличия

| Аспект | `summary_data` | `response_payload` |
|--------|----------------|-------------------|
| **Назначение** | Готовые данные для UI | Сырые данные для debugging |
| **Структура** | Стандартизированная | Зависит от провайдера |
| **Формат** | Всегда валидный JSON | Может быть невалидным |
| **Использование** | Dashboard, API, Reports | Debugging, Audit, Retry |
| **Обработка** | Парсится и валидируется | Сохраняется as-is |
| **Кириллица** | Читаемая (декодированная) | Может быть экранирована |
| **Размер** | Компактный | Полный (с метаданными) |
| **Изменения** | Может обновляться | Immutable (неизменяемый) |

---

## Примеры использования

### 1. Получение топиков для dashboard

```python
from app.models import AIAnalytics

# Получить аналитику
analytics = await AIAnalytics.objects.get(id=79)

# Извлечь топики из summary_data
topics = (
    analytics.summary_data
    .get('multi_llm_analysis', {})
    .get('text_analysis', {})
    .get('main_topics', [])
)

# Результат: ["главные темы обсуждений"]
```

### 2. Debugging ответа LLM

```python
# Получить сырой ответ
response = (
    analytics.response_payload
    .get('text_analysis', {})
    .get('choices', [{}])[0]
    .get('message', {})
    .get('content', '')
)

# Результат: сырой JSON string от LLM
```

### 3. Анализ стоимости

```python
# Из summary_data - общие токены
tokens = analytics.request_tokens + analytics.response_tokens

# Из response_payload - детальная разбивка
usage = (
    analytics.response_payload
    .get('text_analysis', {})
    .get('usage', {})
)

prompt_tokens = usage.get('prompt_tokens', 0)
completion_tokens = usage.get('completion_tokens', 0)
```

---

## Версии структуры данных

### v3.0-multi-llm (текущая)

**Ключевые особенности**:
- ✅ Поддержка множественных LLM провайдеров
- ✅ Разделение по типам медиа (text/image/video)
- ✅ Unified summary для мультимедийного контента
- ✅ Детальная статистика контента
- ✅ Полные метаданные анализа

**Расположение**:
- `summary_data.multi_llm_analysis` - результаты по типам медиа
- `summary_data.unified_summary` - общее резюме
- `summary_data.content_statistics` - статистика
- `summary_data.*_metadata` - метаданные

### v2.0-ai-analysis (устаревшая)

**Структура** (для совместимости):

```json
{
  "ai_analysis": {
    "sentiment_analysis": {
      "overall_sentiment": {
        "label": "positive",
        "score": 0.7
      }
    },
    "key_topics": ["тема1", "тема2"],
    "categories": ["категория1"],
    "keywords": ["слово1", "слово2"]
  },
  "content_stats": {
    "total_posts": 67,
    "total_reactions": 462
  }
}
```

**Поддержка**: Extraction методы в `reporting.py` поддерживают обе структуры с fallback.

---

## FAQ

### Q: Зачем хранить и summary_data и response_payload?

**A**: 
- `summary_data` - для быстрого доступа и консистентной структуры
- `response_payload` - для debugging и возможности повторной обработки

### Q: Можно ли изменять summary_data после создания?

**A**: Да, если нужно переобработать данные. `response_payload` менять нельзя.

### Q: Где искать sentiment_score?

**A**: 
- В `summary_data.multi_llm_analysis.text_analysis.sentiment_score` (если есть)
- Fallback: inference из `overall_mood` в `reporting.py`

### Q: Как добавить новое поле в summary_data?

**A**: 
1. Обновите промпт чтобы LLM возвращал нужное поле
2. Обновите парсинг в `analyzer.py`
3. Обновите extraction в `reporting.py` если используется в dashboard

### Q: Зачем хранить prompt_text?

**A**: Для debugging - чтобы понять какой промпт использовался для анализа.

---

## Best Practices

### ✅ DO:

1. **Используйте summary_data для UI/API**
   ```python
   # Правильно
   topics = analytics.summary_data['multi_llm_analysis']['text_analysis']['main_topics']
   ```

2. **Используйте response_payload для debugging**
   ```python
   # Для анализа ошибок LLM
   raw_content = analytics.response_payload['text_analysis']['choices'][0]['message']['content']
   ```

3. **Проверяйте версию структуры**
   ```python
   version = analytics.summary_data.get('analysis_metadata', {}).get('analysis_version')
   if version == '3.0-multi-llm':
       # Use new structure
   else:
       # Use fallback
   ```

### ❌ DON'T:

1. **Не парсите response_payload для UI**
   ```python
   # Неправильно - structure зависит от провайдера
   content = json.loads(analytics.response_payload['text_analysis']['choices'][0]['message']['content'])
   ```

2. **Не модифицируйте response_payload**
   ```python
   # Неправильно - это immutable audit log
   analytics.response_payload['text_analysis']['model'] = 'new-model'
   ```

3. **Не полагайтесь на наличие sentiment_score**
   ```python
   # Неправильно - может отсутствовать
   score = analytics.summary_data['multi_llm_analysis']['text_analysis']['sentiment_score']
   
   # Правильно - используйте extraction с fallback
   from app.services.ai.reporting import ReportAggregator
   aggregator = ReportAggregator()
   sentiment = aggregator._extract_sentiment(analytics.summary_data)
   ```

---

**Автор**: Factory Droid  
**Дата создания**: 16.10.2025  
**Последнее обновление**: 16.10.2025
