# Custom Media Prompts - Implementation Complete ✅

## Обзор

Реализована полноценная система кастомных промптов для разных типов медиа в BotScenario.

**Теперь каждый сценарий может иметь свои уникальные промпты для:**
- 📝 Текстового анализа
- 🖼️ Анализа изображений
- 🎥 Анализа видео
- 🎵 Анализа аудио
- 📊 Создания unified summary

## Что реализовано

### 1. База данных ✅

**Миграция:** `0032_add_media_prompts_to_scenarios.py`

**Новые поля в `bot_scenarios`:**
```sql
-- Rename existing
ai_prompt → text_prompt (TEXT, nullable)

-- New fields
image_prompt            TEXT NULL  -- Custom image analysis prompt
video_prompt            TEXT NULL  -- Custom video analysis prompt
audio_prompt            TEXT NULL  -- Custom audio analysis prompt
unified_summary_prompt  TEXT NULL  -- Custom unified summary prompt
```

Все поля nullable → полная обратная совместимость!

### 2. Модель BotScenario ✅

**Файл:** `app/models/bot_scenario.py`

**Новые поля:**
```python
class BotScenario(Base, TimestampMixin):
    # Media-specific prompts
    text_prompt: Mapped[str | None] = Column(Text, nullable=True)
    image_prompt: Mapped[str | None] = Column(Text, nullable=True)
    video_prompt: Mapped[str | None] = Column(Text, nullable=True)
    audio_prompt: Mapped[str | None] = Column(Text, nullable=True)
    unified_summary_prompt: Mapped[str | None] = Column(Text, nullable=True)
    
    # Backward compatibility
    @property
    def ai_prompt(self) -> str | None:
        return self.text_prompt
    
    @ai_prompt.setter
    def ai_prompt(self, value: str | None):
        self.text_prompt = value
```

### 3. Prompt Variables System ✅

**Файл:** `app/services/ai/prompt_variables.py`

**Возможности:**
- 📋 Реестр доступных переменных для каждого типа медиа
- 🔄 Подстановка переменных в шаблоны
- 🌲 Поддержка вложенных значений (`{stats.total_posts}`)
- 📖 Help text для админки

**Доступные переменные:**

**Для текста:**
```
{text} - Подготовленный текстовый контент
{platform} - Название платформы (VK, Telegram)
{source_type} - Тип источника (user, group, channel)
{total_posts} - Общее количество постов
{total_reactions} - Общее количество реакций
{total_comments} - Общее количество комментариев
{avg_reactions} - Среднее реакций на пост
{avg_comments} - Среднее комментариев на пост
{date_range_first} - Первая дата в выборке
{date_range_last} - Последняя дата в выборке
{stats} - Полный объект статистики
```

**Для изображений:**
```
{count} - Количество изображений
{platform} - Название платформы
```

**Для видео:**
```
{count} - Количество видео
{platform} - Название платформы
```

**Для аудио:**
```
{count} - Количество аудиофайлов
{platform} - Название платформы
```

### 4. PromptBuilder ✅

**Файл:** `app/services/ai/prompts.py`

**Новый API:**

```python
# Единая точка входа для всех промптов
prompt = PromptBuilder.get_prompt(
    media_type=MediaType.TEXT,
    scenario=bot_scenario,  # Опционально
    # Контекстные переменные:
    text="...",
    platform_name="VK",
    stats={...},
    count=5
)

# Для unified summary
prompt = PromptBuilder.get_unified_summary_prompt(
    text_analysis={...},
    image_analysis={...},
    video_analysis={...},
    scenario=bot_scenario  # Опционально
)
```

**Логика работы:**
1. Проверяет есть ли кастомный промпт в сценарии
2. Если да → использует кастомный с подстановкой переменных
3. Если нет → использует дефолтный hardcoded промпт

### 5. AIAnalyzer Integration ✅

**Файл:** `app/services/ai/analyzer.py`

**Обновлены методы:**
- `_analyze_text()` - использует `PromptBuilder.get_prompt(MediaType.TEXT, ...)`
- `_analyze_images()` - использует `PromptBuilder.get_prompt(MediaType.IMAGE, ...)`
- `_analyze_videos()` - использует `PromptBuilder.get_prompt(MediaType.VIDEO, ...)`
- `_create_unified_summary()` - использует `PromptBuilder.get_unified_summary_prompt(...)`

