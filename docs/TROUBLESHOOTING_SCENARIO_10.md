# Troubleshooting: Source 19 / Scenario 10 - Пустой анализ

**Проблема**: После запуска `python -m cli.scheduler run --once`:
- Заголовок показывается как slug: "Цепочка source_19_chain"
- Записи пустые (нет описания/summary)
- Показываются только метрики (sentiment, posts, reactions)

---

## 🔍 Диагностика проблемы

### Проверка данных в БД

**Выполнить**:
```bash
python3 -c "
import asyncio
import json
from app.models import AIAnalytics
from app.core.database import get_db

async def check():
    async for db in get_db():
        analytics = await AIAnalytics.objects.filter(source_id=19).order_by(AIAnalytics.analysis_date.desc()).limit(1)
        if analytics:
            a = analytics[0]
            print(json.dumps(a.summary_data, indent=2, ensure_ascii=False, default=str))
        break

asyncio.run(check())
"
```

**Результат**:
```json
{
  "analysis_title": null,           ⬅️ ПРОБЛЕМА!
  "analysis_summary": null,         ⬅️ ПРОБЛЕМА!
  "multi_llm_analysis": {
    "text_analysis": {},            ⬅️ ПУСТОЙ!
    "image_analysis": {},
    "video_analysis": {}
  },
  "unified_summary": {},
  "analysis_metadata": {
    "llm_providers_used": 0         ⬅️ LLM НЕ ИСПОЛЬЗОВАЛСЯ!
  }
}
```

---

## ❌ Корневая причина

### **Analysis Types = []** (пустой массив)

**Проверка сценария**:
```bash
python3 -c "
import asyncio
from app.models import BotScenario
from app.core.database import get_db

async def check():
    async for db in get_db():
        s = await BotScenario.objects.get(id=10)
        print(f'Analysis Types: {s.analysis_types}')
        break

asyncio.run(check())
"
```

**Вывод**:
```
Analysis Types: []  ⬅️ ПУСТОЙ МАССИВ!
```

**Должно быть**:
```
Analysis Types: ['sentiment', 'keywords', 'topics']
```

---

## 🛠️ Решение

### **Шаг 1: Исправить Analysis Types в админке**

1. Открыть: http://localhost:8000/admin/bot-scenario/edit/10

2. Найти поле **Analysis Types**

3. **ПРОБЛЕМА**: Админка сохраняет пустой массив `[]` вместо реальных значений

#### **Временное решение** (SQL):

```sql
UPDATE social_manager.bot_scenarios
SET analysis_types = '["sentiment", "keywords", "topics"]'::jsonb
WHERE id = 10;
```

#### **Долгосрочное решение**: Исправить админку

**Файл**: `app/admin/views.py` → BotScenarioAdmin

**Проблема**: JSON поле `analysis_types` некорректно обрабатывается при сохранении

---

### **Шаг 2: Проверить LLM Provider**

```bash
python3 -c "
import asyncio
from app.models import BotScenario
from app.core.database import get_db

async def check():
    async for db in get_db():
        s = await BotScenario.objects.get(id=10)
        print(f'Text LLM Provider ID: {s.text_llm_provider_id}')
        break

asyncio.run(check())
"
```

**Должно быть**: Число (не None)

**Если None**: Выбрать провайдер в админке

---

### **Шаг 3: Заполнить Text Prompt**

**Проверка**:
```bash
python3 -c "
import asyncio
from app.models import BotScenario
from app.core.database import get_db

async def check():
    async for db in get_db():
        s = await BotScenario.objects.get(id=10)
        print(f'Text Prompt length: {len(s.text_prompt) if s.text_prompt else 0}')
        break

asyncio.run(check())
"
```

**Должно быть**: > 1000 символов (полный промпт из документации)

**Если 0**: Скопировать промпт из `docs/SCENARIO_10_QUICK_COPY_PASTE.md`

---

### **Шаг 4: Перезапустить анализ**

```bash
# Удалить старый анализ
python3 -c "
import asyncio
from app.models import AIAnalytics
from app.core.database import get_db

async def delete():
    async for db in get_db():
        await AIAnalytics.objects.filter(source_id=19).delete()
        print('Deleted')
        break

asyncio.run(delete())
"

# Запустить заново
python -m cli.scheduler run --once
```

---

## ✅ Проверка успешности

### **1. Проверить summary_data**

```bash
python3 -c "
import asyncio
import json
from app.models import AIAnalytics
from app.core.database import get_db

async def check():
    async for db in get_db():
        analytics = await AIAnalytics.objects.filter(source_id=19).order_by(AIAnalytics.analysis_date.desc()).limit(1)
        if analytics:
            a = analytics[0]
            sd = a.summary_data
            print(f'Analysis Title: {sd.get(\"analysis_title\")}')
            print(f'Analysis Summary length: {len(sd.get(\"analysis_summary\", \"\"))}')
            print(f'Text Analysis keys: {list(sd.get(\"multi_llm_analysis\", {}).get(\"text_analysis\", {}).keys())}')
            print(f'LLM Providers Used: {sd.get(\"analysis_metadata\", {}).get(\"llm_providers_used\")}')
        break

asyncio.run(check())
"
```

**Ожидаемый результат**:
```
Analysis Title: Активность пользователя за 18 октября
Analysis Summary length: 150+
Text Analysis keys: ['analysis_title', 'analysis_summary', 'main_topics', 'sentiment_score', ...]
LLM Providers Used: 1
```

### **2. Проверить dashboard**

http://localhost:8000/dashboard/topic-chains

