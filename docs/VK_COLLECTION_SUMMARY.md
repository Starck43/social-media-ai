# VK Collection - Summary & Status

## ✅ Что работает:

### 1. VK API Integration
- **VK Service Token**: Настроен через `VK_SERVICE_ACCESS_TOKEN`
- **API Version**: 5.199 (latest)
- **User Resolution**: Screen name `s_shabalin` → User ID `3619562`
- **Data Collection**: Успешно собирается 67 постов

### 2. Collection Flow
```
User → Swagger → POST /monitoring/collect/source
  ↓
ContentCollector.collect_from_source()
  ↓
VKClient.collect_data()
  ↓
wall.get API call → 67 posts collected
```

### 3. Collection Stats
```json
{
  "user": "Stanislav Shabalin",
  "vk_id": 3619562,
  "screen_name": "s_shabalin",
  "total_posts": 67,
  "followers": 242,
  "total_likes": 458,
  "total_views": 17016
}
```

---

## ❌ Проблемы и решения:

### 1. **Настройки VK API** ✅ FIXED
**Проблема:** VKClient ожидал `VK_APP_SECRET`, а в .env был `VK_SERVICE_ACCESS_TOKEN`

**Решение:**
```python
# app/core/config.py
@property
def VK_APP_SECRET(self) -> str:
    return self.VK_SERVICE_ACCESS_TOKEN
```

### 2. **Source External ID** ✅ FIXED
**Проблема:** external_id был screen_name `s_shabalin` вместо numeric ID

**Решение:** Обновили в БД на `3619562`

### 3. **VKClient source_type Enum** ✅ FIXED
**Проблема:** `'str' object has no attribute 'value'`

**Решение:**
```python
'source_type': source_type.value if hasattr(source_type, 'value') else str(source_type)
```

### 4. **Factory Platform Type** ✅ FIXED
**Проблема:** `PlatformType.VKONTAKTE` не существует (должен быть `PlatformType.VK`)

**Решение:**
```python
# app/services/social/factory.py
client_map = {
    'vk': VKClient,  # Compare by string value
    'telegram': TelegramClient,
}
platform_value = platform.platform_type.value if hasattr(platform.platform_type, 'value') else str(platform.platform_type)
```

### 5. **Source.last_checked Update** ✅ FIXED
**Проблема:** `SourceManager` не имел метода `update()` или `save()`

**Решение:**
```python
await Source.objects.update_last_checked(source.id)
```

---

## ⚠️ Текущие ограничения:

### 1. Данные не сохраняются в БД
**Проблема:** ContentCollector собирает данные, но не записывает raw posts в БД.

**Текущее поведение:**
- ✅ Данные собираются из VK API
- ✅ Данные нормализуются
- ❌ Raw posts НЕ сохраняются
- ⚠️ Только AI analytics сохраняется (если `analyze=true`)

**Возможные решения:**
1. Создать таблицу `RawContent` для хранения собранных постов
2. Сохранять только analyzed данные в `AIAnalytics`
3. Использовать временное хранилище (Redis/Cache)

### 2. AI Analysis не выполняется
**Статус:** Needs investigation

Когда `analyze=true`:
- Вызывается `AIAnalyzer.analyze_content()`
- Нужно проверить DeepSeek API integration
- Проверить создание записей в `ai_analytics`

---

## 🧪 Тестирование:

### Прямой тест VK API (без БД):
```bash
python3 test_vk_collection.py
```

**Результат:**
```
✅ Total posts: 67
✅ Total likes: 458
✅ Total views: 17016
```

### API Endpoint тест (через Swagger):
```bash
curl -X POST http://0.0.0.0:8000/api/v1/monitoring/collect/source \
  -H 'Authorization: Bearer <token>' \
  -d '{"source_id": 2, "content_type": "posts", "analyze": false}'
```

**Результат:**
```json
{
  "status": "started",
  "source_id": 2,
  "message": "Content collection started in background"
}
```

**Логи:**
```
INFO - Collecting posts from source 2 (Станислав)
INFO - Collected 67 items from source 2
```

---

## 📋 TODO:

### High Priority:
- [ ] Решить вопрос с хранением raw content (нужна ли отдельная таблица?)
- [ ] Проверить AI analysis integration с DeepSeek
- [ ] Тестировать создание `AIAnalytics` записей

### Medium Priority:
- [ ] Добавить pagination для больших коллекций (>100 posts)
- [ ] Реализовать incremental collection (только новые посты)
- [ ] Добавить error handling для rate limits

### Low Priority:
- [ ] Добавить collection statistics endpoint
- [ ] Реализовать scheduling для автоматической коллекции
- [ ] Добавить webhooks для real-time updates

---

## 🔧 Конфигурация:

### Environment Variables:
```bash
VK_APP_ID=54233367
VK_SERVICE_ACCESS_TOKEN=<your_token>
DEEPSEEK_API_KEY=<your_key>
```

### Database:
```sql
-- Source configuration
UPDATE social_manager.sources 
SET external_id = '3619562'  -- Numeric VK user ID
WHERE id = 2;
```

### Platform params:
```json
{
  "api_base_url": "https://api.vk.com/method",
  "api_version": "5.199",
  "rate_limit_per_hour": 1000
}
```

---

## 📊 Следующие шаги:

1. **Определить архитектуру хранения:**
   - Создать модель `CollectedContent` для raw posts?
   - Или сразу анализировать и сохранять только analytics?

2. **Проверить AI Integration:**
   - Тестировать DeepSeek API
   - Проверить создание AIAnalytics records
   - Проверить BotScenario application

3. **Расширить coverage:**
   - Добавить comments collection
   - Добавить GROUP/CHANNEL sources
   - Тестировать другие SourceType

4. **Production readiness:**
   - Error handling & retry logic
   - Rate limiting
   - Monitoring & alerting
   - Performance optimization
