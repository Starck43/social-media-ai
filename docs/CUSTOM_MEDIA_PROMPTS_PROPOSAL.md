# Proposal: Custom Prompts для разных типов медиа

## Текущее состояние

### Что есть:
- ✅ `BotScenario.ai_prompt` - кастомный промпт для **текстового** контента
- ✅ Hardcoded промпты в `PromptBuilder`:
  - `build_text_prompt()` - используется только если нет `ai_prompt` в сценарии
  - `build_image_prompt()` - **всегда** hardcoded
  - `build_video_prompt()` - **всегда** hardcoded
  - `build_unified_summary_prompt()` - **всегда** hardcoded

### Проблема:
**❌ Нельзя кастомизировать промпты для image/video/audio в админке!**

Пример use case:
- Сценарий для анализа товаров → нужен специфичный image prompt про характеристики товара
- Сценарий для мониторинга брендов → нужен video prompt про логотипы и упоминания
- Сценарий для sentiment → достаточно дефолтного image prompt

## Предлагаемое решение

### Вариант 1: Отдельные поля для каждого типа (рекомендуется ⭐)

Добавить в `BotScenario`:

```python
class BotScenario(Base, TimestampMixin):
    # Existing
    ai_prompt: Mapped[str] = Column(Text, nullable=True)  # For text content
    
    # NEW: Custom prompts for media types
    image_prompt: Mapped[str | None] = Column(
        Text, 
        nullable=True,
        comment="Custom prompt for image analysis. If null, uses default."
    )
    video_prompt: Mapped[str | None] = Column(
        Text, 
        nullable=True,
        comment="Custom prompt for video analysis. If null, uses default."
    )
    audio_prompt: Mapped[str | None] = Column(
        Text, 
        nullable=True,
        comment="Custom prompt for audio analysis. If null, uses default."
    )
    unified_summary_prompt: Mapped[str | None] = Column(
        Text,
        nullable=True,
        comment="Custom prompt for unified summary. If null, uses default."
    )
```

**Плюсы:**
- ✅ Явные поля → понятно в админке
- ✅ Простая валидация
- ✅ Удобно для миграций (nullable = backward compatible)
- ✅ Type-safe (TypeScript knows exact fields)

**Минусы:**
- ❌ Добавляет 4 колонки в таблицу
- ❌ Если появятся новые медиа типы → нужна новая миграция

### Вариант 2: JSON поле с промптами

```python
class BotScenario(Base, TimestampMixin):
    # Existing
    ai_prompt: Mapped[str] = Column(Text, nullable=True)  # Deprecated
    
    # NEW: Flexible prompts storage
    media_prompts: Mapped[dict[str, str]] = Column(
        JSON,
        nullable=True,
        default=dict,
        comment="Custom prompts per media type: {text, image, video, audio, unified}"
    )
    # Example:
    # {
    #     "text": "Analyze product descriptions...",
    #     "image": "Detect product features in images...",
    #     "video": "Identify brand mentions in videos...",
    #     "unified": "Create comprehensive product report..."
    # }
```

**Плюсы:**
- ✅ Гибкость - легко добавить новые типы
- ✅ Одно поле вместо 5
- ✅ Можно хранить дополнительные метаданные

**Минусы:**
- ❌ Хуже UX в админке (JSON editor vs textarea)
- ❌ Сложнее валидация
- ❌ Нет type-safety на уровне БД

### Вариант 3: Hybrid (лучшее из обоих миров)

```python
class BotScenario(Base, TimestampMixin):
    # Main prompts as explicit fields
    text_prompt: Mapped[str | None] = Column(Text, nullable=True)
    image_prompt: Mapped[str | None] = Column(Text, nullable=True)
    video_prompt: Mapped[str | None] = Column(Text, nullable=True)
    
    # Fallback/advanced configuration
    advanced_prompts: Mapped[dict[str, Any]] = Column(
        JSON,
        nullable=True,
        default=dict,
        comment="Advanced prompt config: {audio, unified, variables, etc}"
    )
```

## Рекомендация: **Вариант 1** ⭐

**Почему:**
1. **Простота использования** - админ видит отдельные поля для каждого типа
2. **Backward compatibility** - все поля nullable
3. **Явность > неявность** - понятно что где хранится
4. **Готовность к будущему** - audio prompt уже есть место

## Implementation Plan

### Step 1: Database Migration

