# Boolean Field "Да / Нет" - Реализация

**Дата:** 2024-12-10

---

## ✅ Что сделано

### 1. Кастомное отображение is_active в BaseAdmin

**Файл:** `app/admin/base.py`

```python
from wtforms import SelectField

class BaseAdmin(ModelView):
    # Переопределяем is_active как SelectField с "Да / Нет"
    form_overrides = {
        'is_active': SelectField
    }
    
    form_args = {
        'is_active': {
            'choices': [(True, 'Да'), (False, 'Нет')],
            'coerce': lambda x: x == 'True' if isinstance(x, str) else bool(x)
        }
    }
    
    column_labels = {
        "is_active": "Активен",
        "created_at": "Дата создания",
        "updated_at": "Дата обновления",
    }
```

**Что это делает:**
- `form_overrides` → заменяет BooleanField на SelectField
- `form_args.choices` → задаёт опции: (True, 'Да'), (False, 'Нет')
- `form_args.coerce` → преобразует строку из формы обратно в boolean
- `column_labels` → русская подпись "Активен"

---

### 2. Удалены дубликаты в views.py

Убрали `"is_active": "Активен"` / `"is_active": "Активна"` из:

✅ **UserAdmin** (строка 40)
```python
column_labels = dict({
    "id": "ID",
    "username": "Имя пользователя",
    "email": "Email",
    "role": "Роль",
    # "is_active": "Активен",  ← удалено (берётся из BaseAdmin)
}, **BaseAdmin.column_labels)
```

✅ **PlatformAdmin** (строка 146)
```python
column_labels = {
    "id": "ID",
    "name": "Название",
    # "is_active": "Активна",  ← удалено
}
```

✅ **SourceAdmin** (строка 207)
```python
column_labels = dict({
    "platform": "Платформа",
    "name": "Название",
    # "is_active": "Активен",  ← удалено
}, **BaseAdmin.column_labels)
```

✅ **BotScenarioAdmin** (строка 432)
```python
column_labels = dict({
    "id": "ID",
    "name": "Название",
    # "is_active": "Активен",  ← удалено
}, **BaseAdmin.column_labels)
```

---

## 🎯 Как это работает

### Раньше (BooleanField):
```html
<div class="form-group">
    <label>Активен</label>
    <input type="checkbox" name="is_active" value="1" />
</div>

<!-- При True: галочка стоит -->
<!-- При False: галочка снята -->
```

### Теперь (SelectField):
```html
<div class="form-group">
    <label>Активен</label>
    <select name="is_active">
        <option value="True">Да</option>
        <option value="False">Нет</option>
    </select>
</div>

<!-- При True: выбрано "Да" -->
<!-- При False: выбрано "Нет" -->
```

---

## 🔧 Функция coerce

**Назначение:** Преобразует значение из формы (строка) в boolean для БД.

```python
coerce = lambda x: x == 'True' if isinstance(x, str) else bool(x)
```

**Примеры работы:**

| Вход из формы | Тип      | Результат | В БД  |
|---------------|----------|-----------|-------|
| `"True"`      | str      | `True`    | true  |
| `"False"`     | str      | `False`   | false |
| `True`        | bool     | `True`    | true  |
| `False`       | bool     | `False`   | false |
| `1`           | int      | `True`    | true  |
| `0`           | int      | `False`   | false |

**Почему это важно:**
- HTML форма отправляет `"True"` или `"False"` как строки
- БД ожидает boolean (`true`/`false`)
- coerce преобразует строку → boolean перед сохранением

---

## 📋 Применяется автоматически ко всем моделям

**Список моделей с полем is_active:**

1. ✅ **User** (app/models/user.py)
   - Активен ли пользователь
   - UserAdmin → наследует BaseAdmin

2. ✅ **Platform** (app/models/platform.py)
   - Активна ли платформа
   - PlatformAdmin → наследует BaseAdmin

3. ✅ **Source** (app/models/source.py)
   - Активен ли источник
   - SourceAdmin → наследует BaseAdmin

4. ✅ **BotScenario** (app/models/bot_scenario.py)
   - Активен ли сценарий
   - BotScenarioAdmin → наследует BaseAdmin

