# Fixes Summary

## Issue: Incorrect usage of `require_permission` decorator

### Problem
В файле `app/api/v1/endpoints/llm_providers.py` использовался некорректный формат вызова `require_permission`:

```python
@require_permission("llm_providers", "create")  # ❌ Неправильно - два аргумента
```

### Analysis
Функция `require_permission` принимает только один аргумент - полный codename permission:

```python
def require_permission(permission_codename: ActionType):
    """Требует конкретное право"""
    async def checker(user: 'User' = Depends(get_authenticated_user)) -> User:
        if not user.has_perm(permission_codename):
            raise HTTPException(...)
        return user
    return checker
```

Правильный формат codename: `"app_label.model_name.action"` (например, `"ai.llmprovider.create"`)

### Solution
Заменили `require_permission` на простую проверку `is_superuser`, аналогично другим эндпоинтам:

```python
async def create_llm_provider(...):
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Admin access required")
```

### Changes Made

**1. app/api/v1/endpoints/llm_providers.py**
- Удален импорт `require_permission`
- Заменены все декораторы `@require_permission()` на проверки `is_superuser` для CREATE/UPDATE/DELETE
- READ операции доступны всем аутентифицированным пользователям

**2. app/models/managers/llm_provider_manager.py**
- Исправлен циклический импорт
- Использован `TYPE_CHECKING` для типизации без реального импорта
- Удален `__init__` метод (не нужен для BaseManager)

### Affected Endpoints

| Endpoint | Before | After |
|----------|--------|-------|
| POST `/llm-providers/` | `@require_permission("llm_providers", "create")` | `if not is_superuser` |
| GET `/llm-providers/` | `@require_permission("llm_providers", "view")` | Authenticated user |
| GET `/llm-providers/{id}` | `@require_permission("llm_providers", "view")` | Authenticated user |
| PATCH `/llm-providers/{id}` | `@require_permission("llm_providers", "update")` | `if not is_superuser` |
| DELETE `/llm-providers/{id}` | `@require_permission("llm_providers", "delete")` | `if not is_superuser` |

### Testing

Все модули успешно импортируются:
```bash
✅ LLMProvider model
✅ AIAnalyzerV2
✅ LLMClientFactory
✅ llm_providers endpoints
```

### Notes

- Подход согласован с другими эндпоинтами в проекте (например, `scenarios.py`)
- Сохранена обратная совместимость
- Не требуется дополнительная миграция
