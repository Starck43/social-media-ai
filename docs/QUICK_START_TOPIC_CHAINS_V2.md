# Quick Start: Topic Chains V2

**Версия**: 2.0  
**Дата**: Current Session

---

## Что изменилось

### ✅ Новая логика цепочек

**Было**: Сложный matching тем (50% overlap)  
**Стало**: **1 источник + 1 сценарий = 1 цепочка**

Все анализы одного источника объединяются в **единую хронологическую ленту**.

---

## Быстрый старт

### 1. Создать источник с ограничением по датам

**В админке** → Sources → Edit

**Установить params**:
```json
{
  "collection": {
    "date_from": "2025-01-01T00:00:00Z",
    "date_to": "2025-12-31T23:59:59Z",
    "count": 100
  }
}
```

**Результат**: VK API соберёт только посты за 2025 год

---

### 2. Запустить сбор и анализ

```bash
# Запустить планировщик
python cli/scheduler.py run --source-id 16

# Или через CLI (если реализовано)
python cli/collect.py --source-id 16 \
    --date-from "2025-01-01T00:00:00Z" \
    --date-to "2025-01-31T23:59:59Z"
```

---

### 3. Посмотреть результаты

**Открыть dashboard**:
```
http://localhost:8000/dashboard/topic-chains
```

**Проверить**:
- ✅ Цепочка имеет формат `source_16_scenario_5`
- ✅ Одноэлементные цепочки раскрыты сразу
- ✅ Показаны **даты постов**, а не даты анализа
- ✅ Видны метрики: посты, реакции, тональность

---

## Примеры использования

### Сценарий 1: Мониторинг группы ВК за месяц

**Задача**: Проанализировать всю активность группы за январь 2025

**Шаги**:
1. Создать Source:
   - Platform: ВКонтакте
   - Type: GROUP
   - External ID: `-123456789` (ID группы)
   - Params:
     ```json
     {
       "collection": {
         "date_from": "2025-01-01T00:00:00Z",
         "date_to": "2025-01-31T23:59:59Z"
       }
     }
     ```

2. Создать Scenario:
   - Name: "Мониторинг группы"
   - Content Types: ["posts", "comments"]
   - Analysis Types: ["sentiment", "topics"]

3. Запустить: `python cli/scheduler.py run --source-id {ID}`

4. Результат на dashboard:
   ```
   Цепочка: source_16_scenario_5
   📅 1 янв - 31 янв
   📊 10 анализов (по одному на каждый день с активностью)
   ```

---

### Сценарий 2: Непрерывный мониторинг с checkpoint

**Задача**: Каждый день собирать только новые посты

**Шаги**:
1. Создать Source **БЕЗ** date_from/date_to в params
2. Source.last_checked будет автоматически обновляться
3. При каждом запуске VK API соберёт только посты **после last_checked**

**Логика**:
```
День 1: Соберёт все посты → last_checked = 2025-10-18 10:00
День 2: Соберёт только посты после 2025-10-18 10:00 → last_checked = 2025-10-19 10:00
День 3: Соберёт только посты после 2025-10-19 10:00
```

**Экономия**: 90-99% затрат на анализ (не анализируем повторно)

---

### Сценарий 3: Анализ конкретного периода

**Задача**: Проанализировать активность за период выборов (1-10 марта)

**Шаги**:
1. Установить в Source.params:
   ```json
   {
     "collection": {
       "date_from": "2025-03-01T00:00:00Z",
       "date_to": "2025-03-10T23:59:59Z"
     }
   }
   ```

2. Запустить анализ

3. После завершения **убрать** date_from/date_to (вернуться к checkpoint)

**Результат**: Собраны только посты за 1-10 марта, ничего лишнего

---

## Dashboard: Что показывается

### Одноэлементная цепочка

**Автоматически развёрнута**:

```
┌────────────────────────────────────────────┐
│ ✨ Обсуждение выборов                       │
│ 📅 5 мар - 5 мар | 📊 1 анализ              │
│ 🔄 5 мар, 14:30 (последняя проверка)       │
│                                            │
│ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│ 5 мар, 14:30                               │
│                                            │
│ 💡 Описание                                │
│ Пользователи активно обсуждают...         │
│                                            │
│ 😊 Смешанный | 📄 156 постов | ❤️ 1234    │
│                                            │
│ Выборы  Политика  Дебаты                  │
└────────────────────────────────────────────┘
```

### Многоэлементная цепочка

**Свёрнута, есть кнопка**:

```
┌────────────────────────────────────────────┐
│ ✨ Мониторинг группы                       │
│ 📅 1 мар - 10 мар | 📊 10 анализов         │
│ 🔄 10 мар, 18:00                           │
│                                            │
│ Выборы  Кандидаты  Результаты  Протесты   │
│                                            │
│ [Показать эволюцию тем ▶]                  │
└────────────────────────────────────────────┘

[При клике раскроется timeline всех 10 анализов]
```