**Удалена старая логика:**
- ❌ Убран ScenarioPromptBuilder (deprecated)
- ❌ Убраны if/else ветвления для ai_prompt
- ✅ Единый путь через `PromptBuilder.get_prompt()`

### 6. Admin Panel ✅

**Файл:** `app/admin/views.py`

**BotScenarioAdmin обновлен:**

**Новые labels:**
```python
"text_prompt": "Промпт для текста",
"image_prompt": "Промпт для изображений",
"video_prompt": "Промпт для видео",
"audio_prompt": "Промпт для аудио",
"unified_summary_prompt": "Промпт для общего резюме",
```

**Form widgets:**
```python
"text_prompt": {"rows": 10},
"image_prompt": {"rows": 10},
"video_prompt": {"rows": 10},
"audio_prompt": {"rows": 10},
"unified_summary_prompt": {"rows": 10},
```

**Help texts:**
```python
'text_prompt': {
    'description': 'Кастомный промпт для анализа текста. Оставьте пустым для дефолтного. Переменные: {text}, {platform}, {source_type}, {total_posts}, {avg_reactions}, {avg_comments}'
},
'image_prompt': {
    'description': 'Кастомный промпт для анализа изображений. Оставьте пустым для дефолтного. Переменные: {count}, {platform}'
},
# И т.д.
```

## Примеры использования

### Пример 1: Анализ товаров в e-commerce

```python
# В админке создаем сценарий:
name = "Анализ товаров"

text_prompt = """
Проанализируй отзывы о товаре из {platform}.
Всего отзывов: {total_posts}

Контент:
{text}

Выдели:
1. Упоминания качества товара
2. Проблемы с доставкой
3. Сравнения с конкурентами
4. Рекомендации покупателей

Формат ответа: JSON
"""

image_prompt = """
Проанализируй {count} фотографий товара с {platform}.

Определи:
1. Качество фотографий (профессиональные/любительские)
2. Показаны ли дефекты или повреждения
3. Соответствие товара описанию
4. Качество упаковки
5. Наличие брендинга

Формат ответа: JSON
"""

video_prompt = """
Проанализируй {count} видео с распаковкой/обзором товара.

Проверь:
1. Показан ли товар в использовании
2. Реакция покупателя (положительная/отрицательная)
3. Выявленные недостатки
4. Сравнения с аналогами

Формат ответа: JSON
"""
```

### Пример 2: Мониторинг бренда

```python
name = "Мониторинг бренда"

text_prompt = """
Анализ упоминаний бренда в {platform}.
Период: {date_range_first} - {date_range_last}
Всего упоминаний: {total_posts}

{text}

Задачи:
1. Тональность упоминаний (позитив/негатив/нейтрал)
2. Контекст упоминания (реклама/отзыв/новость)
3. Сравнения с конкурентами
4. Ключевые инфлюенсеры

JSON ответ
"""

image_prompt = """
{count} изображений с упоминанием бренда ({platform}).

Найди:
1. Логотип бренда (да/нет)
2. Продукты бренда
3. Контекст использования
4. UGC vs официальный контент

JSON
"""
```

### Пример 3: Crisis Management

```python
name = "Кризисный мониторинг"

text_prompt = """
⚠️ КРИТИЧЕСКИЙ АНАЛИЗ НЕГАТИВА

Платформа: {platform}
Постов: {total_posts}
Средняя вовлеченность: {avg_reactions} реакций, {avg_comments} комментариев

Контент:
{text}

СРОЧНО определи:
1. Уровень угрозы репутации (ВЫСОКИЙ/СРЕДНИЙ/НИЗКИЙ)
2. Ключевые претензии (топ-3)
3. Лидеры мнений среди недовольных
4. Скорость распространения (вирусность)
5. НЕМЕДЛЕННЫЕ рекомендации по реагированию

Формат: JSON с полем "urgency_score" (0-100)
"""
```

## Тестирование

### Unit Tests ✅

