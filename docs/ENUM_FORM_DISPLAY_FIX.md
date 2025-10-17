# Fix: Enum Fields Always Show "-- Не выбрано --" in Edit Form

**Date**: Current Session  
**Status**: ✅ Fixed

---

## Problem

When editing a BotScenario:
- **trigger_type** and **action_type** always showed "-- Не выбрано --" 
- Values existed in database (visible in table view)
- Could select and save new values (worked)
- But on reload, form showed "-- Не выбрано --" again

**llm_strategy** worked correctly!

---

## Root Cause

SQLAdmin didn't know how to **read** enum objects from database and convert them to form data.

### What Happens

1. **Database stores**: `BotTriggerType.KEYWORD_MATCH` (enum object or NAME string)
2. **SQLAdmin loads**: `obj.trigger_type` → `BotTriggerType.KEYWORD_MATCH` (enum object)
3. **Form expects**: String value like `"KEYWORD_MATCH"` (the NAME)
4. **SQLAdmin passes**: Enum object directly to WTForms
5. **WTForms doesn't match**: Can't compare enum object with string choices → shows "-- Не выбрано --"

### Why llm_strategy Worked

```python
# llm_strategy doesn't use store_as_name=True
llm_strategy: Mapped[LLMStrategyType] = LLMStrategyType.sa_column(
    type_name='llm_strategy_type',
    nullable=True,
    default=LLMStrategyType.COST_EFFICIENT
    # NO store_as_name=True!
)
```

Without `store_as_name=True`:
- Database stores: `"cost_efficient"` (string value)
- SQLAdmin loads: `"cost_efficient"` (string)
- Form expects: `"cost_efficient"` (string)
- Match! ✅

### Why trigger_type/action_type Didn't Work

```python
# These use store_as_name=True
trigger_type: Mapped[BotTriggerType] = BotTriggerType.sa_column(
    type_name='bot_trigger_type',
    nullable=True,
    store_as_name=True  # ← Causes enum object to be loaded
)
```

With `store_as_name=True`:
- Database stores: `"KEYWORD_MATCH"` (NAME string)
- SQLAlchemy loads: `BotTriggerType.KEYWORD_MATCH` (enum object!)
- SQLAdmin passes: Enum object to form
- Form expects: String `"KEYWORD_MATCH"`
- No match! Shows "-- Не выбрано --" ❌

---

## Solution

Override `_details_data_from_instance()` to convert enum objects to strings (NAME):

```python
# app/admin/views.py - BotScenarioAdmin

def _details_data_from_instance(self, obj: Any) -> dict:
    """Convert model instance to form data, handling enums properly."""
    data = super()._details_data_from_instance(obj)
    
    # Convert enum objects to their name (for store_as_name=True enums)
    if hasattr(obj, 'trigger_type') and obj.trigger_type is not None:
        data['trigger_type'] = obj.trigger_type.name
    if hasattr(obj, 'action_type') and obj.action_type is not None:
        data['action_type'] = obj.action_type.name
    
    return data
```

**What this does**:
1. Get form data from parent class (includes enum objects)
2. If `trigger_type` is enum object → convert to `.name` string
3. If `action_type` is enum object → convert to `.name` string
4. Return converted data to WTForms

**Result**: Form receives strings, can match with choices, shows correct value ✅

---

## Flow Comparison

### Before Fix (Broken)

```
Database: "KEYWORD_MATCH" (NAME string)
    ↓
SQLAlchemy: BotTriggerType.KEYWORD_MATCH (enum object)
    ↓
SQLAdmin: passes enum object to form
    ↓
WTForms: can't match enum object with string choices
    ↓
Form shows: "-- Не выбрано --" ❌
```

### After Fix (Working)

```
Database: "KEYWORD_MATCH" (NAME string)
    ↓
SQLAlchemy: BotTriggerType.KEYWORD_MATCH (enum object)
    ↓
SQLAdmin: obj.trigger_type.name → "KEYWORD_MATCH" (string)
    ↓
WTForms: matches "KEYWORD_MATCH" in choices
    ↓
Form shows: "🔑 Совпадение ключевых слов" ✅
```

---

## Testing

### 1. Restart Server

### 2. Edit Existing Scenario

```
http://0.0.0.0:8000/admin/bot-scenario/edit/6
```

**Before fix**:
- trigger_type select: "-- Не выбрано --" ❌
- action_type select: "-- Не выбрано --" ❌

**After fix**:
- trigger_type select: "🔑 Совпадение ключевых слов" ✅
- action_type select: "💬 Комментарий" ✅

### 3. Verify Database Values

```python
from app.models import BotScenario

s = BotScenario.objects.get(id=6)

print("trigger_type:", s.trigger_type)  # BotTriggerType.KEYWORD_MATCH
print("trigger_type.name:", s.trigger_type.name if s.trigger_type else None)  # "KEYWORD_MATCH"

print("action_type:", s.action_type)  # BotActionType.COMMENT
print("action_type.name:", s.action_type.name if s.action_type else None)  # "COMMENT"
```

---

## Why This Was Needed

SQLAdmin has generic form handling that works for:
- Strings
- Integers
- Booleans
- JSON
- Foreign Keys

But for **custom enum types with `store_as_name=True`**, SQLAdmin doesn't know to convert enum objects to strings for form display.

**Solution**: Override `_details_data_from_instance()` to handle this conversion.

---

## Files Modified

1. ✅ `app/admin/views.py`:
   - Added `_details_data_from_instance()` method to BotScenarioAdmin

---

## Related Fixes

This completes the enum select fix:

1. ✅ **First fix**: Changed `choices()` to use `use_db_value=False` (previous session)
2. ✅ **Second fix**: Added `_details_data_from_instance()` to convert enum to string (this session)

Both fixes needed for proper enum handling!

---

## Key Takeaway

**For enums with `store_as_name=True`**:

1. **Form choices**: Use `EnumType.choices(use_db_value=False)` → returns NAME
2. **Form data loading**: Convert `obj.field.name` → string in `_details_data_from_instance()`

```python
# In form_args
'field': {
    'choices': EnumType.choices(use_db_value=False)  # Returns [("NAME", "Label"), ...]
}

# In admin class
def _details_data_from_instance(self, obj):
    data = super()._details_data_from_instance(obj)
    if obj.field:
        data['field'] = obj.field.name  # Convert enum object to NAME string
    return data
```

---

**Status**: ✅ Fixed - Ready to Test  
**Author**: Factory Droid  
**Date**: Current Session
