# Fix: Enum Select Fields Not Showing Correct Values

**Date**: Current Session  
**Status**: ✅ Fixed

---

## Problem

Enum fields `trigger_type` and `action_type` were saved to database but **not displayed correctly** in select dropdowns.

**Example**:
- Saved in DB: `KEYWORD_MATCH` (enum NAME)
- Select dropdown showed: Empty or wrong value
- Displayed in table: `KEYWORD_MATCH` (correct)

---

## Root Cause

**Mismatch between storage format and form choices**:

### In Model (BotScenario)
```python
trigger_type: Mapped[BotTriggerType] = BotTriggerType.sa_column(
    type_name='bot_trigger_type',
    nullable=True,
    store_as_name=True  # ← Stores as NAME: "KEYWORD_MATCH"
)

action_type: Mapped[BotActionType] = BotActionType.sa_column(
    type_name='bot_action_type',
    nullable=True,
    store_as_name=True  # ← Stores as NAME: "COMMENT"
)
```

### In Admin Form (OLD - wrong)
```python
'action_type': {
    'choices': BotActionType.choices(),  # ← Default: use_db_value=True
    # Returns: [("comment", "💬 Комментарий"), ...]
    # But DB has: "COMMENT" (NAME, not db_value!)
},
'trigger_type': {
    'choices': BotTriggerType.choices(),  # ← Default: use_db_value=True
    # Returns: [("keyword_match", "🔑 Совпадение..."), ...]
    # But DB has: "KEYWORD_MATCH" (NAME, not db_value!)
}
```

**Result**: Form looks for "keyword_match" in options, but DB has "KEYWORD_MATCH" → no match → empty select!

---

## Solution

Use `choices(use_db_value=False)` to get NAME instead of db_value:

```python
# app/admin/views.py - BotScenarioAdmin.form_args

'action_type': {
    # use_db_value=False because model uses store_as_name=True
    'choices': [('', '— Не выбрано —')] + BotActionType.choices(use_db_value=False),
    'coerce': lambda x: x if x else None,
    'validators': []
},
'trigger_type': {
    # use_db_value=False because model uses store_as_name=True
    'choices': [('', '— Не выбрано —')] + BotTriggerType.choices(use_db_value=False),
    'coerce': lambda x: x if x else None,
    'validators': []
},
```

---

## How Enum Choices Work

### Method Signature
```python
@classmethod
def choices(cls, use_db_value: bool = False):
    """
    Get choices for form fields.
    
    Args:
        use_db_value: If True, use db_value; if False, use enum name (for store_as_name=True)
    
    Returns:
        List of (value, label) tuples for SelectField
    """
    if use_db_value:
        return [(action.db_value, action.label) for action in cls]
    return [(action.name, action.label) for action in cls]
```

### Example: BotActionType.COMMENT

```python
BotActionType.COMMENT.name      # "COMMENT"
BotActionType.COMMENT.db_value  # "comment"
BotActionType.COMMENT.label     # "💬 Комментарий"
```

### With use_db_value=True (OLD - wrong for store_as_name=True)
```python
BotActionType.choices(use_db_value=True)
# Returns:
[
    ("comment", "💬 Комментарий"),
    ("reply", "↩️ Ответ"),
    ("dm", "✉️ Личное сообщение"),
    ...
]
```

**Problem**: DB has "COMMENT" but form looks for "comment" → mismatch!

### With use_db_value=False (NEW - correct for store_as_name=True)
```python
BotActionType.choices(use_db_value=False)
# Returns:
[
    ("COMMENT", "💬 Комментарий"),
    ("REPLY", "↩️ Ответ"),
    ("DIRECT_MESSAGE", "✉️ Личное сообщение"),
    ...
]
```

**Solution**: DB has "COMMENT" and form looks for "COMMENT" → match! ✅

---

## Verification

### Test Commands

```python
from app.types import BotActionType, BotTriggerType

# Check what DB stores
print("DB stores NAME:", BotActionType.COMMENT.name)  # "COMMENT"

# Check what form expects (OLD)
choices_old = BotActionType.choices(use_db_value=True)
print("Form expects (OLD):", choices_old[0][0])  # "comment" ❌

# Check what form expects (NEW)
choices_new = BotActionType.choices(use_db_value=False)
print("Form expects (NEW):", choices_new[0][0])  # "COMMENT" ✅
```

### Output
```
=== BotActionType ===
With use_db_value=True (old, wrong):
  'comment': 💬 Комментарий
  'reply': ↩️ Ответ
  'dm': ✉️ Личное сообщение

With use_db_value=False (new, correct):
  'COMMENT': 💬 Комментарий
  'REPLY': ↩️ Ответ
  'DIRECT_MESSAGE': ✉️ Личное сообщение

=== Database stores as NAME ===
Example: BotActionType.COMMENT.name = COMMENT
Example: BotActionType.COMMENT.db_value = comment
```

---

## Testing

### 1. Edit Existing Scenario

1. Open: `http://0.0.0.0:8000/admin/bot-scenario/edit/6`
2. Check trigger_type select dropdown
3. **Expected**: Current value selected (e.g., "🔑 Совпадение ключевых слов")
4. **Before fix**: Empty or no selection
5. **After fix**: Correct value selected ✅

### 2. Create New Scenario

1. Open: `http://0.0.0.0:8000/admin/bot-scenario/create`
2. Select trigger_type: "🔑 Совпадение ключевых слов"
3. Select action_type: "💬 Комментарий"
4. Save
5. Reload page
6. **Expected**: Both dropdowns show correct selected values ✅

### 3. Check Database

```python
from app.models import BotScenario

s = BotScenario.objects.get(id=6)
print("trigger_type stored as:", s.trigger_type.name if s.trigger_type else None)
print("action_type stored as:", s.action_type.name if s.action_type else None)

# Should output:
# trigger_type stored as: KEYWORD_MATCH
# action_type stored as: COMMENT
```

---

## Related Enums

This fix applies to all enums using `store_as_name=True`:

✅ **Fixed**:
- `BotActionType` 
- `BotTriggerType`

✅ **Already correct** (don't use store_as_name=True or use db_value):
- `LLMStrategyType`
- `AnalysisType`
- `ContentType`

---

## Files Modified

1. ✅ `app/admin/views.py`:
   - Updated `form_args` for `action_type`
   - Updated `form_args` for `trigger_type`
   - Added comments explaining `use_db_value=False`

---

## Before & After

### Before Fix

**Form**:
```html
<select name="action_type">
  <option value="">— Не выбрано —</option>
  <option value="comment">💬 Комментарий</option>  ← Looking for "comment"
  <option value="reply">↩️ Ответ</option>
  ...
</select>
```

**DB has**: `"COMMENT"` (NAME)

**Result**: No match → empty select ❌

### After Fix

**Form**:
```html
<select name="action_type">
  <option value="">— Не выбрано —</option>
  <option value="COMMENT">💬 Комментарий</option>  ← Looking for "COMMENT"
  <option value="REPLY">↩️ Ответ</option>
  ...
</select>
```

**DB has**: `"COMMENT"` (NAME)

**Result**: Match! → correct value selected ✅

---

## Key Takeaway

**Rule**: When model uses `store_as_name=True`, form must use `choices(use_db_value=False)`

```python
# In Model
field: Mapped[EnumType] = EnumType.sa_column(
    store_as_name=True  # ← Stores NAME
)

# In Admin
form_args = {
    'field': {
        'choices': EnumType.choices(use_db_value=False)  # ← Use NAME
    }
}
```

---

**Status**: ✅ Fixed - Ready to Test  
**Author**: Factory Droid  
**Date**: Current Session