```bash
cd /Users/admin/Projects/social-media-ai

# Test 1: Model fields
python3 -c "
from app.models import BotScenario
s = BotScenario()
assert hasattr(s, 'text_prompt')
assert hasattr(s, 'image_prompt')
assert hasattr(s, 'video_prompt')
print('✅ Model fields OK')
"

# Test 2: Variable substitution
python3 -c "
from app.services.ai.prompt_variables import PromptSubstitution
result = PromptSubstitution.substitute('{platform}: {count}', {'platform': 'VK', 'count': 5})
assert result == 'VK: 5'
print('✅ Variable substitution OK')
"

# Test 3: Custom prompt integration
python3 -c "
from app.types import MediaType
from app.services.ai.prompts import PromptBuilder

class MockScenario:
    text_prompt = 'Custom: {text}'
    image_prompt = None
    video_prompt = None
    audio_prompt = None
    unified_summary_prompt = None

prompt = PromptBuilder.get_prompt(MediaType.TEXT, MockScenario(), text='test')
assert 'Custom: test' == prompt
print('✅ Custom prompts OK')
"
```

**Все тесты пройдены! ✅**

## Миграция существующих сценариев

### Автоматическая миграция

Старое поле `ai_prompt` **автоматически** переименовано в `text_prompt`:

```sql
-- Миграция делает это автоматически:
ALTER TABLE social_manager.bot_scenarios
  RENAME COLUMN ai_prompt TO text_prompt;
```

### Backward Compatibility

Старый код продолжит работать благодаря property:

```python
# Старый код (все еще работает):
scenario.ai_prompt = "My prompt"

# Новый код (рекомендуется):
scenario.text_prompt = "My prompt"
scenario.image_prompt = "Image analysis prompt"
scenario.video_prompt = "Video analysis prompt"
```

## Структура файлов

```
app/
├── models/
│   └── bot_scenario.py               # ✨ Updated: +5 prompt fields, +property
├── services/
│   └── ai/
│       ├── analyzer.py                # ✨ Updated: uses PromptBuilder.get_prompt()
│       ├── prompts.py                 # ✨ Updated: +get_prompt(), +get_unified_summary_prompt()
│       └── prompt_variables.py        # ✨ NEW: Variable system
├── admin/
│   └── views.py                       # ✨ Updated: BotScenarioAdmin with prompt fields
└── utils/
    └── enum_helpers.py                # (unchanged)

migrations/
└── versions/
    └── 0032_add_media_prompts_to_scenarios.py  # ✨ NEW

docs/
├── CUSTOM_MEDIA_PROMPTS_PROPOSAL.md           # Planning doc
└── CUSTOM_MEDIA_PROMPTS_IMPLEMENTATION.md     # ✨ NEW: This file
```

## Преимущества реализации

### Для пользователей:
✅ **Гибкость** - каждый сценарий = уникальные промпты  
✅ **Простота** - отдельные текстовые поля в админке  
✅ **Мощность** - переменные для динамических данных  
✅ **Fallback** - дефолтные промпты если не указаны кастомные  
✅ **Совместимость** - старые сценарии работают как раньше  

### Для разработчиков:
✅ **Type-safe** - явные поля в модели  
✅ **Testable** - легко тестировать с mock scenarios  
✅ **Extensible** - просто добавить новые типы медиа  
✅ **Clean Code** - единая логика через `PromptBuilder.get_prompt()`  
✅ **DRY** - нет дублирования промптов  

## Roadmap

### Возможные улучшения:

**Phase 2: Template Engine (опционально)**
- Поддержка Jinja2 для сложных шаблонов
- Условные блоки в промптах
- Циклы для обработки списков

**Phase 3: Prompt Library (опционально)**
- Библиотека готовых промптов
- Sharing промптов между сценариями
- Версионирование промптов

**Phase 4: A/B Testing (опционально)**
- Сравнение эффективности разных промптов
- Метрики качества анализа
- Автоматический выбор лучшего промпта

## Заключение

Реализована полноценная система кастомных промптов для всех типов медиа:

- ✅ База данных (+5 полей)
- ✅ Модель BotScenario (+properties, +backward compatibility)
- ✅ Variable substitution system
- ✅ PromptBuilder unified API
- ✅ AIAnalyzer integration
- ✅ Admin panel UI
- ✅ Unit tests
- ✅ Documentation

**Система готова к использованию в production!** 🚀

**Теперь админы могут создавать сценарии с уникальными промптами для каждого типа контента, что значительно повышает гибкость и эффективность AI-анализа.**
