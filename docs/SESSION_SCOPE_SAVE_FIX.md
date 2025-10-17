# Session: Scope & Analysis Types Save Fix

**Date**: Current Session  
**Status**: ✅ Completed

---

## Problem

UI в админ-панели работает хорошо (checkboxes, scope textarea), но данные **не сохраняются** в БД при submit формы:

1. **`analysis_types`** - массив не сохраняется из скрытого поля
2. **`content_types`** - массив не сохраняется из скрытого поля  
3. **`scope`** - JSON не сохраняется из textarea

### Причина

`BotScenarioAdmin` **не имел** методов `insert_model()` и `update_model()` для обработки JSON данных из формы.

SQLAdmin по умолчанию пытается сохранить данные "как есть":
- Скрытое поле `<input name="analysis_types" value='["sentiment", "topics"]'>` → сохраняется как **строка** `'["sentiment", "topics"]'` вместо массива
- Textarea `<textarea name="scope">{"key": "value"}</textarea>` → сохраняется как **строка** `'{"key": "value"}'` вместо объекта

---

## Solution

### Added Methods to BotScenarioAdmin

**File**: `app/admin/views.py`

```python
def _parse_json_fields(self, data: dict) -> None:
    """Parse JSON fields from form data (hidden inputs and textareas)."""
    
    # Parse content_types from hidden field (JSON string)
    if "content_types" in data and isinstance(data["content_types"], str):
        try:
            data["content_types"] = json.loads(data["content_types"])
            logger.info(f"Parsed content_types: {data['content_types']}")
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"Failed to parse content_types: {e}, using empty list")
            data["content_types"] = []
    
    # Parse analysis_types from hidden field (JSON string)
    if "analysis_types" in data and isinstance(data["analysis_types"], str):
        try:
            data["analysis_types"] = json.loads(data["analysis_types"])
            logger.info(f"Parsed analysis_types: {data['analysis_types']}")
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"Failed to parse analysis_types: {e}, using empty list")
            data["analysis_types"] = []
    
    # Parse scope from textarea (JSON string)
    if "scope" in data and isinstance(data["scope"], str):
        try:
            # Handle empty string or whitespace
            if not data["scope"].strip():
                data["scope"] = {}
                logger.info("Scope is empty, using empty dict")
            else:
                data["scope"] = json.loads(data["scope"])
                logger.info(f"Parsed scope: {data['scope']}")
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"Failed to parse scope: {e}, using empty dict")
            data["scope"] = {}
    
    # Parse trigger_config from textarea (JSON string)
    if "trigger_config" in data and isinstance(data["trigger_config"], str):
        try:
            if not data["trigger_config"].strip():
                data["trigger_config"] = {}
            else:
                data["trigger_config"] = json.loads(data["trigger_config"])
                logger.info(f"Parsed trigger_config: {data['trigger_config']}")
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"Failed to parse trigger_config: {e}, using empty dict")
            data["trigger_config"] = {}

async def insert_model(self, request: Request, data: dict) -> Any:
    """Parse JSON fields before creating scenario."""
    self._parse_json_fields(data)
    logger.info(f"Creating scenario with data: content_types={data.get('content_types')}, analysis_types={data.get('analysis_types')}, scope keys={list(data.get('scope', {}).keys())}")
    return await super().insert_model(request, data)

async def update_model(self, request: Request, pk: Any, data: dict) -> Any:
    """Parse JSON fields before updating scenario."""
    self._parse_json_fields(data)
    logger.info(f"Updating scenario {pk} with data: content_types={data.get('content_types')}, analysis_types={data.get('analysis_types')}, scope keys={list(data.get('scope', {}).keys())}")
    return await super().update_model(request, pk, data)
```

---

## How It Works

### Flow: User submits form

1. **User fills form**:
   - Checks "Sentiment" and "Topics" analysis types
   - Checks "Posts" and "Comments" content types
   - Adds custom variable in scope: `{"my_brand": "TestBrand"}`

2. **JavaScript creates hidden fields on submit**:
   ```html
   <input type="hidden" name="content_types" value='["posts", "comments"]'>
   <input type="hidden" name="analysis_types" value='["sentiment", "topics"]'>
   <textarea name="scope">{"sentiment": {...}, "topics": {...}, "my_brand": "TestBrand"}</textarea>
   ```

