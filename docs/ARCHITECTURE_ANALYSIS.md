# Architecture Analysis: Where LLMProvider Should Be Connected

## ❓ Вопрос
> Мы LLM модель подключили и в сценарии бота и в аналитику? Какой смысл в этом?

## ✅ Ответ: НЕТ! LLMProvider подключен ТОЛЬКО в BotScenario

### Текущая реализация (ПРАВИЛЬНАЯ):

```
┌─────────────────────────────────────────────────────────────────┐
│                         DATA FLOW                                │
└─────────────────────────────────────────────────────────────────┘

1. Source (VK группа, Telegram канал)
   ├─ id: 1
   ├─ name: "IT News VK"
   └─ bot_scenario_id: 5 ───────┐
                                 │
                                 ▼
2. BotScenario (ЧТО и КАК анализировать)
   ├─ id: 5                          ← ЗДЕСЬ определяется ВСЁ!
   ├─ name: "Tech Content Analysis"
   ├─ analysis_types: ["sentiment", "topics"]
   ├─ content_types: ["posts", "videos"]
   ├─ ai_prompt: "Analyze tech content..."
   ├─ text_llm_provider_id: 1 ────┐  ← LLM для текста
   ├─ image_llm_provider_id: 2 ───┤  ← LLM для фото
   └─ video_llm_provider_id: 2 ───┤  ← LLM для видео
                                   │
                                   ▼
                          LLMProvider (КАКОЙ AI использовать)
                          ├─ id: 1 "DeepSeek" (text)
                          └─ id: 2 "GPT-4 Vision" (image, video)

3. ContentCollector.collect_from_source(source)
   └─ Собирает контент: posts, comments, videos, images

4. AIAnalyzer.analyze_content(content, source)
   ├─ Загружает bot_scenario из source.bot_scenario_id
   ├─ Получает LLM providers из bot_scenario
   ├─ Классифицирует контент по типам
   ├─ Анализирует каждый тип своим LLM
   └─ Создает AIAnalytics с результатами

5. AIAnalytics (РЕЗУЛЬТАТ анализа - архивная запись)
   ├─ id: 100
   ├─ source_id: 1
   ├─ summary_data: {...}              ← Результаты анализа
   ├─ llm_model: "deepseek-chat"       ← Для трассировки (строка)
   └─ NO LLMProvider FK!               ← НЕТ связи! Это ПРАВИЛЬНО!
```

## 🎯 Логика архитектуры

### BotScenario - "Инструкция КАК анализировать"

**Роль:** Определяет **стратегию анализа** для источника

**Содержит:**
- ✅ `analysis_types` - какие типы анализа делать
- ✅ `content_types` - какой контент собирать
- ✅ `ai_prompt` - кастомный промпт
- ✅ `text_llm_provider_id` - **КАКОЙ LLM использовать для текста**
- ✅ `image_llm_provider_id` - **КАКОЙ LLM использовать для фото**
- ✅ `video_llm_provider_id` - **КАКОЙ LLM использовать для видео**

**Почему здесь?**
- 🔄 **Переиспользуемый** - один сценарий может использоваться для многих источников
- 🎨 **Гибкий** - можно менять LLM провайдеры для разных задач
- 📝 **Конфигурируемый** - все настройки анализа в одном месте

**Примеры сценариев:**
```python
# Сценарий 1: Быстрый анализ (только текст)
{
  "name": "Quick Sentiment Check",
  "analysis_types": ["sentiment"],
  "text_llm_provider": "DeepSeek Default",  # Быстрый и дешевый
  "image_llm_provider": null,                # Не анализируем
  "video_llm_provider": null                 # Не анализируем
}

# Сценарий 2: Комплексный мультимедиа анализ
{
  "name": "Full Media Analysis",
  "analysis_types": ["sentiment", "topics", "engagement"],
  "text_llm_provider": "DeepSeek Default",  # Для текста
  "image_llm_provider": "GPT-4 Vision",     # Для фото/скриншотов
  "video_llm_provider": "GPT-4 Vision"      # Для видео
}

# Сценарий 3: Только визуальный контент
{
  "name": "Visual Content Only",
  "analysis_types": ["visual_themes"],
  "text_llm_provider": null,                # Не анализируем текст
  "image_llm_provider": "GPT-4 Vision",     # Основной фокус
  "video_llm_provider": "GPT-4 Vision"
}
```

### AIAnalytics - "Архивная запись результата"

**Роль:** Хранит **результаты** уже выполненного анализа

**Содержит:**
- ✅ `summary_data` - результаты анализа (JSON)
- ✅ `llm_model` - какая модель использовалась (строка для трассировки)
- ✅ `prompt_text` - какой промпт был (для отладки)
- ✅ `response_payload` - полный ответ LLM (для аудита)
- ❌ **НЕТ** `llm_provider_id` FK

**Почему НЕТ связи с LLMProvider?**

1. 📚 **Историческая запись**
   - AIAnalytics - это snapshot результата на момент анализа
   - Даже если LLM провайдер будет удален/изменен, результат остается валидным
   - Поле `llm_model` (строка) достаточно для трассировки

2. 🔍 **Независимость от конфигурации**
   - Результаты анализа не зависят от текущей конфигурации LLM
   - Если мы поменяем провайдер в сценарии, старые результаты остаются валидными

3. 🎯 **Правильная нормализация**
   - AIAnalytics связан с Source (откуда данные)
   - Source связан с BotScenario (как анализировать)
   - BotScenario связан с LLMProvider (какой AI использовать)
   - AIAnalytics → Source → BotScenario → LLMProvider (через цепочку)

## 🔄 Пример полного Flow

### Настройка:

```python
# 1. Создаем LLM провайдеры
deepseek = LLMProvider(
    name="DeepSeek",
    provider_type="deepseek",
    capabilities=["text"]
)

gpt4_vision = LLMProvider(
    name="GPT-4 Vision",
    provider_type="openai",
    capabilities=["text", "image", "video"]
)

# 2. Создаем сценарий
scenario = BotScenario(
    name="Tech News Analysis",
    analysis_types=["sentiment", "topics"],
    content_types=["posts", "videos"],
    text_llm_provider_id=deepseek.id,      # Текст → DeepSeek
    image_llm_provider_id=gpt4_vision.id,  # Фото → GPT-4
    video_llm_provider_id=gpt4_vision.id   # Видео → GPT-4
)

# 3. Привязываем к источнику
source = Source(
    name="TechCrunch VK",
    platform_id=1,
    bot_scenario_id=scenario.id  # ← Источник использует этот сценарий
)
```

### Выполнение анализа:

```python
# 1. Собираем контент
collector = ContentCollector()
await collector.collect_from_source(source)

# 2. Внутри collector вызывается:
analyzer = AIAnalyzer()
await analyzer.analyze_content(content, source)

# 3. AIAnalyzer делает:
# - Загружает scenario из source.bot_scenario_id
bot_scenario = await BotScenario.objects.get(id=source.bot_scenario_id)

# - Классифицирует контент
classified = {
    'text': [post1, post2, ...],
    'images': [img1, img2, ...],
    'videos': [vid1, ...]
}

# - Для каждого типа берет СВОЙ LLM из scenario
text_provider = bot_scenario.text_llm_provider    # DeepSeek
image_provider = bot_scenario.image_llm_provider  # GPT-4 Vision
video_provider = bot_scenario.video_llm_provider  # GPT-4 Vision

# - Анализирует каждый тип
text_analysis = await analyze_with_llm(classified['text'], text_provider)
image_analysis = await analyze_with_llm(classified['images'], image_provider)
video_analysis = await analyze_with_llm(classified['videos'], video_provider)

# - Объединяет результаты
unified_summary = create_summary(text_analysis, image_analysis, video_analysis)

# 4. Сохраняет результат
analytics = AIAnalytics(
    source_id=source.id,
    summary_data={
        'text_analysis': text_analysis,
        'image_analysis': image_analysis,
        'video_analysis': video_analysis,
        'unified_summary': unified_summary
    },
    llm_model="deepseek-chat, gpt-4-vision",  # Для трассировки (строка)
    # НЕТ llm_provider_id! Не нужен!
)
```

## 💡 Преимущества текущей архитектуры

### ✅ Гибкость
```python
# Можем легко изменить LLM для всех источников использующих сценарий
scenario.text_llm_provider_id = new_provider.id
# Все источники с этим сценарием автоматически начнут использовать новый LLM
```

### ✅ Переиспользование
```python
# Один сценарий для многих источников
sources = [
    Source(name="TechCrunch VK", bot_scenario_id=scenario.id),
    Source(name="Habr VK", bot_scenario_id=scenario.id),
    Source(name="IT News TG", bot_scenario_id=scenario.id)
]
# Все используют одну и ту же конфигурацию LLM
```

### ✅ Независимость результатов
```python
# Даже если мы удалим LLM провайдер
await LLMProvider.objects.delete(old_provider.id)

# Старые результаты анализа остаются валидными
old_analytics = await AIAnalytics.objects.filter(
    llm_model="old-model-name"
)
# Все еще доступны для просмотра
```

### ✅ Простота запросов
```python
# Получить все аналитики источника
analytics = await AIAnalytics.objects.filter(source_id=source.id)

# Получить LLM провайдеры через цепочку
source = await Source.objects.select_related('bot_scenario').get(id=1)
scenario = source.bot_scenario
text_llm = scenario.text_llm_provider  # Доступ через сценарий
```

## 🚫 Что было бы НЕПРАВИЛЬНО

### ❌ Если бы LLMProvider был в AIAnalytics:

```python
# ПЛОХАЯ архитектура (так НЕ СДЕЛАНО):
class AIAnalytics:
    llm_provider_id: int  # ❌ НЕПРАВИЛЬНО!
    
# Проблемы:
# 1. Дублирование данных - каждая запись хранит provider_id
# 2. Нет гибкости - нельзя изменить LLM для источника централизованно
# 3. Результаты зависят от существования провайдера
# 4. Нарушение нормализации БД
```

### ❌ Если бы LLMProvider был в Source:

```python
# ПЛОХАЯ архитектура (так НЕ СДЕЛАНО):
class Source:
    text_llm_provider_id: int  # ❌ НЕПРАВИЛЬНО!
    
# Проблемы:
# 1. Нет переиспользования - каждый источник настраивается отдельно
# 2. Дублирование конфигурации - если 100 источников, 100 раз указываем LLM
# 3. Сложно массово изменить - нужно обновлять каждый источник
```

## ✅ Вывод

**LLMProvider подключен ТОЛЬКО в BotScenario - это ПРАВИЛЬНО!**

**Роли четко разделены:**
- 📋 **BotScenario** - определяет КАК анализировать (включая выбор LLM)
- 🔗 **Source** - источник данных, использует сценарий
- 📊 **AIAnalytics** - архивирует результаты анализа
- 🤖 **LLMProvider** - конфигурация AI моделей

**Архитектура обеспечивает:**
- ✅ Гибкость настройки
- ✅ Переиспользование конфигураций
- ✅ Независимость исторических данных
- ✅ Простоту изменения провайдеров
- ✅ Правильную нормализацию БД

**Никаких изменений не требуется - всё сделано правильно!** 🎯