```python
# migrations/versions/0032_add_media_prompts_to_scenarios.py

def upgrade() -> None:
    # Rename existing field for clarity
    op.alter_column(
        'bot_scenarios',
        'ai_prompt',
        new_column_name='text_prompt',
        schema='social_manager'
    )
    
    # Add new media prompt fields
    op.add_column('bot_scenarios',
        sa.Column('image_prompt', sa.Text(), nullable=True,
                  comment='Custom prompt for image analysis'),
        schema='social_manager'
    )
    op.add_column('bot_scenarios',
        sa.Column('video_prompt', sa.Text(), nullable=True,
                  comment='Custom prompt for video analysis'),
        schema='social_manager'
    )
    op.add_column('bot_scenarios',
        sa.Column('audio_prompt', sa.Text(), nullable=True,
                  comment='Custom prompt for audio analysis'),
        schema='social_manager'
    )
    op.add_column('bot_scenarios',
        sa.Column('unified_summary_prompt', sa.Text(), nullable=True,
                  comment='Custom prompt for unified summary'),
        schema='social_manager'
    )

def downgrade() -> None:
    op.alter_column('bot_scenarios', 'text_prompt',
                    new_column_name='ai_prompt',
                    schema='social_manager')
    op.drop_column('bot_scenarios', 'unified_summary_prompt', schema='social_manager')
    op.drop_column('bot_scenarios', 'audio_prompt', schema='social_manager')
    op.drop_column('bot_scenarios', 'video_prompt', schema='social_manager')
    op.drop_column('bot_scenarios', 'image_prompt', schema='social_manager')
```

### Step 2: Update Model

```python
# app/models/bot_scenario.py

class BotScenario(Base, TimestampMixin):
    # ... existing fields ...
    
    # Media-specific prompts
    text_prompt: Mapped[str | None] = Column(
        Text, nullable=True,
        comment="Custom prompt for text analysis"
    )
    image_prompt: Mapped[str | None] = Column(
        Text, nullable=True,
        comment="Custom prompt for image analysis"
    )
    video_prompt: Mapped[str | None] = Column(
        Text, nullable=True,
        comment="Custom prompt for video analysis"
    )
    audio_prompt: Mapped[str | None] = Column(
        Text, nullable=True,
        comment="Custom prompt for audio analysis"
    )
    unified_summary_prompt: Mapped[str | None] = Column(
        Text, nullable=True,
        comment="Custom prompt for creating unified summary from multi-media analysis"
    )
    
    # Backward compatibility property
    @property
    def ai_prompt(self) -> str | None:
        """Legacy property for backward compatibility."""
        return self.text_prompt
    
    @ai_prompt.setter
    def ai_prompt(self, value: str | None):
        """Legacy setter for backward compatibility."""
        self.text_prompt = value
```

### Step 3: Update PromptBuilder

```python
# app/services/ai/prompts.py

class PromptBuilder:
    @staticmethod
    def get_prompt(
        media_type: MediaType,
        scenario: BotScenario | None,
        **context
    ) -> str:
        """
        Get prompt for media type, using custom or default.
        
        Args:
            media_type: Type of media (TEXT, IMAGE, VIDEO, AUDIO)
            scenario: Bot scenario with custom prompts
            **context: Context variables for prompt building
        
        Returns:
            Complete prompt string
        """
        # Try custom prompt first
        custom_prompt = None
        if scenario:
            if media_type == MediaType.TEXT:
                custom_prompt = scenario.text_prompt
            elif media_type == MediaType.IMAGE:
                custom_prompt = scenario.image_prompt
            elif media_type == MediaType.VIDEO:
                custom_prompt = scenario.video_prompt
            elif media_type == MediaType.AUDIO:
                custom_prompt = scenario.audio_prompt
        
        # Use custom if available
        if custom_prompt:
            # Support variable substitution
            return PromptBuilder._substitute_variables(custom_prompt, context)
        
        # Fallback to default prompts
        if media_type == MediaType.TEXT:
            return PromptBuilder.build_text_prompt(**context)
        elif media_type == MediaType.IMAGE:
            return PromptBuilder.build_image_prompt(**context)
        elif media_type == MediaType.VIDEO:
            return PromptBuilder.build_video_prompt(**context)
        else:
            raise ValueError(f"Unknown media type: {media_type}")
    
    @staticmethod
    def _substitute_variables(template: str, variables: dict) -> str:
        """Substitute variables in prompt template."""
        # Simple variable substitution
        # Can use Jinja2 for advanced templates
        result = template
        for key, value in variables.items():
            result = result.replace(f"{{{key}}}", str(value))
        return result
```

### Step 4: Update Analyzer

```python
# app/services/ai/analyzer.py

async def _analyze_text(self, text_items, bot_scenario, content_stats, platform_name):
    # ... existing code ...
    
    # Build prompt using new system
    prompt = PromptBuilder.get_prompt(
        MediaType.TEXT,
        bot_scenario,
        text=text_content,
        stats=content_stats,
        platform=platform_name,
        source_type=source_type
    )

async def _analyze_images(self, image_items, bot_scenario, platform_name):
    # ... existing code ...
    
    # Build prompt
    prompt = PromptBuilder.get_prompt(
        MediaType.IMAGE,
        bot_scenario,
        count=len(media_urls),
        platform=platform_name
    )

async def _analyze_videos(self, video_items, bot_scenario, platform_name):
    # ... existing code ...
    
    # Build prompt
    prompt = PromptBuilder.get_prompt(
        MediaType.VIDEO,
        bot_scenario,
        count=len(media_urls),
        platform=platform_name
    )
```

### Step 5: Update Admin

