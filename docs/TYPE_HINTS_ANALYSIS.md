# Type Hints Analysis: JSON Fields

## 🔍 Проблема

PyCharm показывает предупреждение: **"Unresolved attribute reference 'get' for class 'JSON'"**

Происходит в коде:
```python
platform_params = self.platform.params.get('api_version', '5.199')
source_params = source.params.get('collection', {})
```

Где `params` определён как:
```python
params: Mapped[JSON] = mapped_column(JSON, default=dict, nullable=False)
```

---

## ❌ Почему CustomModelConverter НЕ решит проблему?

### Model Converters в SQLAdmin:
- ✅ Работают **только для форм в админ-панели** (WTForms)
- ✅ Конвертируют SQLAlchemy типы → WTForms поля
- ❌ **НЕ влияют** на type hints в Python коде
- ❌ **НЕ влияют** на runtime поведение моделей

### Пример из документации:
```python
class CustomModelConverter(ModelConverter):
    @converts("JSON", "JSONB")
    def conv_json(self, model, prop, kwargs) -> UnboundField:
        return CustomJSONField(**kwargs)  # WTForms field для HTML формы
```

**Это только меняет, как JSON отображается в админке**, но не меняет type hints для `Platform.params` или `Source.params`.

---

## ✅ Правильные решения

### Решение 1: Исправить Type Hints в моделях (РЕКОМЕНДУЕТСЯ)

**Текущий код:**
```python
params: Mapped[JSON] = mapped_column(JSON, default=dict, nullable=False)
```

**Исправленный код:**
```python
from typing import Dict, Any

params: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
```

**Преимущества:**
- ✅ IDE понимает, что `params` это dict
- ✅ Autocomplete работает для `.get()`, `.keys()`, и т.д.
- ✅ Не ломает runtime код
- ✅ Совместимо с SQLAlchemy 2.0

**Где применить:**
- `app/models/platform.py` - поле `params`
- `app/models/source.py` - поле `params`
- `app/models/bot_scenario.py` - поля `scope` и `analysis_types`

---

### Решение 2: Type Cast в клиентах

**Если не хотим менять модели:**
```python
from typing import cast, Dict, Any

platform_params = cast(Dict[str, Any], self.platform.params or {})
source_params = cast(Dict[str, Any], source.params.get('collection', {})) if source.params else {}
```

**Преимущества:**
- ✅ Не меняет модели
- ✅ Локальное решение в каждом файле

**Недостатки:**
- ❌ Многословно
- ❌ Нужно повторять в каждом файле

---

### Решение 3: Suppress Warning (НЕ РЕКОМЕНДУЕТСЯ)

```python
# noinspection PyUnresolvedReferences
platform_params = self.platform.params.get('api_version', '5.199')
```

**Недостатки:**
- ❌ Скрывает проблему, не решает её
- ❌ Нет autocomplete

---

## 📊 SourceType Coverage в VKClient

### Поддерживаемые типы в VKClient:

| SourceType | Поддержка | VK API Method | Примечания |
|-----------|----------|--------------|-----------|
| **USER** | ✅ Полная | `wall.get`, `users.get` | owner_id положительный |
| **GROUP** | ✅ Полная | `wall.get`, `groups.getById` | owner_id отрицательный |
| **CHANNEL** | ✅ Полная | `wall.get`, `groups.getById` | owner_id отрицательный |
| **PUBLIC** | ⚠️ Частичная | `wall.get`, `groups.getById` | То же что GROUP |
| **EVENT** | ⚠️ Частичная | `wall.get`, `groups.getById` | То же что GROUP |
| **MARKET** | ⚠️ Частичная | `wall.get`, `groups.getById` | То же что GROUP |
| **ALBUM** | ❌ Нет | - | Требует `photos.get` API |
| **CHAT** | ❌ Нет | `messages.getHistory` | Требует групповую авторизацию |
| **PAGE** | ✅ Алиас | - | То же что GROUP |
| **SUPERGROUP** | ❌ N/A | - | Только Telegram |
| **BOT** | ❌ N/A | - | Только Telegram |
| **BROADCAST** | ❌ N/A | - | Только Telegram |

### Типы VK, требующие доработки:

#### 1. **ALBUM** (Фотоальбомы)
```python
# Нужно добавить:
SourceType.ALBUM: {
    "photos": "photos.get",
    "info": "photos.getAlbums",
}
```

#### 2. **CHAT** (Беседы)
```python
# Нужно добавить:
SourceType.CHAT: {
    "messages": "messages.getHistory",
    "info": "messages.getConversationById",
}
# Требует: user access token или community token с правами на сообщения
```

#### 3. **MARKET** (Магазины)
```python
# Можно улучшить:
SourceType.MARKET: {
    "products": "market.get",
    "info": "market.getById",
}
```

---

## 🎯 Рекомендации

### 1. Исправить Type Hints (CRITICAL)
**Файлы для изменения:**
- `app/models/platform.py`
- `app/models/source.py`
- `app/models/bot_scenario.py`

**Изменение:**
```python
# Было:
params: Mapped[JSON] = mapped_column(JSON, default=dict)

# Стало:
from typing import Dict, Any
params: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)
```

### 2. Расширить VKClient (OPTIONAL)
Добавить поддержку дополнительных SourceType:
- ALBUM - для фотоальбомов
- CHAT - для бесед (требует расширенные права)
- MARKET - для товаров

### 3. CustomModelConverter (OPTIONAL)
Использовать **только для улучшения отображения JSON в админке**, но не для type hints:

```python
# app/admin/converters.py
from typing import Any, ClassVar, Type
import json
from wtforms import Field
from sqladmin import ModelConverter
from sqladmin.forms import converts
from sqlalchemy.orm import ColumnProperty

class CustomJSONField(Field):
    def _value(self) -> str:
        if self.raw_data:
            return self.raw_data[0]
        elif self.data:
            return json.dumps(self.data, ensure_ascii=False, indent=2)
        else:
            return "{}"

class CustomModelConverter(ModelConverter):
    @converts("JSON", "JSONB")
    def conv_json(self, model: type, prop: ColumnProperty, kwargs: dict[str, Any]):
        return CustomJSONField(**kwargs)

# В BaseAdmin:
class BaseAdmin(ModelView):
    form_converter: ClassVar[Type[CustomModelConverter]] = CustomModelConverter
```

**Результат:** JSON поля в админке будут красиво форматированы, но type hints останутся неизменными.

---

## 📝 Итоговый Plan

1. ✅ **Fix Type Hints** - изменить `Mapped[JSON]` → `Mapped[Dict[str, Any]]`
2. ⚠️ **Expand VKClient** - добавить ALBUM, CHAT, MARKET (опционально)
3. 💡 **Add CustomModelConverter** - для красивого отображения в админке (опционально)
