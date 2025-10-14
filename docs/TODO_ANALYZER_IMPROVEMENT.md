# TODO: Улучшение логики анализа контента в AIAnalyzerV2

## 🎯 Цель

Изменить логику обработки мультимодального контента (текст + изображения + видео) для:
- Сохранения контекста между текстом и медиа
- Экономии токенов (~70%)
- Более точного анализа

## ❌ Текущая проблема

**Файл:** `app/services/ai/analyzer_v2.py`

**Текущая логика (НЕПРАВИЛЬНАЯ):**

```python
async def analyze_content(self, content, source):
    classified = ContentClassifier.classify_content(content)
    
    # 1. Анализ текста ОТДЕЛЬНО
    text_result = await self._analyze_text(classified['text'])
    # DeepSeek: "Positive sentiment"
    
    # 2. Анализ изображений ОТДЕЛЬНО
    image_result = await self._analyze_images(classified['images'])
    # GPT-4V: "Logo on image, happy people"
    
    # 3. Анализ видео ОТДЕЛЬНО
    video_result = await self._analyze_videos(classified['videos'])
    # GPT-4V: "Product demonstration video"
    
    # 4. Объединение результатов в конце
    unified = self._create_unified_summary(text_result, image_result, video_result)
```

**Проблемы:**
- ❌ Контекст теряется (текст и медиа анализируются отдельно)
- ❌ Неэффективно (3 отдельных запроса к LLM)
- ❌ Невозможно понять связь между текстом и визуальным контентом
- ❌ Дорого (оплата за 3 запроса)

**Пример:**
```
Пост: "Встречайте нашу новую коллекцию!" + Фото с логотипом

Текущий анализ:
  - Текст: "Positive sentiment" (без контекста фото)
  - Фото: "Brand logo visible" (без контекста текста)
  
Проблема: Не понимает что фото ОТНОСИТСЯ к новой коллекции
```

## ✅ Правильная логика (TO IMPLEMENT)

**Концепция: Vision → Text → Analyze**

### Шаг 1: Vision модель извлекает визуальную информацию в ТЕКСТ

```python
vision_descriptions = []

# Для каждого изображения/видео
for media in images + videos:
    # Используем Vision модель ТОЛЬКО для описания
    prompt = "Кратко опиши что изображено на картинке/видео. Укажи: объекты, людей, текст, эмоции, бренды."
    
    description = await vision_llm.extract_visual_info(
        media_url=media['url'],
        prompt=prompt
    )
    # Результат: "На изображении: логотип бренда XYZ, счастливые люди в синей одежде, 
    #             текст 'Новая коллекция 2024', яркие цвета"
    
    vision_descriptions.append({
        'media_url': media['url'],
        'media_type': media['type'],  # image or video
        'description': description
    })
```

### Шаг 2: Объединение текста с визуальным контекстом

```python
# Исходный текст поста
original_text = "\n\n".join([item['text'] for item in classified['text']])

# Добавляем визуальный контекст
combined_text = original_text

if vision_descriptions:
    combined_text += "\n\n[ВИЗУАЛЬНЫЙ КОНТЕКСТ]:\n"
    for i, vd in enumerate(vision_descriptions, 1):
        combined_text += f"{i}. {vd['media_type'].upper()}: {vd['description']}\n"

# Пример результата:
# """
# Встречайте нашу новую коллекцию!
# 
# [ВИЗУАЛЬНЫЙ КОНТЕКСТ]:
# 1. IMAGE: На изображении: логотип бренда XYZ, счастливые люди в синей одежде, 
#           текст 'Новая коллекция 2024', яркие цвета
# """
```

### Шаг 3: Анализ комбинированного текста ОДНОЙ моделью

```python
# Теперь используем текстовую модель (DeepSeek) для анализа
# Она получает И текст И описание визуального контента
prompt = """Проанализируй следующий контент из социальной сети.
Учитывай как текстовую часть, так и визуальный контекст (если указан).

КОНТЕНТ:
{combined_text}

Определи:
1. Общую тональность (positive/negative/neutral)
2. Основные темы
3. Упоминания брендов
4. Связь между текстом и визуальным контентом
5. Эмоциональную окраску

Верни результат в JSON формате."""

result = await text_llm.analyze(
    combined_text=combined_text,
    prompt=prompt
)

# Теперь модель ПОНИМАЕТ связь:
# "Positive sentiment - объявление о новой коллекции, подтверждается 
#  визуальным контекстом с логотипом и текстом на изображении"
```

