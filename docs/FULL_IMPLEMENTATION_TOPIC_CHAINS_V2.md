# Full Implementation: Topic Chains V2 + Date Range + Auto-expand

**Date**: Current Session  
**Status**: ✅ Implemented

---

## Изменения в логике Topic Chains

### ❌ СТАРАЯ логика (убрана)

**Проблема**: Сложная логика matching тем
- Цепочка создавалась на основе 50% overlap тем
- `topic_chain_id` = hash(source + topics + date)
- Каждая новая тема → новая цепочка

**Пример**:
```
Источник 16:
├─ chain_16_abc123 (темы: дизайн, UX)
├─ chain_16_def456 (темы: вакцинация, covid)
└─ chain_16_xyz789 (темы: ремонт, дороги)
```

### ✅ НОВАЯ логика (реализована)

**Решение**: Простая привязка к source + scenario
- **1 источник + 1 сценарий = 1 цепочка** (timeline)
- `topic_chain_id` = `source_{id}_scenario_{id}` или `source_{id}`
- Все анализы добавляются в ОДНУ цепочку
- Сортировка по датам постов

**Пример**:
```
Источник 16, Сценарий 5:
source_16_scenario_5 (ОДНА цепочка - timeline)
├─ 15 окт: [дизайн, UX]
├─ 16 окт: [вакцинация, covid]  
├─ 17 окт: [ремонт, дороги]
└─ 18 окт: [благоустройство, парки]
```

---

## Изменения в коде

### 1. analyzer.py - Упрощённая логика цепочек

**Файл**: `app/services/ai/analyzer.py`

#### Было (сложный matching):
```python
# Extract topics
current_topics = []
text_parsed = analysis_results.get('text_analysis', {}).get('parsed', {})
if 'main_topics' in text_parsed:
    current_topics.extend(text_parsed['main_topics'])

# Find matching chain (50% overlap)
if current_topics:
    matched_chain = await self._find_matching_topic_chain(source, current_topics)
    if matched_chain:
        topic_chain_id = matched_chain
    else:
        # Generate new chain based on topics + date hash
        topic_chain_id = self._generate_topic_chain_id(source, current_topics, datetime.now())
```

#### Стало (простая привязка):
```python
# Auto-generate topic_chain_id if not provided
# NEW LOGIC: One source + one scenario = one chain (timeline by dates)
if not topic_chain_id:
    topic_chain_id = self._generate_topic_chain_id(source, bot_scenario)
    logger.info(f"Using topic chain: {topic_chain_id} for source {source.id}")
```

#### Метод _generate_topic_chain_id:

**Было**:
```python
def _generate_topic_chain_id(self, source, topics, timestamp):
    primary_topic = topics[0] if topics else "general"
    hash_input = f"{source.id}_{primary_topic}_{timestamp.date()}"
    hash_short = hashlib.md5(hash_input.encode()).hexdigest()[:8]
    return f"chain_{source.id}_{hash_short}"
```

**Стало**:
```python
def _generate_topic_chain_id(self, source, bot_scenario=None):
    """
    NEW LOGIC: One source + one scenario = one chain (timeline by dates).
    """
    if bot_scenario and bot_scenario.id:
        return f"source_{source.id}_scenario_{bot_scenario.id}"
    else:
        return f"source_{source.id}"
```

**Примеры**:
- С сценарием: `source_16_scenario_5`
- Без сценария: `source_16`

---

### 2. VK Client - Date Range Filtering

**Файл**: `app/services/social/vk_client.py`

**Добавлена поддержка** `date_from` и `date_to` из `Source.params.collection`:

```python
def _build_params(self, source, method):
    source_params = source.params.get('collection', {})
    
    if method in ('wall.get', 'wall.getComments'):
        params_dict = {...}
        
        # DATE RANGE FILTERING
        # Priority: date_from/date_to > last_checked > no filter
        
        date_from = source_params.get('date_from')
        date_to = source_params.get('date_to')
        
        # Apply date_from (start boundary)
        if date_from:
            date_from_dt = datetime.fromisoformat(date_from.replace('Z', '+00:00'))
            params_dict['start_time'] = int(date_from_dt.timestamp())
            logger.info(f"VK date_from filter: {date_from_dt.isoformat()}")
        # Fallback to checkpoint
        elif source.last_checked:
            params_dict['start_time'] = int(source.last_checked.timestamp())
            logger.info(f"VK checkpoint: {source.last_checked.isoformat()}")
        
        # Apply date_to (end boundary)
        if date_to:
            date_to_dt = datetime.fromisoformat(date_to.replace('Z', '+00:00'))
            params_dict['end_time'] = int(date_to_dt.timestamp())
            logger.info(f"VK date_to filter: {date_to_dt.isoformat()}")
```

