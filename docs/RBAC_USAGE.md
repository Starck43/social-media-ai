# RBAC (Role-Based Access Control) Usage Guide

## Архитектура

### Модели

**Role** - Роли пользователей
- `name` - название роли (уникальное)
- `codename` - код роли (UserRoleType enum)
- `description` - описание
- Связь many-to-many с `Permission`
- Связь one-to-many с `User`

**Permission** - Разрешения для действий над моделями
- `codename` - уникальный код (формат: `app.model.action`)
- `name` - человекочитаемое название
- `action_type` - тип действия (ActionType enum)
- `model_type_id` - ссылка на ModelType
- Связь many-to-many с `Role`

**ModelType** - Типы моделей для управления правами
- `app_name` - название приложения
- `model_name` - название модели
- `table_name` - название таблицы
- `is_managed` - управляется ли через RBAC
- Связь one-to-many с `Permission`

### Enum типы

**UserRoleType** (app/types/models.py)
```python
class UserRoleType(Enum):
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    MODERATOR = "moderator"
    USER = "user"
    GUEST = "guest"
```

**ActionType** (app/types/models.py)
```python
class ActionType(Enum):
    VIEW = "view"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    EXECUTE = "execute"
```

## RoleManager - Управление ролями

### Основные методы

#### 1. Получение роли

```python
from app.models import Role
from app.types.models import UserRoleType

# По имени
role = await Role.objects.get_by_name("admin")

# По codename
role = await Role.objects.get_by_codename(UserRoleType.ADMIN)

# С загруженными permissions
role = await Role.objects.get_with_permissions(role_id=1)

# С загруженными users
role = await Role.objects.get_with_users(role_id=1)
```

#### 2. Создание роли

```python
from app.types.models import UserRoleType

role = await Role.objects.create_role(
	name="content_manager",
	codename=UserRoleType.MODERATOR,
	description="Can manage content and moderate users"
)
```

#### 3. Управление правами роли

```python
# Добавить право
role = await Role.objects.add_permission(
    role_id=1,
    permission_id=5
)

# Удалить право
role = await Role.objects.remove_permission(
    role_id=1,
    permission_id=5
)

# Проверить наличие права
has_perm = await Role.objects.has_permission(
    role_id=1,
    permission_codename="social.source.view"
)

# Получить все права роли
permissions = await Role.objects.get_permissions_for_role(
    role_id=1,
    action_type=ActionType.VIEW  # Optional filter
)
```

#### 4. Поиск ролей по правам

```python
# Получить все роли с определенным правом
roles = await Role.objects.get_roles_with_permission(
    permission_codename="social.source.delete"
)
```

#### 5. Статистика

```python
stats = await Role.objects.get_stats()
# Returns:
# {
#     'total': 5,
#     'by_codename': {
#         'admin': {'name': 'admin', 'description': '...'},
#         'user': {'name': 'user', 'description': '...'},
#         ...
#     }
# }
```

## PermissionManager - Управление правами

### Основные методы

#### 1. Получение прав

```python
from app.models import Permission
from app.types.models import ActionType

# По codename
perm = await Permission.objects.get_by_codename("social.source.view")

# По типу действия
perms = await Permission.objects.get_by_action_type(ActionType.DELETE)

# Для конкретной модели
perms = await Permission.objects.get_for_model(
    app_name="social",
    model_name="source",
    action_type=ActionType.VIEW  # Optional
)

# Для ModelType ID
perms = await Permission.objects.get_for_model_type(
    model_type_id=1,
    action_type=ActionType.CREATE  # Optional
)

# С загруженным model_type
perm = await Permission.objects.get_with_model_type(permission_id=5)
```

#### 2. Создание прав

```python
from app.types.models import ActionType

# Создать одно право
perm = await Permission.objects.create_permission(
    codename="social.source.view",
    name="Can view source",
    action_type=ActionType.VIEW,
    model_type_id=1
)

# Создать права для всех действий над моделью
perms = await Permission.objects.bulk_create_for_model(
    app_name="social",
    model_name="source",
    model_type_id=1,
    action_types=[
        ActionType.VIEW,
        ActionType.CREATE,
        ActionType.UPDATE,
        ActionType.DELETE
    ]
)
```