---

## API Endpoints

### Получить все цепочки

```bash
GET /api/v1/dashboard/topic-chains?source_id=16&limit=50
```

**Response**:
```json
[
  {
    "chain_id": "source_16_scenario_5",
    "source_id": 16,
    "analyses_count": 3,
    "first_date": "2025-10-15",
    "last_date": "2025-10-18",
    "content_earliest_date": "2025-10-15T10:30:00Z",
    "content_latest_date": "2025-10-18T14:25:00Z",
    "topics": ["дизайн", "UX", "критика"],
    "source": {
      "name": "Иванов Иван",
      "platform": "vkontakte",
      "last_checked": "2025-10-18T14:30:00Z"
    }
  }
]
```

### Получить evolution цепочки

```bash
GET /api/v1/dashboard/topic-chains/source_16_scenario_5/evolution
```

**Response**:
```json
[
  {
    "analysis_date": "2025-10-15",
    "analysis_title": "Обсуждение дизайна",
    "analysis_summary": "Пользователи обсуждают...",
    "sentiment_score": 0.6,
    "sentiment_label": "Смешанный",
    "toxicity_score": 0.3,
    "total_posts": 67,
    "total_reactions": 462,
    "avg_reactions": 6.9,
    "topics": ["дизайн", "UX"]
  }
]
```

---

## Проверка работы

### 1. Проверить topic_chain_id

```sql
SELECT id, source_id, topic_chain_id, analysis_date 
FROM social_manager.ai_analytics 
WHERE source_id = 16 
ORDER BY id DESC LIMIT 10;
```

**Ожидаемый формат**:
- `source_16_scenario_5` (если есть сценарий)
- `source_16` (если без сценария)

### 2. Проверить date filtering

**В логах** (logs/app.log):
```
VK date_from filter: 2025-01-01T00:00:00+00:00 (ts: 1735689600)
VK date_to filter: 2025-01-31T23:59:59+00:00 (ts: 1738367999)
```

**Или checkpoint**:
```
VK checkpoint: collecting posts after 2025-10-18T10:00:00+00:00 (ts: 1729245600)
```

### 3. Проверить dashboard

1. Открыть: http://localhost:8000/dashboard/topic-chains
2. **Ctrl+Shift+R** (жёсткое обновление JS)
3. Проверить:
   - [ ] Цепочки имеют формат `source_X_scenario_Y`
   - [ ] Одноэлементные развёрнуты
   - [ ] Многоэлементные свёрнуты с кнопкой
   - [ ] Показаны даты постов (не анализа)
   - [ ] Видны метрики (посты, реакции, тональность)

---

## Troubleshooting

### Цепочки не объединяются

**Проблема**: Каждый анализ создаёт новую цепочку

**Причина**: Старая версия кода

**Решение**:
```bash
git pull origin main
git log --oneline | grep "Topic Chains V2"
# Должен быть commit d31baab или новее
```

### Dashboard не показывает автораскрытие

**Проблема**: Одноэлементные цепочки не раскрываются

**Решение**:
1. Ctrl+Shift+R (жёсткое обновление)
2. Проверить в Console (F12) → Network → dashboard.js (должен обновиться)
3. Проверить в Elements → найти `.chain-evolution.show`

### Date range не работает

**Проблема**: VK собирает все посты, игнорируя date_from

**Решение**:
1. Проверить формат в params:
   ```json
   {"collection": {"date_from": "2025-01-01T00:00:00Z"}}
   ```
2. Проверить логи:
   ```bash
   grep "VK date_from" logs/app.log
   ```
3. Если нет записей → перезапустить с флагом debug

---

## Миграция со старой версии

### Старые цепочки (chain_16_abc123)

**Статус**: Останутся в БД, продолжат работать

**Новые анализы**: Создадут цепочки `source_16` или `source_16_scenario_5`

### Очистка (опционально)

```sql
-- Посмотреть старые цепочки
SELECT topic_chain_id, COUNT(*) 
FROM social_manager.ai_analytics 
WHERE topic_chain_id LIKE 'chain_%'
GROUP BY topic_chain_id;

-- Удалить (ОСТОРОЖНО!)
DELETE FROM social_manager.ai_analytics 
WHERE topic_chain_id LIKE 'chain_%';
```

---

## Next Steps

**Реализовано** (вопросы 1-2):
- ✅ Date range control (date_from/date_to)
- ✅ Auto-expand single chains
- ✅ Simplified topic chains logic

**Планируется** (вопрос 3):
- 🔜 Event-based user activity monitoring
- 🔜 VKClient.collect_user_activity()
- 🔜 Dashboard event timeline

---

**Автор**: Factory Droid  
**Дата**: Current Session  
**Версия**: 2.0