**Настройка в Source.params**:
```json
{
  "collection": {
    "date_from": "2025-01-01T00:00:00Z",  // Начало мониторинга
    "date_to": "2025-12-31T23:59:59Z",    // Конец (optional)
    "count": 100
  }
}
```

---

### 3. Dashboard - Auto-expand для одноэлементных цепочек

**Файл**: `app/static/js/dashboard.js`

#### Изменения в buildChainCard:

```javascript
buildChainCard(chain, source) {
    // ... existing code ...
    
    return `
        <div class="chain-item">
            <!-- ... header ... -->
            
            <!-- Show button ONLY if multiple analyses -->
            ${chain.analyses_count > 1 ? `
            <div class="mt-3">
                <button class="btn btn-sm btn-outline-primary collapse-toggle" 
                        data-bs-toggle="collapse" 
                        data-bs-target="#evolution-${chain.chain_id}"
                        data-chain-id="${chain.chain_id}">
                    <i class="fas fa-chevron-right me-1"></i>
                    Показать эволюцию тем
                </button>
            </div>
            ` : ''}
            
            <!-- Auto-expand if single analysis -->
            <div class="collapse chain-evolution ${chain.analyses_count === 1 ? 'show' : ''}" 
                 id="evolution-${chain.chain_id}">
                <div class="analysis-timeline" id="timeline-${chain.chain_id}">
                    ${chain.analyses_count === 1 ? '' : `
                    <div class="text-center py-3">
                        <div class="spinner-border"></div>
                    </div>
                    `}
                </div>
            </div>
        </div>
    `
}
```

**Логика**:
- `analyses_count === 1` → кнопка скрыта, контент показан сразу (`.show`)
- `analyses_count > 1` → кнопка видна, контент свёрнут

**Файл**: `app/templates/topic_chains_dashboard.html`

**Автозагрузка** evolution для развёрнутых цепочек:

```javascript
function renderChains(chains) {
    // ... build HTML ...
    
    listEl.innerHTML = html;
    
    // Event listeners for collapse buttons
    document.querySelectorAll('.collapse-toggle').forEach(btn => {
        btn.addEventListener('click', async function() {
            const chainId = this.getAttribute('data-chain-id');
            const timeline = document.getElementById(`timeline-${chainId}`);
            
            if (!this.classList.contains('loaded')) {
                await TopicChainUtils.loadEvolution(chainId);
                this.classList.add('loaded');
            }
        });
    });
    
    // Auto-load evolution for single-analysis chains (already shown)
    document.querySelectorAll('.chain-evolution.show').forEach(async (collapseEl) => {
        const chainId = collapseEl.id.replace('evolution-', '');
        const timeline = document.getElementById(`timeline-${chainId}`);
        
        if (timeline && !timeline.dataset.loaded) {
            await TopicChainUtils.loadEvolution(chainId);
            timeline.dataset.loaded = 'true';
        }
    });
}
```

---

## Визуальные изменения

### Одноэлементная цепочка (автораскрыта):

```
┌─────────────────────────────────────────────────────┐
│ ✨ Обсуждение дизайна и реакции в комментариях       │
│                                                     │
│ 📅 18 окт - 18 окт | 📊 1 анализ                     │
│ 🔄 18 окт, 14:30                                    │
│                                                     │
│ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│                                                     │
│ 18 окт, 14:30                                       │
│                                                     │
│ 💡 Описание                                         │
│ Пользователи обсуждают дизайн различных продуктов  │
│ и сервисов, делятся мнениями и опытом...           │
│                                                     │
│ 😊 Смешанный | 📄 67 постов | ❤️ 462 реакций       │
│                                                     │
│ Дизайн продуктов  UX/UI  Критика                   │
│ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
└─────────────────────────────────────────────────────┘
```

### Многоэлементная цепочка (свёрнута):

```
┌─────────────────────────────────────────────────────┐
│ ✨ Мониторинг активности пользователя               │
│                                                     │
│ 📅 15 окт - 18 окт | 📊 4 анализа                    │
│ 🔄 18 окт, 16:00                                    │
│                                                     │
│ Дизайн  UX  Вакцинация  Ремонт  Благоустройство    │
│                                                     │
│ [Показать эволюцию тем ▶]                           │
└─────────────────────────────────────────────────────┘

[При клике разворачивается timeline со всеми 4 анализами]
```

---

## Примеры использования

### 1. Настройка date range в админке

**В админ панели Source**:

Установить `params`:
```json
{
  "collection": {
    "date_from": "2025-01-01T00:00:00Z",
    "count": 100
  }
}
```

**Результат**: VK API соберёт только посты после 1 января 2025

### 2. CLI сбор с датами

```bash
# В будущем (если нужно):
python cli/collect.py --source-id 16 \
    --date-from "2025-01-01T00:00:00Z" \
    --date-to "2025-01-31T23:59:59Z"
```

### 3. Сценарий "Мониторинг активности пользователя"

