# План рефакторинга LLMProvider → LLMModel

## Текущая проблема

**LLMProvider** содержит и информацию о провайдере, и о модели:
- ❌ `model_name` - конкретная модель
- ❌ `capabilities` - что умеет модель
- ❌ Один провайдер = одна модель

**Хотим:**
✅ Один провайдер (OpenAI) → множество моделей (gpt-4, gpt-3.5, dall-e-3)

---

## Новая архитектура

### LLMProvider (Провайдер)
```python
id: int
name: str                    # "OpenAI", "DeepSeek", "SambaNova"
description: str
provider_type: enum          # OPENAI, DEEPSEEK, SAMBANOVA
api_url: str                 # "https://api.openai.com/v1"
api_key_env: str            # "OPENAI_API_KEY"
config: dict                 # Общие настройки провайдера
is_active: bool

# Relationships
models: List[LLMModel]       # Модели этого провайдера
```

### LLMModel (Модель)
```python
id: int
provider_id: int (FK)        # → LLMProvider
name: str                    # "gpt-4-turbo", "deepseek-chat"
description: str
capabilities: list[str]      # ["text"], ["image"], ["text", "image"]
input_cost: float           # $ per 1M tokens
output_cost: float          # $ per 1M tokens
context_window: int         # Max tokens
config: dict                # Настройки модели (temperature, max_tokens)
is_active: bool
is_default: bool            # Default model for provider

# Relationships
provider: LLMProvider
text_scenarios: List[BotScenario]
image_scenarios: List[BotScenario]
video_scenarios: List[BotScenario]
```

### BotScenario (обновлённый)
```python
# БЫЛО (legacy):
text_llm_provider_id: int → llm_providers
image_llm_provider_id: int → llm_providers
video_llm_provider_id: int → llm_providers

# СТАЛО:
text_llm_model_id: int → llm_models
image_llm_model_id: int → llm_models
video_llm_model_id: int → llm_models
```

---

## Миграция данных

### 1. Создать новую таблицу llm_models
- Скопировать все LLMProvider → LLMModel
- provider_id = соответствующий LLMProvider.id
- Сохранить capabilities в LLMModel
- Убрать capabilities из LLMProvider

### 2. Обновить bot_scenarios
- Добавить колонки: text_llm_model_id, image_llm_model_id, video_llm_model_id
- Скопировать данные: text_llm_provider_id → text_llm_model_id (через маппинг)
- Удалить старые колонки: text_llm_provider_id, image_llm_provider_id, video_llm_provider_id

### 3. Обновить llm_providers
- Удалить колонки: model_name, capabilities

---

## Обновление кода

### analyzer.py
```python
# БЫЛО:
provider = await LLMProvider.objects.get(id=provider_id)
client = LLMClientFactory.create(provider)

# СТАЛО:
model = await LLMModel.objects.get(id=model_id)
provider = await LLMProvider.objects.get(id=model.provider_id)
client = LLMClientFactory.create(provider, model)
```

### llm_client.py
```python
# БЫЛО:
def __init__(self, provider: LLMProvider):
    self.model_name = provider.model_name

# СТАЛО:
def __init__(self, provider: LLMProvider, model: LLMModel):
    self.model_name = model.name
    self.provider = provider
    self.model = model
```

### llm_provider_resolver.py
```python
# Обновить resolve логику для работы с LLMModel
# Вместо capabilities провайдера использовать capabilities модели
```

---

## Преимущества новой архитектуры

1. **Множество моделей для провайдера:**
   ```
   OpenAI
   ├─ gpt-4-turbo (text, image)
   ├─ gpt-3.5-turbo (text)
   └─ dall-e-3 (image)
   ```

2. **Разные стоимости:**
   - gpt-4: $10/$30 per 1M
   - gpt-3.5: $0.5/$1.5 per 1M

3. **Гибкая конфигурация:**
   - Модель A: temperature=0.7
   - Модель B: temperature=0.2

4. **Легкое добавление моделей:**
   - Новая модель = новая запись в llm_models
   - Не нужно создавать новый провайдер

---

## Checklist

- [ ] Обновить LLMProvider model (убрать model_name, capabilities)
- [ ] Обновить LLMModel model (исправить relationships)
- [ ] Создать миграцию 0037
- [ ] Обновить BotScenario (заменить FK)
- [ ] Обновить analyzer.py
- [ ] Обновить llm_client.py
- [ ] Обновить llm_provider_resolver.py
- [ ] Обновить admin/views.py
- [ ] Тестирование
- [ ] Документация

---

**Дата:** 19 октября 2025  
**Статус:** Планирование завершено