#### 3. Поиск прав

```python
# Поиск по codename или name
perms = await Permission.objects.search_permissions(
    query="source",
    action_type=ActionType.VIEW  # Optional
)
```

#### 4. Статистика

```python
stats = await Permission.objects.get_stats()
# Returns:
# {
#     'total': 20,
#     'by_action': {
#         'view': 5,
#         'create': 5,
#         'update': 5,
#         'delete': 5
#     },
#     'by_app': {
#         'social': 12,
#         'auth': 8
#     }
# }
```

## Формат codename

Права имеют строгий формат: `app.model.action`

**Примеры валидных codename:**
- `social.source.view`
- `social.user.create`
- `auth.role.update`
- `monitoring.analytics.delete`

**Валидация:**
```python
from app.models import Permission

# Проверить формат
Permission.validate_codename("social.source.view")  # OK
Permission.validate_codename("invalid-format")      # ValueError

# Разделить codename
app_model, action = Permission.split_codename("social.source.view")
# Returns: ("social.source", "view")
```

## Использование в API

### Пример endpoint для назначения прав роли

```python
from fastapi import APIRouter, Depends, HTTPException
from app.models import Role, Permission

router = APIRouter(prefix="/roles", tags=["roles"])

@router.post("/{role_id}/permissions/{permission_id}")
async def assign_permission(role_id: int, permission_id: int):
    """Assign permission to role"""
    role = await Role.objects.add_permission(role_id, permission_id)
    
    if not role:
        raise HTTPException(404, "Role or Permission not found")
    
    return {"message": "Permission assigned", "role_id": role.id}

@router.delete("/{role_id}/permissions/{permission_id}")
async def revoke_permission(role_id: int, permission_id: int):
    """Revoke permission from role"""
    role = await Role.objects.remove_permission(role_id, permission_id)
    
    if not role:
        raise HTTPException(404, "Role or Permission not found")
    
    return {"message": "Permission revoked", "role_id": role.id}
```

### Проверка прав пользователя

```python
from app.models import User, Role

async def check_user_permission(user_id: int, permission_codename: str) -> bool:
    """Check if user has specific permission through their role"""
    user = await User.objects.get(id=user_id)
    if not user or not user.role_id:
        return False
    
    return await Role.objects.has_permission(
        role_id=user.role_id,
        permission_codename=permission_codename
    )

# Usage
has_access = await check_user_permission(1, "social.source.delete")
if not has_access:
    raise HTTPException(403, "Insufficient permissions")
```

## Использование в админке

### Scaffold form для выбора permissions

```python
from sqladmin import ModelView
from app.models import Role, Permission

class RoleAdmin(ModelView, model=Role):
    async def scaffold_form(self):
        form = await super().scaffold_form()
        
        # Get all permissions for multi-select
        permissions = await Permission.objects.filter()
        
        form.permissions.choices = [
            (p.id, f"{p.codename} - {p.name}")
            for p in permissions
        ]
        
        return form
```

## Примеры полного workflow

### 1. Создание роли с правами

```python
from app.models import Role, Permission, ModelType
from app.types.models import UserRoleType, ActionType

# 1. Создать ModelType для Source
model_type = await ModelType.objects.create(
	app_name="social",
	model_name="source",
	table_name="sources",
	is_managed=True
)

# 2. Создать права для всех действий
permissions = await Permission.objects.bulk_create_for_model(
	app_name="social",
	model_name="source",
	model_type_id=model_type.id,
	action_types=[
		ActionType.VIEW,
		ActionType.CREATE,
		ActionType.UPDATE,
		ActionType.DELETE
	]
)

# 3. Создать роль
role = await Role.objects.create_role(
	name="source_manager",
	codename=UserRoleType.MODERATOR,
	description="Can fully manage sources"
)

# 4. Назначить все права роли
for perm in permissions:
	await Role.objects.add_permission(role.id, perm.id)
```

