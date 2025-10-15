# Rate Limiting: Нужен ли rate_limit_per_hour?

## Текущая ситуация

### Что есть:
1. **LLM Rate Limiting** ✅ (реализовано)
   - `LLMRateLimiter` в `optimizer.py`
   - Контролирует запросы к OpenAI, DeepSeek, Anthropic
   - Лимиты: 60 RPM (OpenAI), 50 RPM (Anthropic)

2. **Platform Rate Tracking** ⚠️ (частично)
   - `Platform.rate_limit_remaining` - остаток лимита
   - `Platform.rate_limit_reset_at` - время сброса
   - НО: логики проверки нет!

3. **Collection Throttling** ✅ (реализовано)
   - `BotScenario.collection_interval_hours` (мин. 1 час)
   - `Source.last_checked` (CheckpointManager)
   - Trigger система (экономия токенов)

### Чего нет:
- ❌ Логики проверки `rate_limit_per_hour` из params
- ❌ Задержек перед запросами к VK/Telegram API
- ❌ Обработки 429 Too Many Requests от соцсетей

## Анализ необходимости

### Реальные лимиты соцсетей:

**VK API:**
- 3 запроса в секунду = **10,800 запросов/час**
- Для сообществ: до 20 req/sec с правами токена

**Telegram Bot API:**
- 30 сообщений в секунду для обычных чатов
- Без строгих лимитов на получение обновлений

### Текущая нагрузка:

При `collection_interval_hours = 1`:
```
10 источников × 1 запрос/час = 10 запросов/час
100 источников × 1 запрос/час = 100 запросов/час
```

**Вывод:** Даже при 1000 источниках вы используете < 10% лимита VK API!

## Рекомендация: **Убрать rate_limit_per_hour** ⭐

### Аргументы ЗА удаление:

1. **Не используется в коде**
   - Нет логики проверки
   - Только конфигурационный мусор

2. **Избыточно**
   - `collection_interval_hours` уже контролирует частоту
   - CheckpointManager предотвращает дубли
   - Triggers экономят запросы к LLM (не к соцсетям, но все равно снижают нагрузку)

3. **Реальные лимиты очень высокие**
   - VK: 10,800 req/hour
   - Telegram: практически безлимитный
   - Ваша архитектура физически не может превысить лимиты

4. **Простота > Сложность**
   - Меньше параметров = меньше путаницы
   - YAGNI (You Aren't Gonna Need It)

### Что оставить:

```python
# Platform model
class Platform(Base):
    # Убрать из params:
    # ❌ "rate_limit_per_hour": 1000
    
    # Оставить поля для мониторинга (на будущее):
    rate_limit_remaining: Mapped[int] = Column(Integer, nullable=True)
    rate_limit_reset_at: Mapped[DateTime] = Column(DateTime, nullable=True)
```

**Зачем оставить поля?**
- Если VK/Telegram API вернет заголовки `X-RateLimit-Remaining`
- Можно логировать в будущем
- Минимальная overhead (nullable поля)

### Упрощенные params:

**VK:**
```json
{
  "api_base_url": "https://api.vk.com/method",
  "api_version": "5.199"
}
```

**Telegram:**
```json
{
  "api_base_url": "https://api.telegram.org",
  "bot_token_env": "TELEGRAM_BOT_TOKEN"
}
```

## Альтернатива: Реализовать полноценный rate limiting

Если все-таки нужен строгий контроль, реализуйте:

### 1. PlatformRateLimiter (аналог LLMRateLimiter)

```python
# app/services/social/rate_limiter.py
class PlatformRateLimiter:
    """Rate limiter for social platform APIs."""
    
    def __init__(self):
        self.request_history: dict[int, list[datetime]] = defaultdict(list)
    
    async def acquire(self, platform: Platform):
        """Check rate limit before API call."""
        max_per_hour = platform.params.get('rate_limit_per_hour', 10000)
        
        now = datetime.now(timezone.utc)
        platform_history = self.request_history[platform.id]
        
        # Remove requests older than 1 hour
        platform_history[:] = [
            ts for ts in platform_history 
            if (now - ts).total_seconds() < 3600
        ]
        
        # Check if limit exceeded
        if len(platform_history) >= max_per_hour:
            oldest = platform_history[0]
            wait_time = 3600 - (now - oldest).total_seconds()
            
            logger.warning(
                f"Rate limit reached for {platform.name}, "
                f"waiting {wait_time:.1f}s"
            )
            await asyncio.sleep(wait_time)
            platform_history.clear()
        
        # Record this request
        platform_history.append(now)
```

### 2. Интегрировать в клиенты

```python
# app/services/social/vk_client.py
class VKClient(BaseClient):
    def __init__(self, platform: Platform):
        super().__init__(platform)
        self.rate_limiter = PlatformRateLimiter()
    
    async def _make_request(self, method: str, params: dict):
        # Wait if rate limit would be exceeded
        await self.rate_limiter.acquire(self.platform)
        
        # Make actual request
        response = await self.session.get(...)
        
        # Update platform rate limit tracking
        if 'X-RateLimit-Remaining' in response.headers:
            await Platform.objects.update_rate_limit(
                self.platform.id,
                remaining=int(response.headers['X-RateLimit-Remaining']),
                reset_at=...
            )
```

### 3. Добавить в Scheduler

```python
# app/services/scheduler.py
async def _collect_platform_content(self, client, source, checkpoint):
    """Collect with rate limiting."""
    # Client handles rate limiting internally
    content = await client.collect_posts(...)  # Will wait if needed
    return content
```

## Итоговая рекомендация

### ✅ Для вашего проекта: **Убрать rate_limit_per_hour**

**Причины:**
1. Текущая архитектура уже ограничивает частоту (collection_interval_hours)
2. Реальные лимиты соцсетей в 100-1000 раз выше ваших потребностей
3. Нет логики проверки = мертвый код
4. Простота важнее преждевременной оптимизации

**Действия:**
1. Удалить `"rate_limit_per_hour"` из params в БД
2. Обновить документацию/примеры без этого параметра
3. Оставить `rate_limit_remaining` и `rate_limit_reset_at` (на будущее)

### 🔮 Когда реализовать полноценный rate limiting:

Только если:
- У вас > 1000 активных источников
- `collection_interval_hours < 1` (невозможно сейчас, мин. = 1)
- Получаете 429 ошибки от VK/Telegram (крайне маловероятно)
- Нужна точная квота по деньгам (paid VK API plans)

**Пока этого нет - YAGNI!** 🚀