```python
# app/admin/views.py

class BotScenarioAdmin(BaseAdmin):
    # ... existing configuration ...
    
    # Group prompts in form
    form_columns = [
        # ... other fields ...
        
        # Prompts section
        'text_prompt',
        'image_prompt', 
        'video_prompt',
        'audio_prompt',
        'unified_summary_prompt',
    ]
    
    # Column labels
    column_labels = {
        'text_prompt': 'Промпт для текста',
        'image_prompt': 'Промпт для изображений',
        'video_prompt': 'Промпт для видео',
        'audio_prompt': 'Промпт для аудио',
        'unified_summary_prompt': 'Промпт для общего резюме',
    }
    
    # Form widget args - use TextArea for prompts
    form_widget_args = {
        'text_prompt': {'rows': 10},
        'image_prompt': {'rows': 10},
        'video_prompt': {'rows': 10},
        'audio_prompt': {'rows': 10},
        'unified_summary_prompt': {'rows': 10},
    }
    
    # Form help texts
    form_args = {
        'text_prompt': {
            'description': 'Кастомный промпт для анализа текста. Оставьте пустым для дефолтного. Переменные: {text}, {platform}, {stats}'
        },
        'image_prompt': {
            'description': 'Кастомный промпт для анализа изображений. Оставьте пустым для дефолтного. Переменные: {count}, {platform}'
        },
        'video_prompt': {
            'description': 'Кастомный промпт для анализа видео. Оставьте пустым для дефолтного. Переменные: {count}, {platform}'
        },
        'audio_prompt': {
            'description': 'Кастомный промпт для анализа аудио. Оставьте пустым для дефолтного.'
        },
        'unified_summary_prompt': {
            'description': 'Кастомный промпт для создания общего резюме из мультимедийного анализа.'
        }
    }
```

## Advanced: Template Variables System

### Support for dynamic variables in prompts:

```python
# app/services/ai/prompt_variables.py

class PromptVariables:
    """Available variables for prompt templates."""
    
    TEXT_VARIABLES = {
        'text': 'Prepared text content',
        'platform': 'Platform name (VK, Telegram)',
        'source_type': 'Source type (user, group, channel)',
        'stats': 'Content statistics dict',
        'total_posts': 'Number of posts',
        'date_range': 'Date range of content'
    }
    
    IMAGE_VARIABLES = {
        'count': 'Number of images',
        'platform': 'Platform name'
    }
    
    VIDEO_VARIABLES = {
        'count': 'Number of videos',
        'platform': 'Platform name'
    }
    
    @staticmethod
    def get_help_text(media_type: MediaType) -> str:
        """Get help text for available variables."""
        if media_type == MediaType.TEXT:
            vars_dict = PromptVariables.TEXT_VARIABLES
        elif media_type == MediaType.IMAGE:
            vars_dict = PromptVariables.IMAGE_VARIABLES
        elif media_type == MediaType.VIDEO:
            vars_dict = PromptVariables.VIDEO_VARIABLES
        else:
            return ""
        
        lines = ["Доступные переменные:"]
        for var, desc in vars_dict.items():
            lines.append(f"  {{{var}}} - {desc}")
        return "\n".join(lines)
```

## Benefits

### For Users:
1. **Гибкость** - каждый сценарий может иметь свои промпты для каждого типа медиа
2. **Простота** - отдельные поля в админке, не нужен JSON редактор
3. **Мощность** - переменные в промптах позволяют динамически подставлять данные
4. **Fallback** - если не указан кастомный промпт, используется дефолтный

### For Developers:
1. **Type-safe** - явные поля в модели
2. **Testable** - легко тестировать с разными промптами
3. **Extensible** - просто добавить новые типы медиа
4. **Backward compatible** - старый код продолжит работать

## Example Use Cases

### Use Case 1: Product Analysis
```python
# BotScenario for e-commerce product monitoring
text_prompt = """
Проанализируй отзывы о товаре:
{text}

Выдели:
- Упоминания качества
- Проблемы с доставкой
- Сравнения с конкурентами
"""

image_prompt = """
Проанализируй {count} фото товара:

Определи:
- Качество фотографий (профессиональные/любительские)
- Показаны ли дефекты
- Соответствие описанию
- Упаковка товара
"""
```

### Use Case 2: Brand Monitoring
```python
video_prompt = """
Проанализируй {count} видео с упоминанием бренда:

Выяви:
- Тип упоминания (положительное/отрицательное/нейтральное)
- Показан ли логотип бренда
- Контекст использования продукта
- Наличие сравнений с конкурентами
"""
```

### Use Case 3: Crisis Management
```python
text_prompt = """
КРИТИЧЕСКАЯ СИТУАЦИЯ - анализ негатива.

Контент: {text}

СРОЧНО определи:
1. Уровень угрозы репутации (высокий/средний/низкий)
2. Ключевые претензии
3. Лидеры мнений среди недовольных
4. Рекомендации по реагированию
"""
```

## Conclusion

Добавление отдельных полей для промптов каждого типа медиа:
- ✅ Повышает гибкость системы
- ✅ Улучшает UX админки
- ✅ Сохраняет обратную совместимость
- ✅ Готовит систему к будущим расширениям (audio, 3D, AR контент)

**Рекомендую реализовать!**