### 2. Проверка и управление правами

```python
# Получить роль с правами
role = await Role.objects.get_with_permissions(role_id=1)

# Проверить права
has_view = await Role.objects.has_permission(
    role_id=role.id,
    permission_codename="social.source.view"
)

has_delete = await Role.objects.has_permission(
    role_id=role.id,
    permission_codename="social.source.delete"
)

# Получить права по типу действия
view_perms = await Role.objects.get_permissions_for_role(
    role_id=role.id,
    action_type=ActionType.VIEW
)

# Найти все роли с правом на удаление
roles_can_delete = await Role.objects.get_roles_with_permission(
    permission_codename="social.source.delete"
)
```

### 3. Поиск и статистика

```python
# Поиск прав
source_perms = await Permission.objects.search_permissions(
    query="source",
    action_type=ActionType.VIEW
)

# Статистика прав
perm_stats = await Permission.objects.get_stats()
print(f"Total permissions: {perm_stats['total']}")
print(f"By action: {perm_stats['by_action']}")

# Статистика ролей
role_stats = await Role.objects.get_stats()
print(f"Total roles: {role_stats['total']}")
```

## Best Practices

### 1. Всегда используйте валидацию codename

```python
# BAD
perm = await Permission.objects.create(
    codename="invalid_format",  # Не пройдет валидацию
    ...
)

# GOOD
perm = await Permission.objects.create_permission(
    codename="social.source.view",  # Автоматическая валидация
    ...
)
```

### 2. Используйте bulk_create для массового создания

```python
# BAD - медленно
for action in [ActionType.VIEW, ActionType.CREATE, ...]:
    await Permission.objects.create_permission(...)

# GOOD - быстро
perms = await Permission.objects.bulk_create_for_model(
    app_name="social",
    model_name="source",
    model_type_id=1,
    action_types=[ActionType.VIEW, ActionType.CREATE, ...]
)
```

### 3. Используйте prefetch для связей

```python
# BAD - N+1 queries
role = await Role.objects.get(id=1)
for perm in role.permissions:  # Lazy loading
    print(perm.name)

# GOOD - 1 query
role = await Role.objects.get_with_permissions(role_id=1)
for perm in role.permissions:  # Already loaded
    print(perm.name)
```

### 4. Кэшируйте проверки прав

```python
from functools import lru_cache
from typing import Optional

@lru_cache(maxsize=128)
async def cached_permission_check(role_id: int, perm_codename: str) -> bool:
    return await Role.objects.has_permission(role_id, perm_codename)
```

## Ошибки и их решения

### ValueError: Invalid codename format

```python
# Проблема: неверный формат codename
perm = await Permission.objects.create_permission(
    codename="invalid-format",  # ❌ Должно быть app.model.action
    ...
)

# Решение
perm = await Permission.objects.create_permission(
    codename="social.source.view",  # ✅
    ...
)
```

### ValueError: Permission already exists

```python
# Проблема: право уже создано
perm = await Permission.objects.create_permission(
    codename="social.source.view",  # ❌ Уже существует
    ...
)

# Решение 1: проверить существование
existing = await Permission.objects.get_by_codename("social.source.view")
if not existing:
    perm = await Permission.objects.create_permission(...)

# Решение 2: использовать bulk_create (автоматически пропускает существующие)
perms = await Permission.objects.bulk_create_for_model(...)
```

### ValueError: Role already exists

```python
# Проблема: роль с таким именем уже создана
role = await Role.objects.create_role(
    name="admin",  # ❌ Уже существует
    ...
)

# Решение
existing = await Role.objects.get_by_name("admin")
if not existing:
    role = await Role.objects.create_role(...)
```

## Дополнительные ресурсы

- [Модель Role](../app/models/role.py)
- [Модель Permission](../app/models/permission.py)
- [Модель ModelType](../app/models/model_type.py)
- [RoleManager](../app/models/managers/role_manager.py)
- [PermissionManager](../app/models/managers/permission_manager.py)
- [Enum типы](../app/types/models.py)