**Source**: USER (Иванов Иван)  
**Scenario**: "Активность пользователя"  
**Topic Chain ID**: `source_16_scenario_5`

**Все анализы** этого пользователя по этому сценарию → **одна цепочка**:

```
source_16_scenario_5
├─ 15 окт: [пост на стене]
├─ 16 окт: [комментарий в группе A]
├─ 17 окт: [лайк в группе B]
└─ 18 окт: [комментарий в группе C]
```

**Dashboard показывает**: Timeline всех действий пользователя

---

## Преимущества новой логики

### ✅ Простота

**БЫЛО**: Сложный алгоритм matching тем (50% overlap, lookback 7 days, hash generation)  
**СТАЛО**: Простая формула `source_{id}_scenario_{id}`

### ✅ Предсказуемость

**БЫЛО**: Непонятно когда создастся новая цепочка vs добавится в существующую  
**СТАЛО**: Всегда одна цепочка для source+scenario

### ✅ Timeline

**БЫЛО**: Разрозненные цепочки по темам  
**СТАЛО**: Хронологическая лента всех событий источника

### ✅ UX

**БЫЛО**: Одноэлементные цепочки нужно разворачивать вручную  
**СТАЛО**: Автоматически развёрнуты при загрузке

---

## Обратная совместимость

### Старые цепочки

**Формат**: `chain_16_abc123` (с hash)  
**Статус**: Останутся в БД, продолжат работать  
**Новые анализы**: Будут создавать новые цепочки `source_16` или `source_16_scenario_X`

### Миграция (не требуется)

Старые и новые цепочки **сосуществуют** без конфликтов.

Если нужна чистка:
```sql
-- Optional: удалить старые цепочки (после проверки)
DELETE FROM social_manager.ai_analytics 
WHERE topic_chain_id LIKE 'chain_%' 
  AND topic_chain_id NOT LIKE 'source_%';
```

---

## Тестирование

### 1. Проверить новую логику цепочек

```bash
# Запустить анализ источника 16 со сценарием 5
python cli/scheduler.py run --source-id 16

# Проверить topic_chain_id
SELECT id, source_id, topic_chain_id, analysis_date 
FROM social_manager.ai_analytics 
WHERE source_id = 16 
ORDER BY id DESC LIMIT 5;

# Ожидаемый результат: topic_chain_id = "source_16_scenario_5"
```

### 2. Проверить date range

```bash
# Установить date_from в Source.params
UPDATE social_manager.sources 
SET params = jsonb_set(
    COALESCE(params, '{}'::jsonb), 
    '{collection,date_from}', 
    '"2025-01-01T00:00:00Z"'
)
WHERE id = 16;

# Запустить сбор
python cli/scheduler.py run --source-id 16

# Проверить логи VK
grep "VK date_from filter" logs/app.log

# Ожидаемый вывод:
# VK date_from filter: 2025-01-01T00:00:00+00:00 (ts: 1735689600)
```

### 3. Проверить автораскрытие

```bash
# Открыть dashboard
http://localhost:8000/dashboard/topic-chains

# Проверить:
# 1. Цепочки с 1 анализом → развёрнуты сразу
# 2. Цепочки с 2+ анализами → свёрнуты, есть кнопка
# 3. Ctrl+Shift+R для жёсткого обновления JS
```

---

## Files Modified

### Backend:
1. ✅ `app/services/ai/analyzer.py`
   - Упрощён `_generate_topic_chain_id()` → simple source+scenario ID
   - Убрана сложная логика topic matching
   
2. ✅ `app/services/social/vk_client.py`
   - Добавлена поддержка `date_from` / `date_to` из params
   - Приоритет: date_from > last_checked > no filter

### Frontend:
3. ✅ `app/static/js/dashboard.js`
   - Условная кнопка: показывается только если `analyses_count > 1`
   - Автораскрытие: class `.show` если `analyses_count === 1`

4. ✅ `app/templates/topic_chains_dashboard.html`
   - Автозагрузка evolution для `.chain-evolution.show`

---

## Next Steps (Вопрос 3 - User Activity)

**Осталось реализовать**: Event-based анализ активности пользователя

### План:
1. **ContentType** расширить: добавить `USER_ACTIONS`
2. **VKClient** расширить: метод `collect_user_activity()`
3. **Analyzer** расширить: event-based режим через `scope.event_based = True`
4. **Summary_data** структура:
```json
{
  "events": [
    {
      "type": "post|comment|like",
      "time": "2025-10-18T10:30:00Z",
      "annotation": "...",
      "sentiment": 0.7
    }
  ]
}
```

5. **Dashboard** расширить: отображение событий в timeline

**Приоритет**: Medium (требует расширения архитектуры)

---

**Status**: ✅ Implemented (Questions 1 & 2)  
**Pending**: Question 3 (User Activity Monitoring)

**Author**: Factory Droid  
**Date**: Current Session