## 🔧 Изменения в коде

### Файл: `app/services/ai/analyzer_v2.py`

```python
class AIAnalyzerV2:
    async def analyze_content(self, content, source):
        """Улучшенная логика с сохранением контекста."""
        
        # 1. Классифицировать контент
        classified = ContentClassifier.classify_content(content)
        
        # 2. Извлечь визуальную информацию в текст (если есть медиа)
        vision_context = await self._extract_visual_context(
            classified['images'],
            classified['videos'],
            bot_scenario
        )
        
        # 3. Объединить текст + визуальный контекст
        combined_content = self._combine_text_and_vision(
            classified['text'],
            vision_context
        )
        
        # 4. Анализировать всё вместе одной моделью
        analysis_result = await self._analyze_combined_content(
            combined_content,
            bot_scenario,
            content_stats,
            platform_name,
            source
        )
        
        # 5. Сохранить результаты
        return await self._save_analysis(
            analysis_result,
            source,
            topic_chain_id,
            parent_analysis_id
        )
    
    async def _extract_visual_context(
        self, 
        images: list, 
        videos: list,
        bot_scenario: BotScenario
    ) -> list[dict]:
        """
        Извлекает визуальную информацию в текстовый формат.
        
        Returns:
            List of dicts: [
                {
                    'media_url': str,
                    'media_type': 'image' | 'video',
                    'description': str
                }
            ]
        """
        if not images and not videos:
            return []
        
        # Получить Vision LLM провайдер
        vision_provider = await self._get_vision_llm_provider(bot_scenario)
        if not vision_provider:
            logger.warning("No vision LLM provider available")
            return []
        
        vision_client = LLMClientFactory.create(vision_provider)
        vision_descriptions = []
        
        # Обработать изображения
        for img in images[:10]:  # Лимит на кол-во изображений
            try:
                prompt = """Кратко опиши что изображено на картинке.
Укажи: основные объекты, людей, текст (если есть), эмоции, бренды, цвета.
Будь лаконичен, 2-3 предложения."""
                
                description = await vision_client.analyze_image(
                    image_url=img['url'],
                    prompt=prompt
                )
                
                vision_descriptions.append({
                    'media_url': img['url'],
                    'media_type': 'image',
                    'description': description
                })
            except Exception as e:
                logger.error(f"Failed to analyze image {img['url']}: {e}")
        
        # Обработать видео (аналогично)
        for vid in videos[:5]:  # Лимит на кол-во видео
            try:
                prompt = """Кратко опиши что происходит в видео.
Укажи: основные действия, людей, текст (если есть), тематику.
Будь лаконичен, 2-3 предложения."""
                
                description = await vision_client.analyze_video(
                    video_url=vid['url'],
                    prompt=prompt
                )
                
                vision_descriptions.append({
                    'media_url': vid['url'],
                    'media_type': 'video',
                    'description': description
                })
            except Exception as e:
                logger.error(f"Failed to analyze video {vid['url']}: {e}")
        
        return vision_descriptions
    
    def _combine_text_and_vision(
        self,
        text_items: list,
        vision_context: list[dict]
    ) -> str:
        """
        Объединяет текстовый контент с визуальными описаниями.
        
        Returns:
            Combined text ready for analysis
        """
        # Собрать весь текст
        text_parts = []
        for item in text_items:
            if item.get('text'):
                text_parts.append(item['text'])
        
        combined = "\n\n".join(text_parts)
        
        # Добавить визуальный контекст
        if vision_context:
            combined += "\n\n[ВИЗУАЛЬНЫЙ КОНТЕКСТ]:\n"
            for i, vc in enumerate(vision_context, 1):
                media_type_label = "ИЗОБРАЖЕНИЕ" if vc['media_type'] == 'image' else "ВИДЕО"
                combined += f"{i}. {media_type_label}: {vc['description']}\n"
        
        return combined
    
    async def _analyze_combined_content(
        self,
        combined_text: str,
        bot_scenario: BotScenario,
        content_stats: dict,
        platform_name: str,
        source: Source
    ) -> dict:
        """
        Анализирует комбинированный текст (текст + описания медиа).
        
        Использует текстовую модель (например DeepSeek), которая теперь
        имеет полный контекст включая визуальную информацию.
        """
        # Получить текстовую LLM
        text_provider = await self._get_text_llm_provider(bot_scenario)
        if not text_provider:
            raise ValueError("No text LLM provider available")
        
        text_client = LLMClientFactory.create(text_provider)
        
        # Построить промпт с учётом того, что контекст уже включает визуальное описание
        prompt = PromptBuilder.build_combined_analysis_prompt(
            combined_text=combined_text,
            content_stats=content_stats,
            platform_name=platform_name,
            analysis_types=bot_scenario.analysis_types if bot_scenario else []
        )
        
        # Анализировать
        result = await text_client.analyze(prompt)
        
        return {
            'analysis': result,
            'llm_model': text_provider.model_name,
            'has_visual_context': '[ВИЗУАЛЬНЫЙ КОНТЕКСТ]' in combined_text,
            'combined_text_length': len(combined_text)
        }
    
    async def _get_vision_llm_provider(self, bot_scenario: BotScenario) -> Optional[LLMProvider]:
        """Получить Vision LLM провайдер (для image или video)."""
        # Попробовать получить image провайдер
        if bot_scenario and bot_scenario.image_llm_provider_id:
            return await LLMProvider.objects.get(id=bot_scenario.image_llm_provider_id)
        
        # Fallback: найти любой провайдер с image или video capability
        providers = await LLMProvider.objects.filter(is_active=True)
        for provider in providers:
            if 'image' in provider.capabilities or 'video' in provider.capabilities:
                return provider
        
        return None
```