**Ожидаемое отображение**:
```
✨ Активность пользователя за 18 октября

📅 18 окт - 18 окт | 📊 1 анализ

━━━━━━━━━━━━━━━━━━━━━━━━
18 окт, 14:30

💡 Описание
Пользователь опубликовал 2 поста, оставил 5 комментариев...

😊 Смешанный | 📄 100 постов | ❤️ 9489 реакций

Тема 1  Тема 2  Тема 3
━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## 🐛 Корневая проблема в админке

### **Файл**: `app/admin/views.py`

**Проблемный код**:
```python
class BotScenarioAdmin(ModelView):
    # ...
    
    async def update_model(self, form, model):
        # ПРОБЛЕМА: analysis_types приходит как {} вместо []
        analysis_types = form.data.get('analysis_types')
        
        if isinstance(analysis_types, dict):
            # Конвертация {} → []
            analysis_types = []  # ⬅️ ТЕРЯЮТСЯ ДАННЫЕ!
        
        # ...
```

### **Решение**: Исправить обработку JSON поля

**Добавить**:
```python
async def update_model(self, form, model):
    data = dict(form.data)
    
    # FIX: Handle analysis_types correctly
    if 'analysis_types' in data:
        analysis_types = data['analysis_types']
        
        # If checkbox list was used
        if isinstance(analysis_types, list):
            # Already correct format
            pass
        # If JSON field returned dict (weird form behavior)
        elif isinstance(analysis_types, dict):
            # Try to extract values
            if 'values' in analysis_types:
                data['analysis_types'] = analysis_types['values']
            else:
                # Keep as empty list only if truly empty
                logger.warning(f"analysis_types is dict but has no 'values': {analysis_types}")
                data['analysis_types'] = []
    
    return await super().update_model(data, model)
```

---

## 📋 Чек-лист для Scenario #10

### **Обязательные поля для работы анализа**:

- [ ] **Analysis Types**: `["sentiment", "keywords", "topics"]` (НЕ пустой массив!)
- [ ] **Text LLM Provider**: Выбран провайдер (не NULL)
- [ ] **Text Prompt**: Заполнен (> 1000 символов)
- [ ] **Content Types**: `["posts", "comments", "reactions"]`
- [ ] **Scope**: `{"event_based": true, ...}`
- [ ] **Is Active**: TRUE

### **SQL проверка**:

```sql
SELECT 
    id,
    name,
    analysis_types,
    text_llm_provider_id,
    CASE WHEN text_prompt IS NULL THEN 0 ELSE LENGTH(text_prompt) END as prompt_length,
    is_active
FROM social_manager.bot_scenarios
WHERE id = 10;
```

**Ожидаемый результат**:
```
id | name             | analysis_types                      | text_llm_provider_id | prompt_length | is_active
---+------------------+-------------------------------------+----------------------+---------------+-----------
10 | Отслеживание...  | ["sentiment", "keywords", "topics"] | 8                    | 3000+         | true
```

---

## 🔧 Быстрый фикс (SQL)

**Если нет времени чинить админку**, выполнить SQL:

```sql
-- Исправить Analysis Types
UPDATE social_manager.bot_scenarios
SET analysis_types = '["sentiment", "keywords", "topics"]'::jsonb
WHERE id = 10;

-- Проверить LLM Provider
UPDATE social_manager.bot_scenarios
SET text_llm_provider_id = 8  -- Заменить на ID вашего провайдера
WHERE id = 10 AND text_llm_provider_id IS NULL;

-- Проверить результат
SELECT id, name, analysis_types, text_llm_provider_id, is_active
FROM social_manager.bot_scenarios
WHERE id = 10;
```

**Затем**:
```bash
# Удалить старый анализ
DELETE FROM social_manager.ai_analytics WHERE source_id = 19;

# Запустить заново
python -m cli.scheduler run --once
```

---

## 📊 Ожидаемый результат после фикса

### **summary_data** (должен содержать):

```json
{
  "analysis_title": "Активность пользователя за 18 октября",
  "analysis_summary": "Пользователь опубликовал 2 поста, оставил 5 комментариев...",
  "multi_llm_analysis": {
    "text_analysis": {
      "analysis_title": "...",
      "analysis_summary": "...",
      "main_topics": ["Тема 1", "Тема 2", "Тема 3"],
      "sentiment_score": 0.6,
      "sentiment_label": "Смешанный",
      "keywords": ["ключ 1", "ключ 2", ...]
    }
  },
  "analysis_metadata": {
    "llm_providers_used": 1  ⬅️ НЕ 0!
  }
}
```

### **Dashboard** (должен отображать):

- ✅ Заголовок: "Активность пользователя за 18 октября" (не "Цепочка source_19_chain")
- ✅ Описание: Полный текст analysis_summary
- ✅ Темы: Список тем из main_topics
- ✅ Метрики: Sentiment, posts, reactions

---

## 🆘 Если всё ещё не работает

### **Проверить логи**:

```bash
tail -100 logs/app.log | grep -E "ERROR|WARNING|No.*provider|analysis_types|source.*19"
```

### **Искать**:
- `No text LLM provider configured` → Провайдер не выбран
- `analysis_types=[]` → Админка сохраняет пустой массив
- `WARNING.*No data found` → Контент не собран

### **Связаться с разработчиком** если:
- Analysis Types не сохраняется в админке
- LLM провайдер настроен, но анализ не запускается
- Логи показывают другие ошибки

---

**Дата**: Current Session  
**Статус**: 🔍 Диагностирована проблема с adminanalysis_types  
**Решение**: SQL fix + исправление админки