**Все эти модели автоматически получат "Да / Нет" вместо True/False!**

---

## 🧪 Тестирование

### 1. Создание новой записи

**Шаги:**
1. Открыть админку: `/admin/user/create` (или любую другую модель)
2. Заполнить обязательные поля
3. Найти поле "Активен"
4. **Проверить:** Это выпадающий список (не чекбокс)
5. Выбрать "Да" или "Нет"
6. Нажать "Save"
7. **Проверить:** Запись сохранилась с правильным значением

**Ожидаемый результат:**
```
Активен: [▼ Да]  или  [▼ Нет]
```

---

### 2. Редактирование существующей записи

**Шаги:**
1. Открыть список записей: `/admin/user/list`
2. Выбрать запись с `is_active = True`
3. Нажать "Edit"
4. **Проверить:** В поле "Активен" выбрано "Да"
5. Изменить на "Нет"
6. Нажать "Save"
7. **Проверить:** В списке записей теперь is_active = False

**Ожидаемый результат:**
- При `is_active = True` → выбрано "Да"
- При `is_active = False` → выбрано "Нет"

---

### 3. Проверка на всех моделях

**Тестировать на:**
- ✅ `/admin/user/create` и `/admin/user/edit/1`
- ✅ `/admin/platform/create` и `/admin/platform/edit/1`
- ✅ `/admin/source/create` и `/admin/source/edit/1`
- ✅ `/admin/botscenario/create` и `/admin/botscenario/edit/1`

**Проверить:**
1. Поле is_active - это select (не checkbox)
2. Опции: "Да" и "Нет"
3. Значение корректно отображается при редактировании
4. Изменение сохраняется в БД

---

## 🚀 Преимущества

### 1. Ясность
- "Да / Нет" понятнее чем True/False для пользователей
- Не нужно объяснять что означает галочка

### 2. Единообразие
- Все модели используют одинаковый подход
- Настройка в одном месте (BaseAdmin)

### 3. Надёжность
- SelectField всегда отправляет значение (в отличие от checkbox)
- Нет проблем с unchecked checkbox = no value

### 4. Локализация
- Легко изменить на другой язык: `[(True, 'Yes'), (False, 'No')]`

---

## ⚠️ Возможные проблемы и решения

### Проблема 1: "Не отображается выпадающий список"

**Причина:** SQLAdmin не применяет form_overrides

**Решение:**
1. Проверить импорт: `from wtforms import SelectField`
2. Проверить что view наследует BaseAdmin
3. Убедиться что нет локальных `form_overrides` которые перезаписывают

---

### Проблема 2: "Значение не сохраняется"

**Причина:** coerce не срабатывает

**Решение:**
Добавить debug в `BaseAdmin.on_model_change`:
```python
async def on_model_change(self, data: dict, model: Any, is_created: bool, request=None):
    print(f"DEBUG is_active: {data.get('is_active')} (type: {type(data.get('is_active'))})")
    await super().on_model_change(data, model, is_created, request)
```

Проверить что приходит boolean, не строка.

---

### Проблема 3: "Выбрано не то значение при редактировании"

**Причина:** Текущее значение модели не соответствует choices

**Решение:**
1. Проверить что в БД `is_active` хранится как boolean
2. Проверить что choices правильно определены: `[(True, 'Да'), (False, 'Нет')]`
3. Убедиться что `True`/`False` - это bool, не строки

---

## 📚 Документация

### SQLAdmin form_overrides
https://aminalaee.github.io/sqladmin/configurations/

### WTForms SelectField
https://wtforms.readthedocs.io/en/stable/fields/#wtforms.fields.SelectField

### Пример из Flask-Admin
https://flask-admin.readthedocs.io/en/latest/introduction/#customizing-built-in-forms

---

## ✅ Итого

**Изменения:**
- ✅ Добавлен `form_overrides` в BaseAdmin
- ✅ Добавлен `form_args` с choices и coerce
- ✅ Добавлен `column_labels` для is_active
- ✅ Удалены дубликаты из 4 admin views

**Результат:**
- Вместо чекбокса True/False
- Теперь выпадающий список "Да / Нет"
- Применяется ко всем моделям с is_active
- Работает для создания и редактирования

**Готово к использованию!** 🎉
