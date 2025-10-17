# AI Analytics - Руководство по админ-панели

## ❓ Часто задаваемые вопросы

### 1. Зачем хранить `prompt_text` в таблице?

**Ответ**: `prompt_text` нужен для:
- 🔍 **Отладки** - понимания почему LLM вернул такой результат
- 📊 **Аудита** - проверки что именно мы спрашивали у модели
- 🔄 **Воспроизведения** - возможности повторить анализ с тем же промптом
- 📈 **Оптимизации** - анализа эффективности разных промптов

**Но есть минусы**:
- 📦 Занимает много места в БД (промпты могут быть 500-2000+ символов)
- 🚫 Не нужен в обычной работе админа

**Решение**:
- ✅ **Исключен из формы редактирования** - не показываем в UI
- ✅ **Хранится в БД** - доступен для технических задач
- ✅ **Можно просмотреть через SQL** если нужно для отладки

```sql
-- Если нужно посмотреть промпт для конкретного анализа:
SELECT id, llm_model, prompt_text 
FROM social_manager.ai_analytics 
WHERE id = 77;
```

**Альтернатива** (если места в БД критично):
- Можно сделать поле `nullable` и не заполнять его
- Хранить только в логах (файлы `logs/app.log`)
- Удалять старые промпты (> 30 дней) через scheduled task

---

### 2. Почему не видно `summary_data` в форме редактирования?

**Ответ**: Это **правильно и специально**! 

**Причины исключения из формы**:
1. 🤖 **Автоматически генерируется** - создается AI Analyzer'ом при анализе
2. 📊 **Сложная структура** - это JSON с вложенными объектами
3. 🚫 **Не должно редактироваться** - ручное изменение нарушит целостность данных
4. 📝 **Большой объем** - может быть 5-10KB+ текста

**Где его можно посмотреть**:
- ✅ **Details страница** (`/admin/ai-analytics/details/77`) - красиво отформатированный JSON с подсветкой
- ✅ **Кнопка "Просмотр анализа"** - специальное действие для просмотра
- ✅ **JSON viewer** - встроенный просмотрщик с копированием в буфер

---

### 3. Какие поля read-only в форме редактирования?

**Поля только для чтения** (настроено в `form_widget_args`):
```python
✅ analysis_date      # Дата анализа
✅ llm_model          # Модель ИИ
✅ provider_type      # Провайдер (sambanova, deepseek, openai)
✅ request_tokens     # Токенов в запросе
✅ response_tokens    # Токенов в ответе
✅ estimated_cost     # Стоимость в центах
✅ media_types        # Типы медиа (text, image, video)
```

**Поля полностью исключены** (настроено в `form_excluded_columns`):
```python
🚫 summary_data       # Данные анализа (JSON)
🚫 response_payload   # Ответ LLM (JSON)
🚫 prompt_text        # Промпт (слишком большой)
🚫 created_at         # Автоматическая timestamp
🚫 updated_at         # Автоматическая timestamp
```

**Поля которые МОЖНО редактировать**:
```python
✏️  source_id         # Можно поменять источник
✏️  period_type       # Период анализа
✏️  topic_chain_id    # ID цепочки тем (опционально)
✏️  parent_analysis_id # ID родительского анализа (опционально)
```

---

## 📋 Структура данных `summary_data`

### Новая структура (v3.0 - multi-LLM):