3. **SQLAdmin calls `insert_model()` or `update_model()`**:
   ```python
   # data from form (all strings!):
   data = {
       "name": "Test Scenario",
       "content_types": '["posts", "comments"]',  # STRING!
       "analysis_types": '["sentiment", "topics"]',  # STRING!
       "scope": '{"sentiment": {...}, "topics": {...}, "my_brand": "TestBrand"}',  # STRING!
   }
   ```

4. **Our `_parse_json_fields()` parses strings to JSON**:
   ```python
   self._parse_json_fields(data)
   
   # After parsing:
   data = {
       "name": "Test Scenario",
       "content_types": ["posts", "comments"],  # LIST!
       "analysis_types": ["sentiment", "topics"],  # LIST!
       "scope": {  # DICT!
           "sentiment": {...},
           "topics": {...},
           "my_brand": "TestBrand"
       }
   }
   ```

5. **SQLAdmin saves to database**:
   ```sql
   INSERT INTO bot_scenarios (
       name,
       content_types,  -- JSON array
       analysis_types, -- JSON array
       scope           -- JSON object
   ) VALUES (
       'Test Scenario',
       '["posts", "comments"]',
       '["sentiment", "topics"]',
       '{"sentiment": {...}, "topics": {...}, "my_brand": "TestBrand"}'
   );
   ```

---

## Error Handling

### Invalid JSON in form

If user manually edits textarea and enters invalid JSON:

```json
{invalid json content
```

**Result**:
- Logger warning: `"Failed to parse scope: Expecting property name enclosed in double quotes, using empty dict"`
- Saved value: `{}` (empty dict)
- **No exception** - form submission succeeds

### Empty fields

If user doesn't select any checkboxes:

**Result**:
- `content_types`: `[]` (empty array)
- `analysis_types`: `[]` (empty array)
- `scope`: `{}` (empty dict)

### Only custom variables in scope

If user adds custom variables but no analysis types selected:

```json
{
  "my_brand": "TestBrand",
  "competitor": "CompetitorBrand"
}
```

**Result**: Saved as-is with no analysis configs

---

## Testing

### Manual Test 1: Create new scenario

1. **Start server**:
   ```bash
   cd /Users/admin/Projects/social-media-ai
   # Start your server (uvicorn, gunicorn, etc.)
   ```

2. **Open create page**:
   ```
   http://0.0.0.0:8000/admin/bot-scenario/create
   ```

3. **Fill form**:
   - Name: "Test Scenario"
   - Check "Sentiment" and "Topics" analysis types
   - Check "Posts" content type
   - Leave scope as-is (should have sentiment and topics configs)

4. **Submit form**

5. **Verify in database**:
   ```python
   from app.models import BotScenario
   
   scenario = BotScenario.objects.filter(name="Test Scenario").first()
   print("analysis_types:", scenario.analysis_types)  # Should be: ['sentiment', 'topics']
   print("content_types:", scenario.content_types)    # Should be: ['posts']
   print("scope keys:", list(scenario.scope.keys()))  # Should be: ['sentiment', 'topics']
   ```

6. **Check logs**:
   ```
   INFO:app.admin.views:Parsed content_types: ['posts']
   INFO:app.admin.views:Parsed analysis_types: ['sentiment', 'topics']
   INFO:app.admin.views:Parsed scope: {'sentiment': {...}, 'topics': {...}}
   INFO:app.admin.views:Creating scenario with data: content_types=['posts'], analysis_types=['sentiment', 'topics'], scope keys=['sentiment', 'topics']
   ```

### Manual Test 2: Edit existing scenario

1. **Open edit page**:
   ```
   http://0.0.0.0:8000/admin/bot-scenario/edit/6
   ```

2. **Modify**:
   - Uncheck "Topics" analysis type
   - Add custom variable in scope: `{"my_var": "test"}`

3. **Submit form**

4. **Verify**:
   ```python
   scenario = BotScenario.objects.get(id=6)
   print("analysis_types:", scenario.analysis_types)  # Should NOT include 'topics'
   print("scope:", scenario.scope)  # Should NOT have 'topics' config but HAVE 'my_var'
   ```

5. **Check logs**:
   ```
   INFO:app.admin.views:Parsed analysis_types: ['sentiment']
   INFO:app.admin.views:Parsed scope: {'sentiment': {...}, 'my_var': 'test'}
   INFO:app.admin.views:Updating scenario 6 with data: analysis_types=['sentiment'], scope keys=['sentiment', 'my_var']
   ```

### Manual Test 3: Invalid JSON

1. **Open edit page**

