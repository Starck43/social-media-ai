# Notifications, Dashboard & Messenger Integration

## Обзор системы

Полная система для управления уведомлениями, мониторинга через dashboard и интеграции с мессенджерами (Telegram, VK) для отправки критических уведомлений админу.

## Архитектура

```
┌─────────────────┐      ┌──────────────────┐      ┌─────────────────┐
│  Services/API   │─────>│  Notifications   │─────>│  Messenger      │
│                 │      │  (Database)      │      │  (Telegram/VK)  │
└─────────────────┘      └──────────────────┘      └─────────────────┘
         │                        │                         │
         │                        │                         │
         v                        v                         v
┌─────────────────┐      ┌──────────────────┐      ┌─────────────────┐
│  Admin Panel    │      │   Dashboard API  │      │  Admin Chat     │
│  (Views)        │      │   (Filters)      │      │  (Critical)     │
└─────────────────┘      └──────────────────┘      └─────────────────┘
```

## Компоненты

### 1. Notification Model & Manager

**Модель:** `app/models/notification.py`

```python
class Notification:
    id: int
    title: str
    message: str
    notification_type: NotificationType  # Enum тип
    is_read: bool
    related_entity_type: str  # 'source', 'platform', 'analysis'
    related_entity_id: int
    created_at: datetime
    updated_at: datetime
```

**Менеджер:** `app/models/managers/notification_manager.py`

Предоставляет методы:
- `create_notification()` - создание уведомления
- `get_unread()` - получение непрочитанных
- `mark_as_read()` - пометить прочитанным
- `get_by_type()` - фильтр по типу
- `get_recent()` - последние уведомления

### 2. Notification Service

**Сервис:** `app/services/notifications/service.py`

Высокоуровневый сервис для работы с уведомлениями:

```python
from app.services.notifications.service import notify

# Создание с автоотправкой в мессенджер
notification = await notify.create(
    title="Critical Error",
    message="API connection failed",
    ntype=NotificationType.API_ERROR,
    entity_type="platform",
    entity_id=platform_id,
    send_to_messenger=True  # Отправит в Telegram
)

# Готовые методы для частых случаев
await notify.report_ready("Analytics", "Dashboard")
await notify.api_error("VK API failed", "Connection timeout")
await notify.rate_limit_warning("VK", remaining=10)
await notify.keyword_mention(source_id, "important", "Context text")
```

### 3. Messenger Service

**Сервис:** `app/services/notifications/messenger.py`

Отправка уведомлений в мессенджеры:

```python
from app.services.notifications.messenger import messenger_service

# Отправка в Telegram
result = await messenger_service.send_notification(
    title="Alert",
    message="Something happened",
    notification_type=NotificationType.API_ERROR,
    messenger="telegram",  # или "vk" или "all"
    recipient_id="chat_id"  # опционально
)

# Критическое уведомление (во все мессенджеры)
await messenger_service.send_critical_alert(
    title="System Error",
    message="Database connection lost",
    error_details="Connection timeout after 30s"
)

# Готовые методы
await messenger_service.send_report_ready("Weekly Report", "/reports/weekly")
await messenger_service.send_trend_alert("VK Group", "Negative trend detected", "negative")
```

**Настройка в `.env`:**
```bash
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_ADMIN_CHAT_ID=your_chat_id_here
VK_APP_SECRET=your_vk_secret
```

### 4. API Endpoints - Notifications

**Роутер:** `app/api/v1/endpoints/notifications.py`

#### Список уведомлений с фильтрами
```http
GET /api/v1/notifications/notifications
    ?is_read=false
    &notification_type=API_ERROR
    &since=2024-01-01T00:00:00
    &limit=50
    &offset=0
```

Response:
```json
[
    {
        "id": 1,
        "title": "Error collecting data",
        "message": "Failed to collect...",
        "notification_type": "API_ERROR",
        "is_read": false,
        "related_entity_type": "source",
        "related_entity_id": 5,
        "created_at": "2024-10-11T12:00:00"
    }
]
```

#### Статистика уведомлений
```http
GET /api/v1/notifications/notifications/stats?since=2024-01-01
```

Response:
```json
{
    "total": 150,
    "unread": 25,
    "by_type": {
        "API_ERROR": 10,
        "REPORT_READY": 40,
        "TREND_ALERT": 5
    }
}
```

#### Создание уведомления
```http
POST /api/v1/notifications/notifications
Authorization: Bearer {token}

{
    "title": "Custom Alert",
    "message": "Something important",
    "notification_type": "SYSTEM_UPDATE",
    "related_entity_type": "platform",
    "related_entity_id": 1
}
```

#### Пометить как прочитанное
```http
POST /api/v1/notifications/notifications/{id}/mark-read
```

#### Пометить все как прочитанные
```http
POST /api/v1/notifications/notifications/mark-all-read
```

#### Очистка старых уведомлений
```http
POST /api/v1/notifications/notifications/cleanup?days=30
Authorization: Bearer {admin_token}
```