### Новый файл: `app/services/ai/prompts.py` (добавить метод)

```python
@staticmethod
def build_combined_analysis_prompt(
    combined_text: str,
    content_stats: dict,
    platform_name: str,
    analysis_types: list[str]
) -> str:
    """
    Строит промпт для анализа комбинированного контента (текст + визуальное описание).
    """
    prompt = f"""Проанализируй следующий контент из социальной сети {platform_name}.

ВАЖНО: Контент включает текстовую часть И описание визуального контента (если указано в разделе [ВИЗУАЛЬНЫЙ КОНТЕКСТ]).
Учитывай СВЯЗЬ между текстом и визуальными элементами при анализе.

КОНТЕНТ ({content_stats['total_items']} элементов):
{combined_text[:4000]}  # Лимит токенов

ЗАДАЧИ АНАЛИЗА:
"""
    
    if 'sentiment' in analysis_types:
        prompt += """
1. АНАЛИЗ ТОНАЛЬНОСТИ:
   - Определи общую тональность (positive/negative/neutral/mixed)
   - Учитывай как текст, так и визуальный контекст
   - Оцени эмоциональную окраску визуальных элементов
"""
    
    if 'topics' in analysis_types:
        prompt += """
2. АНАЛИЗ ТЕМ:
   - Определи основные темы контента
   - Учитывай связь текста с визуальными элементами
   - Выяви ключевые слова и визуальные маркеры
"""
    
    if 'brand_mentions' in analysis_types:
        prompt += """
3. УПОМИНАНИЯ БРЕНДОВ:
   - Найди упоминания брендов в тексте
   - Определи видимость брендов в визуальном контенте (логотипы, продукты)
   - Оцени контекст упоминаний
"""
    
    prompt += """

Верни результат в JSON формате с полями:
{
  "overall_sentiment": "positive/negative/neutral/mixed",
  "sentiment_score": 0-100,
  "main_topics": ["тема1", "тема2"],
  "brand_mentions": [{"brand": "название", "context": "контекст", "visual": true/false}],
  "text_visual_coherence": "описание связи текста и визуального контента",
  "key_insights": ["инсайт1", "инсайт2"]
}
"""
    
    return prompt
```

## 📊 Сравнение подходов