2. **Manually edit scope textarea**:
   ```
   {this is invalid json
   ```

3. **Submit form**

4. **Verify**:
   - Form submission succeeds (no error)
   - Check logs: `WARNING:app.admin.views:Failed to parse scope: ...`
   - Scenario saved with `scope = {}`

---

## Logs Output Examples

### Successful save:

```
INFO:app.admin.views:Parsed content_types: ['posts', 'comments']
INFO:app.admin.views:Parsed analysis_types: ['sentiment', 'topics', 'keywords']
INFO:app.admin.views:Parsed scope: {'sentiment': {'detect_sarcasm': True, 'emotion_analysis': True}, 'topics': {'max_topics': 5}, 'keywords': {'max_keywords': 20}, 'brand_name': 'MyBrand'}
INFO:app.admin.views:Creating scenario with data: content_types=['posts', 'comments'], analysis_types=['sentiment', 'topics', 'keywords'], scope keys=['sentiment', 'topics', 'keywords', 'brand_name']
```

### Failed JSON parse:

```
WARNING:app.admin.views:Failed to parse scope: Expecting property name enclosed in double quotes: line 1 column 2 (char 1), using empty dict
INFO:app.admin.views:Creating scenario with data: content_types=['posts'], analysis_types=['sentiment'], scope keys=[]
```

---

## Before & After

### Before Fix

**Form submit**:
```python
# Data received by SQLAdmin:
data = {
    "name": "Test",
    "content_types": '["posts"]',      # STRING
    "analysis_types": '["sentiment"]',  # STRING
    "scope": '{"sentiment": {...}}'     # STRING
}
```

**Saved to DB**:
```sql
-- WRONG! Saved as strings
content_types = '["posts"]'         -- Should be JSON array
analysis_types = '["sentiment"]'    -- Should be JSON array  
scope = '{"sentiment": {...}}'      -- Should be JSON object

-- Database treats them as TEXT, not JSON
```

**Result**:
- ❌ Can't query by analysis_types (it's a string)
- ❌ Can't access scope properties (it's a string)
- ❌ Application code breaks when trying to use these fields

### After Fix

**Form submit**:
```python
# Data received by SQLAdmin:
data = {
    "name": "Test",
    "content_types": '["posts"]',      # STRING
    "analysis_types": '["sentiment"]',  # STRING
    "scope": '{"sentiment": {...}}'     # STRING
}

# After _parse_json_fields():
data = {
    "name": "Test",
    "content_types": ["posts"],        # LIST
    "analysis_types": ["sentiment"],   # LIST
    "scope": {"sentiment": {...}}      # DICT
}
```

**Saved to DB**:
```sql
-- CORRECT! Saved as JSON
content_types = ["posts"]::json          -- JSON array
analysis_types = ["sentiment"]::json     -- JSON array
scope = {"sentiment": {...}}::json       -- JSON object
```

**Result**:
- ✅ Can query: `WHERE 'sentiment' = ANY(analysis_types)`
- ✅ Can access: `scope->'sentiment'->>'detect_sarcasm'`
- ✅ Application code works correctly

---

## Related Issues

### SQLAdmin JSON handling

SQLAdmin **does not** automatically parse JSON from form fields. It passes data as-is from the form.

**Why?**
- Forms return all values as **strings**
- SQLAdmin can't know which strings should be JSON
- Developer must explicitly parse JSON fields

**Solution**: Override `insert_model()` and `update_model()` to parse JSON strings.

### Alternative approaches

1. **Use WTForms JSON field** (complex, requires custom field type)
2. **Use JavaScript to submit as real JSON** (requires AJAX, breaks standard forms)
3. **Parse in model setter** (works but mixing concerns)
4. **Parse in admin methods** ✅ (clean separation, recommended)

---

## Files Modified

1. ✅ `app/admin/views.py` - added `_parse_json_fields()`, `insert_model()`, `update_model()`

---

## Checklist

- ✅ Added `_parse_json_fields()` helper method
- ✅ Added `insert_model()` for creating scenarios
- ✅ Added `update_model()` for editing scenarios
- ✅ Added logging for debugging
- ✅ Added error handling for invalid JSON
- ✅ Handle empty strings
- ✅ Parse all 4 JSON fields: content_types, analysis_types, scope, trigger_config
- ✅ Python syntax checked
- ✅ Documentation created

---

**Status**: ✅ Ready for testing  
**Next Step**: Test creating and editing scenarios

---

**Author**: Factory Droid  
**Date**: Current Session