```json
{
  "multi_llm_analysis": {
    "text_analysis": {
      "main_topics": ["тема 1", "тема 2"],
      "overall_mood": "позитивный",
      "highlights": ["важный момент 1"],
      "sentiment_score": 0.75
    },
    "image_analysis": {},
    "video_analysis": {}
  },
  "unified_summary": {
    // Объединенный анализ всех типов контента
  },
  "content_statistics": {
    "total_posts": 67,
    "total_reactions": 462,
    "total_comments": 0,
    "avg_reactions_per_post": 6.9
  },
  "source_metadata": {
    "source_type": "group",
    "platform": "ВКонтакте",
    "source_name": "Станислав"
  },
  "analysis_metadata": {
    "analysis_version": "3.0-multi-llm",
    "analysis_timestamp": "2025-10-16T10:24:56",
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

---

## 🎨 Новый UI админ-панели

### Список (List View):
```
ID | Источник | Модель              | Провайдер | Стоимость | Дата       | Создано
77 | Станислав| Llama-4-Maverick... | sambanova | $0.1500   | 16.10.2025 | 16.10 10:24
```

### Детали (Details View):
1. **LLM Metrics** (6 карточек):
   - Модель ИИ
   - Провайдер (с бейджем)
   - Токенов (запрос)
   - Токенов (ответ)
   - Стоимость ($)
   - Типы медиа

2. **Статистика контента** (если есть):
   - Постов
   - Реакций
   - Комментариев
   - Средняя вовлеченность

3. **Анализ текста** (если есть):
   - Главные темы
   - Общее настроение
   - Выделяющиеся моменты
   - Оценка тональности (progress bar)

4. **JSON Viewers** (2 секции):
   - Данные анализа (`summary_data`)
   - Ответ LLM (`response_payload`)
   - С подсветкой синтаксиса
   - С кнопкой копирования

5. **Технические детали**:
   - ID, период, цепочка тем
   - Родительский анализ
   - Timestamps

---

## 🔧 Примеры использования

### Просмотр анализа:
1. Перейти в `/admin/ai-analytics`
2. Найти нужный анализ (по источнику, дате, модели)
3. Нажать на ID или "Просмотр анализа"
4. Изучить метрики, текст анализа, JSON данные

### Копирование JSON:
1. Открыть детали анализа
2. Найти секцию "Данные анализа (JSON)"
3. Нажать кнопку "Копировать"
4. Вставить в текстовый редактор / IDE

### Экспорт для анализа:
```bash
# SQL экспорт в JSON файл
psql -U user -d db -c "COPY (
  SELECT row_to_json(t) FROM (
    SELECT id, source_id, llm_model, provider_type, 
           summary_data, created_at
    FROM social_manager.ai_analytics
    WHERE analysis_date >= CURRENT_DATE - INTERVAL '7 days'
  ) t
) TO '/tmp/analytics_export.json';"
```

---

## 📊 Метрики и мониторинг

### Отслеживание стоимости:
```sql
-- Стоимость за последние 7 дней по провайдерам
SELECT 
  provider_type,
  COUNT(*) as requests,
  SUM(request_tokens + response_tokens) as total_tokens,
  SUM(estimated_cost) / 100.0 as total_cost_usd
FROM social_manager.ai_analytics
WHERE analysis_date >= CURRENT_DATE - INTERVAL '7 days'
  AND provider_type IS NOT NULL
GROUP BY provider_type
ORDER BY total_cost_usd DESC;
```

### Популярные модели:
```sql
-- Топ-5 используемых моделей
SELECT 
  llm_model,
  provider_type,
  COUNT(*) as usage_count,
  AVG(request_tokens + response_tokens) as avg_tokens
FROM social_manager.ai_analytics
WHERE created_at >= CURRENT_DATE - INTERVAL '30 days'
GROUP BY llm_model, provider_type
ORDER BY usage_count DESC
LIMIT 5;
```

---

## ✅ Рекомендации

### Для production:
1. ✅ **Регулярно проверять** стоимость анализов в Dashboard
2. ✅ **Архивировать** старые анализы (> 90 дней) в отдельную таблицу
3. ✅ **Мониторить** качество анализов через `summary_data`
4. ✅ **Настроить алерты** при превышении бюджета на LLM

### Для оптимизации:
1. 💡 Использовать более дешевые модели для простых задач
2. 💡 Кэшировать результаты повторяющихся анализов
3. 💡 Удалять `prompt_text` у старых записей (экономия места)
4. 💡 Использовать batch анализ вместо множества мелких запросов

---

## 🐛 Troubleshooting

### summary_data пустой:
```python
# Проверить логи анализатора
tail -f logs/app.log | grep "Error in text analysis"

# Проверить что LLM провайдер активен
SELECT * FROM social_manager.llm_providers WHERE is_active = true;
```

### Стоимость не записывается:
```python
# Проверить что токены заполняются
SELECT id, request_tokens, response_tokens, estimated_cost 
FROM social_manager.ai_analytics 
ORDER BY id DESC LIMIT 10;

# Если токены есть, а cost = NULL - проверить код в analyzer.py
```

### Промпт слишком большой:
```sql
-- Найти самые большие промпты
SELECT id, source_id, LENGTH(prompt_text) as prompt_length
FROM social_manager.ai_analytics
WHERE prompt_text IS NOT NULL
ORDER BY prompt_length DESC
LIMIT 10;

-- Удалить промпты старше 30 дней
UPDATE social_manager.ai_analytics
SET prompt_text = NULL
WHERE created_at < CURRENT_DATE - INTERVAL '30 days'
  AND prompt_text IS NOT NULL;
```

---

## 📚 Связанные документы

- [ANALYTICS_AGGREGATION_SYSTEM.md](./ANALYTICS_AGGREGATION_SYSTEM.md) - Архитектура агрегации
- [DASHBOARD_IMPLEMENTATION.md](./DASHBOARD_IMPLEMENTATION.md) - Dashboard UI
- [UNIFIED_DASHBOARD_SYSTEM.md](./UNIFIED_DASHBOARD_SYSTEM.md) - Общий дашборд

---

**Дата создания**: 16.10.2025  
**Версия**: 1.0  
**Автор**: Factory Droid
