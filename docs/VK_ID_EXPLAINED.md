# VK: Screen Name vs Numeric ID

## Почему screen_name не работает?

### VK API требует numeric owner_id

VK API методы (wall.get, users.get, и т.д.) принимают **только numeric ID**:

```python
# ❌ НЕ РАБОТАЕТ
params = {
    "owner_id": "s_shabalin",  # screen_name
    "access_token": token
}

# ✅ РАБОТАЕТ
params = {
    "owner_id": 3619562,  # numeric ID
    "access_token": token
}
```

### Почему так?

**Screen name** - это просто "красивый URL":
- Можно изменить в настройках профиля
- Не уникален на 100% (может быть занят кем-то ещё)
- Это алиас для удобства пользователей

**Numeric ID** - это настоящий идентификатор:
- Присваивается при регистрации
- Никогда не меняется
- Уникальный для каждого пользователя
- Используется во всех API методах

---

## Как найти numeric ID по screen_name?

### Способ 1: Через VK API (автоматически)

```python
import httpx

async def resolve_screen_name(screen_name: str) -> int:
    url = "https://api.vk.com/method/utils.resolveScreenName"
    params = {
        "screen_name": screen_name,
        "access_token": YOUR_TOKEN,
        "v": "5.199"
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params)
        data = response.json()
        
        if 'response' in data and data['response']:
            return data['response']['object_id']
    
    return None

# Примеры:
# s_shabalin → 3619562
# durov → 1
# o_shamova → 28678007
```

### Способ 2: Через браузер (вручную)

#### Вариант A: Через фото профиля
1. Откройте профиль: `https://vk.com/s_shabalin`
2. Кликните на аватарку (фото профиля)
3. Посмотрите URL открывшегося фото:
   ```
   https://vk.com/photo3619562_457239017
                    ^^^^^^^
                    это numeric ID!
   ```

#### Вариант B: Через исходный код страницы
1. Откройте профиль: `https://vk.com/s_shabalin`
2. Нажмите `Ctrl+U` (View Source)
3. Найдите в коде:
   ```html
   <meta property="og:url" content="https://vk.com/id3619562">
   ```
   или
   ```javascript
   "oid": 3619562
   ```

#### Вариант C: Через меню "Отправить сообщение"
1. Откройте профиль
2. Нажмите "Отправить сообщение"
3. Посмотрите URL:
   ```
   https://vk.com/im?sel=3619562
                         ^^^^^^^
   ```

### Способ 3: Онлайн сервисы

Есть специальные сервисы для поиска ID:
- regvk.com/id
- vk-id.ru
- И другие

---

## В нашем приложении

### Текущая реализация

В `VKClient._build_params()` мы используем `external_id` напрямую как `owner_id`:

```python
def _build_params(self, source: Source, method: str) -> dict:
    params = {
        "owner_id": source.external_id,  # ❌ Если это screen_name - не работает!
        "count": 100,
        "access_token": settings.VK_SERVICE_ACCESS_TOKEN,
        "v": "5.199"
    }
    return params
```

### Улучшение: Auto-resolve

Можем добавить автоматическое определение:

```python
async def _resolve_external_id(self, external_id: str) -> str:
    """Convert screen_name to numeric ID if needed."""
    
    # Если уже numeric (или negative для групп)
    if external_id.lstrip('-').isdigit():
        return external_id
    
    # Если screen_name - резолвим
    url = "https://api.vk.com/method/utils.resolveScreenName"
    params = {
        "screen_name": external_id,
        "access_token": settings.VK_SERVICE_ACCESS_TOKEN,
        "v": "5.199"
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params)
        data = response.json()
        
        if 'response' in data and data['response']:
            object_id = data['response']['object_id']
            
            # Для групп добавляем минус
            if data['response']['type'] in ['group', 'page', 'event']:
                return f"-{object_id}"
            
            return str(object_id)
    
    raise ValueError(f"Cannot resolve screen_name: {external_id}")
```

---

## Примеры VK URLs и их ID

### Пользователи (USER)

| URL | Screen Name | Numeric ID |
|-----|-------------|------------|
| vk.com/id1 | - | 1 |
| vk.com/durov | durov | 1 |
| vk.com/s_shabalin | s_shabalin | 3619562 |
| vk.com/id3619562 | - | 3619562 |

### Группы/Паблики (GROUP/PUBLIC)

| URL | Screen Name | Numeric ID |
|-----|-------------|------------|
| vk.com/apiclub | apiclub | **-1** (с минусом!) |
| vk.com/club1 | - | **-1** |

**Важно:** Для групп ID отрицательный в API!

---

## Рекомендации

### При создании Source в админке:

1. **Лучший вариант:** Использовать numeric ID
   ```
   ✅ 3619562
   ✅ -1 (для групп)
   ```

2. **Можно:** Screen name (но нужен auto-resolve)
   ```
   ⚠️ s_shabalin (требует resolve)
   ⚠️ apiclub (требует resolve + минус для групп)
   ```

3. **Нельзя:** URL целиком
   ```
   ❌ https://vk.com/s_shabalin
   ❌ vk.com/s_shabalin
   ```

### Улучшение UX

Можно добавить в SourceAdmin форму кнопку "Resolve ID":

```python
# В форме создания Source
<input type="text" name="external_id" placeholder="Введите ID или screen_name">
<button type="button" onclick="resolveScreenName()">🔍 Найти ID</button>

<script>
async function resolveScreenName() {
    const input = document.querySelector('[name="external_id"]');
    const value = input.value;
    
    if (value.match(/^\d+$/)) {
        alert('Это уже numeric ID!');
        return;
    }
    
    const response = await fetch(`/api/vk/resolve?screen_name=${value}`);
    const data = await response.json();
    
    if (data.id) {
        input.value = data.id;
        alert(`Найден ID: ${data.id}`);
    }
}
</script>
```

---

## Итог

### Почему не работает screen_name?
- VK API не принимает screen_name в `owner_id`
- Нужен numeric ID

### Как найти numeric ID?
1. **Автоматически:** `utils.resolveScreenName` API
2. **Вручную:** Через фото профиля → URL фотографии
3. **Через код:** View Source → найти `oid` или `og:url`

### Что делать?
- Всегда сохранять **numeric ID** в `external_id`
- При необходимости добавить auto-resolve в VKClient
- Добавить helper в админку для поиска ID

---

## Хотите добавить auto-resolve?

Могу реализовать автоматическое определение, чтобы:
- При создании Source с screen_name - автоматически резолвить в numeric ID
- Сохранять в базу только numeric ID
- Показывать пользователю оба варианта (screen_name + ID)