### Текущий подход (3 запроса):

```
Пост: "Новая коллекция!" + Фото логотипа

Запрос 1: DeepSeek (text only)
  Input: "Новая коллекция!"
  Output: "Positive, announcement"
  Cost: ~100 tokens × $0.0001 = $0.00001

Запрос 2: GPT-4V (image only)
  Input: image + "Опиши что на фото"
  Output: "Logo visible, blue colors"
  Cost: ~500 tokens × $0.01 = $0.005

Запрос 3: DeepSeek (combine results)
  Input: "Combine: announcement + logo"
  Output: unified summary
  Cost: ~200 tokens × $0.0001 = $0.00002

ИТОГО: $0.00503 (0.5 цента)
ПРОБЛЕМА: Контекст потерян!
```

### Новый подход (2 запроса):

```
Пост: "Новая коллекция!" + Фото логотипа

Запрос 1: GPT-4V (extract visual to text)
  Input: image + "Кратко опиши что на фото"
  Output: "Логотип бренда, синие цвета, текст '2024'"
  Cost: ~300 tokens × $0.01 = $0.003

Запрос 2: DeepSeek (analyze combined context)
  Input: "Новая коллекция!\n[ВИЗУАЛЬНЫЙ КОНТЕКСТ]: Логотип бренда..."
  Output: "Positive sentiment, brand announcement with visual confirmation"
  Cost: ~250 tokens × $0.0001 = $0.000025

ИТОГО: $0.003025 (0.3 цента)
ЭКОНОМИЯ: 40% + контекст сохранён!
```

## ✅ Преимущества нового подхода

1. **Сохранение контекста** 🎯
   - Текст и медиа анализируются вместе
   - Понятна связь между ними
   - Более точный анализ

2. **Экономия токенов** 💰
   - 2 запроса вместо 3-4
   - ~40-70% экономии
   - Меньше промежуточных шагов

3. **Простая архитектура** 🏗️
   - Линейный flow: Vision → Combine → Analyze
   - Легче отладка
   - Меньше точек отказа

4. **Лучшее качество** ⭐
   - Модель видит полный контекст
   - Учитывает связи
   - Более глубокий анализ

## 📝 Checklist для реализации

- [ ] Добавить метод `_extract_visual_context()` в `AIAnalyzerV2`
- [ ] Добавить метод `_combine_text_and_vision()` в `AIAnalyzerV2`
- [ ] Обновить метод `_analyze_combined_content()` в `AIAnalyzerV2`
- [ ] Добавить метод `_get_vision_llm_provider()` в `AIAnalyzerV2`
- [ ] Добавить `build_combined_analysis_prompt()` в `PromptBuilder`
- [ ] Обновить `LLMClient` для поддержки `analyze_image()` и `analyze_video()`
- [ ] Добавить лимиты на количество обрабатываемых медиа (защита от перерасхода)
- [ ] Тестирование с реальными постами
- [ ] Сравнение результатов старой и новой логики
- [ ] Обновить документацию

## 🔍 Тестирование

```python
# Тестовый сценарий
test_content = [
    {
        'text': 'Встречайте нашу новую коллекцию!',
        'images': [
            {'url': 'https://example.com/logo.jpg'}
        ]
    }
]

# Запустить анализ
result = await AIAnalyzerV2().analyze_content(
    content=test_content,
    source=test_source
)

# Проверить что:
# 1. Vision описание добавлено к тексту
# 2. Анализ учитывает визуальный контекст
# 3. Токены сэкономлены
# 4. Качество улучшилось
```

## 📌 Приоритет

**HIGH** - Критично для качества анализа и экономии токенов

## 🕐 Оценка времени

~4-6 часов работы:
- 2 часа: рефакторинг `AIAnalyzerV2`
- 1 час: обновление промптов
- 1 час: обновление `LLMClient`
- 1-2 часа: тестирование и отладка

## 📚 Связанные файлы

- `app/services/ai/analyzer_v2.py` (основные изменения)
- `app/services/ai/prompts.py` (новые промпты)
- `app/services/ai/llm_client.py` (методы для vision)
- `app/services/ai/content_classifier.py` (без изменений)
