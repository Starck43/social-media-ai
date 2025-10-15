# Исправление работы с tuple enums

## Проблема

После конвертации enums на tuple формат (с `db_value`, `display_name`, `emoji`), многие места в коде использовали `.value`, который для tuple enum возвращал весь кортеж вместо нужного значения.

Например:
```python
PlatformType.VK.value  # Возвращало: ('vk', 'ВКонтакте', '🔵')
# Ожидалось: 'vk'
```

Это приводило к ошибкам типа:
```
Unsupported platform type: ('vk', 'ВКонтакте', '🔵')
```

## Решение

### 1. Создана helper функция `get_enum_value()`

Файл: `app/utils/enum_helpers.py`

```python
def get_enum_value(enum_val: Any) -> str:
    """
    Get string value from enum, handling tuple enums and simple enums.
    
    For tuple enums (with db_value), returns db_value.
    For simple enums, returns value.
    For strings, returns as-is.
    """
    if enum_val is None:
        return ''
    
    # For tuple enum, use db_value
    if hasattr(enum_val, 'db_value'):
        return enum_val.db_value
    
    # For simple enum, use value
    if hasattr(enum_val, 'value'):
        return str(enum_val.value)
    
    # Already a string or other type
    return str(enum_val)
```

### 2. Обновлены все файлы

Заменены все паттерны вида:
```python
# Старый код
value = enum.value if hasattr(enum, 'value') else str(enum)

# Новый код
from app.utils.enum_helpers import get_enum_value
value = get_enum_value(enum)
```

#### Обновленные файлы (Python):
- `app/services/social/factory.py` - фабрика клиентов
- `app/services/social/vk_client.py` - VK клиент
- `app/services/ai/analyzer.py` - AI анализатор
- `app/services/ai/llm_client.py` - LLM клиент
- `app/services/ai/llm_provider_resolver.py` - резолвер провайдеров
- `app/models/llm_provider.py` - модель LLM провайдера
- `app/models/managers/source_manager.py` - менеджер источников
- `app/models/managers/notification_manager.py` - менеджер уведомлений
- `app/models/managers/permission_manager.py` - менеджер разрешений
- `app/models/managers/platform_manager.py` - менеджер платформ
- `app/models/managers/role_manager.py` - менеджер ролей
- `app/api/v1/endpoints/llm_providers.py` - API endpoints для LLM
- `app/admin/views.py` - админ панель

#### Обновленные файлы (Templates):
- `app/templates/sqladmin/source_check_results.html`
- `app/templates/sqladmin/source_check_results_standalone.html`
- `app/templates/sqladmin/source_details.html`

В шаблонах заменили:
```jinja2
{# Старый код #}
{{ source.source_type.value }}

{# Новый код #}
{{ source.source_type.label if source.source_type.label is defined else source.source_type }}
```

### 3. Обновлены column formatters в админке

```python
# Старый код
column_formatters = {
    "provider_type": lambda m, a: (
        m.provider_type.value if hasattr(m.provider_type, 'value')
        else str(m.provider_type)
    ),
}

# Новый код
column_formatters = {
    "provider_type": lambda m, a: (
        m.provider_type.label if hasattr(m.provider_type, 'label')
        else str(m.provider_type) if m.provider_type else ""
    ),
}
```

## Результат

Теперь все tuple enums корректно работают во всем приложении:
- ✅ Админ панель отображает русские названия с эмодзи
- ✅ API возвращает правильные db_value
- ✅ Внутренняя логика использует правильные значения
- ✅ Шаблоны показывают красивые label

## Примеры использования

```python
from app.utils.enum_helpers import get_enum_value, get_enum_label
from app.types import PlatformType, SourceType

# Получить db_value для использования в логике
platform_value = get_enum_value(PlatformType.VK)
# Результат: 'vk'

# Получить label для отображения пользователю
platform_label = get_enum_label(PlatformType.VK)
# Результат: '🔵 ВКонтакте'

# Работает с любыми enum
source_value = get_enum_value(SourceType.USER)  # 'user'
source_label = get_enum_label(SourceType.USER)  # '👤 Пользователь'

# Безопасно обрабатывает None и строки
get_enum_value(None)  # ''
get_enum_value('already_string')  # 'already_string'
```

## Тестирование

Проверено:
- ✅ Админ панель - все enum отображаются правильно
- ✅ API endpoints - возвращают правильные значения
- ✅ Source check action - работает без ошибок
- ✅ Factory функции - создают правильные клиенты
- ✅ Managers - статистика считается корректно