### 5. API Endpoints - Dashboard

**Роутер:** `app/api/v1/endpoints/dashboard.py`

#### Общая статистика
```http
GET /api/v1/dashboard/dashboard/stats
    ?platform_id=1
    &source_type=GROUP
    &since=2024-01-01
```

Response:
```json
{
    "total_sources": 50,
    "active_sources": 45,
    "total_platforms": 2,
    "active_platforms": 2,
    "total_analytics": 1250,
    "unread_notifications": 15,
    "sources_by_platform": {
        "VK": 30,
        "Telegram": 20
    },
    "sources_by_type": {
        "GROUP": 25,
        "CHANNEL": 15,
        "USER": 10
    },
    "analytics_by_period": {
        "daily": 1000,
        "weekly": 200,
        "monthly": 50
    }
}
```

#### Источники с деталями
```http
GET /api/v1/dashboard/dashboard/sources
    ?platform_id=1
    &source_type=GROUP
    &is_active=true
    &has_scenario=true
    &limit=50
```

Response:
```json
[
    {
        "id": 1,
        "name": "Tech News",
        "platform_name": "VK",
        "source_type": "GROUP",
        "is_active": true,
        "last_checked": "2024-10-11T12:00:00",
        "analytics_count": 45,
        "bot_scenario_name": "Sentiment Analysis"
    }
]
```

#### Аналитика с фильтрами
```http
GET /api/v1/dashboard/dashboard/analytics
    ?source_id=1
    &period_type=DAILY
    &since=2024-01-01
    &limit=50
```

#### Тренды для источника
```http
GET /api/v1/dashboard/dashboard/trends/1
    ?days=30
    &metric=sentiment  # или activity, engagement
```

Response:
```json
[
    {
        "date": "2024-10-11",
        "value": 0.75,
        "label": "positive"
    },
    {
        "date": "2024-10-12",
        "value": 0.65,
        "label": "positive"
    }
]
```

#### Последние уведомления для дашборда
```http
GET /api/v1/dashboard/dashboard/notifications/recent?limit=10
```

### 6. Admin Panel Integration

**Обновлённые Views:** `app/admin/views.py`

#### NotificationAdmin

**Новые возможности:**
- Автоматическая сортировка по дате (новые сверху)
- Форматирование статуса: ✅ Прочитано / 📬 Новое
- Action: "Пометить прочитанным" - массово помечает выбранные
- Action: "Отправить в мессенджер" - отправляет в Telegram
- Логирование всех действий

**Использование:**
1. Выбрать уведомления в списке
2. Нажать "Пометить прочитанным" для массовой пометки
3. Нажать "Отправить в мессенджер" для критических

#### BotScenarioAdmin

**Новые возможности:**
- Кастомный шаблон с визуальным редактором scope
- Предустановленные пресеты (Sentiment, Trends, Engagement, Keywords)
- JSON редактор с форматированием
- Шаблоны промптов
- Action: "Активировать/Деактивировать" - переключение статуса
- Логирование изменений

**Использование:**
1. При создании/редактировании открывается визуальный редактор
2. Выбрать пресет или создать кастомный scope
3. Редактировать JSON переменных
4. Промпт автоматически подставляет переменные через {variable_name}

### 7. Шаблоны

#### Bot Scenario Create Template
**Файл:** `app/templates/sqladmin/bot_scenario_create.html`

**Особенности:**
- Визуальный редактор scope
- 4 предустановленных пресета:
  - Sentiment Analysis
  - Trend Detection
  - Engagement Analysis
  - Keyword Monitoring
- JSON редактор с валидацией
- Кнопки "Add Variable", "Format JSON"
- Шаблоны промптов

### 8. Интеграция в сервисы

#### Автоматические уведомления при ошибках

**ContentCollector** (`app/services/monitoring/collector.py`):

```python
# При ошибке сбора данных автоматически:
# 1. Логируется ошибка
# 2. Создаётся уведомление
# 3. Отправляется в Telegram админу

except Exception as e:
    logger.error(f"Error collecting: {e}")
    
    # Автоматическое уведомление
    await notify.create(
        title=f"Error collecting from {source.name}",
        message=f"Failed: {str(e)}",
        ntype=NotificationType.API_ERROR,
        entity_type="source",
        entity_id=source.id,
        send_to_messenger=True  # В Telegram
    )
```

## Примеры использования

### 1. Мониторинг с уведомлениями

```python
from app.services.monitoring.collector import ContentCollector
from app.services.notifications.service import notify

collector = ContentCollector()

# Сбор с автоуведомлениями при ошибках
result = await collector.collect_from_source(source, analyze=True)

if result:
    # Создать уведомление об успехе
    await notify.report_ready(
        entity_type="source",
        entity_id=source.id,
        title=f"Data collected from {source.name}",
        message=f"Collected {result['content_count']} items"
    )
```

### 2. Критические алерты

```python
from app.services.notifications.messenger import messenger_service

# При критической ошибке отправить админу
try:
    # Какая-то критическая операция
    result = await critical_operation()
except Exception as e:
    # Отправить критический алерт
    await messenger_service.send_critical_alert(
        title="Critical System Error",
        message="Failed to connect to database",
        error_details=str(e)
    )
```

### 3. Интеграция с dashboard

```python
from app.api.v1.endpoints.dashboard import get_dashboard_stats

# Получить статистику для dashboard
stats = await get_dashboard_stats(
    platform_id=1,
    source_type=SourceType.GROUP,
    since=date.today() - timedelta(days=30)
)

# stats содержит всю нужную информацию для отображения
print(f"Active sources: {stats.active_sources}")
print(f"Unread notifications: {stats.unread_notifications}")
```

### 4. Фильтрация уведомлений

```python
# Получить только непрочитанные ошибки за последнюю неделю
notifications = await Notification.objects.filter(
    is_read=False,
    notification_type=NotificationType.API_ERROR,
    created_at__gte=datetime.now() - timedelta(days=7)
)

for n in notifications:
    print(f"{n.title}: {n.message}")
    await Notification.objects.update_by_id(n.id, is_read=True)
```

## Типы уведомлений

```python
class NotificationType(Enum):
    REPORT_READY = "report_ready"
    MOOD_CHANGE = "mood_change"
    TREND_ALERT = "trend_alert"
    TOPIC_RESUMED = "topic_resumed"
    API_ERROR = "api_error"
    CONNECTION_ERROR = "connection_error"
    SOURCE_INACTIVE = "source_inactive"
    RATE_LIMIT_WARNING = "rate_limit_warning"
    BOT_COMMENT = "bot_comment"
    BOT_SKIPPED = "bot_skipped"
    SYSTEM_BACKUP = "system_backup"
    SYSTEM_UPDATE = "system_update"
    SUBSCRIBED_ACTIVITY = "subscribed_activity"
    KEYWORD_MENTION = "keyword_mention"
```

**Критические типы** (отправляются в мессенджеры автоматически):
- API_ERROR
- CONNECTION_ERROR
- SYSTEM_BACKUP

## Логирование

Все критические операции логируются:

```python
import logging
logger = logging.getLogger(__name__)

# В сервисах
logger.info(f"Creating notification: {title}")
logger.error(f"Failed to send to messenger: {e}", exc_info=True)

# В API
logger.info(f"User {user.username} listing notifications")
logger.warning(f"Notification {id} not found")

# В admin
logger.info(f"Scenario {pk} status changed to {status}")
logger.error(f"Error toggling scenario {pk}: {e}")
```

## Настройка Telegram Bot

### 1. Создать бота
1. Открыть [@BotFather](https://t.me/botfather) в Telegram
2. Отправить `/newbot`
3. Следовать инструкциям
4. Получить токен: `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`

### 2. Получить Chat ID
1. Отправить любое сообщение боту
2. Открыть: `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Найти `"chat":{"id":123456789}`

### 3. Настроить в `.env`
```bash
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_ADMIN_CHAT_ID=123456789
```

## Testing

```python
# Тест отправки уведомления
from app.services.notifications.messenger import messenger_service

result = await messenger_service._send_telegram(
    title="Test",
    message="Hello from bot!",
    is_critical=False
)

print(result)  # {'success': True, 'chat_id': '...'}
```

## Troubleshooting

### Уведомления не отправляются в Telegram
1. Проверьте `TELEGRAM_BOT_TOKEN` и `TELEGRAM_ADMIN_CHAT_ID` в `.env`
2. Убедитесь, что вы отправили боту хотя бы одно сообщение
3. Проверьте логи: `logger.error(...)`

### Dashboard не показывает данные
1. Проверьте наличие источников: `await Source.objects.filter()`
2. Проверьте наличие аналитики: `await AIAnalytics.objects.filter()`
3. Проверьте фильтры в запросе

### Scope не применяется в сценарии
1. Убедитесь, что scope - валидный JSON
2. Проверьте, что переменные в промпте совпадают с ключами scope
3. Используйте `{variable_name}` для подстановки

## Best Practices

1. **Используйте типизированные уведомления** - выбирайте правильный NotificationType
2. **Логируйте критические операции** - используйте logger.error для ошибок
3. **Отправляйте в мессенджер только критическое** - `send_to_messenger=True` для важных событий
4. **Очищайте старые уведомления** - используйте cleanup endpoint регулярно
5. **Фильтруйте уведомления** - используйте API фильтры для эффективного поиска
6. **Мониторьте dashboard** - регулярно проверяйте статистику и тренды
7. **Создавайте переиспользуемые сценарии** - один сценарий для похожих источников

## Security

1. **Admin endpoints** требуют `is_superuser=True`
2. **Токены мессенджеров** хранятся только в `.env`, не в коде
3. **Логи не содержат** токены и секреты
4. **Rate limiting** применяется к API endpoints
5. **Валидация данных** через Pydantic models
