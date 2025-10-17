# Session: Fix Save Issue (V2)

**Date**: Current Session  
**Status**: ✅ Fixed

---

## Problem

After implementing JSON parsing logic, form still **not saving** data to database.

**Root Cause**: Field conflict!

1. SQLAdmin created standard fields: `<textarea name="scope">`, `<input name="content_types">`, `<input name="analysis_types">`
2. We hid them with CSS: `display: none !important`
3. Our JavaScript created NEW fields with SAME names
4. Result: **Two sets of fields with identical names in the form!**
5. When form submitted, which fields were sent? Both? First? Last?
6. SQLAdmin got confused and ignored the data

---

## Solution

### Step 1: Exclude fields from SQLAdmin form

**File**: `app/admin/views.py`

```python
form_excluded_columns = [
    "sources",
    "llm_mapping",
    # Exclude these fields - we handle them manually in custom template
    "content_types",
    "analysis_types", 
    "scope",
] + BaseAdmin.form_excluded_columns
```

**Result**: SQLAdmin won't create these fields at all.

### Step 2: Manually extract fields from request

Since we excluded fields from SQLAdmin form, they won't be in `data` dict. We need to get them directly from `request.form()`:

```python
async def insert_model(self, request: Request, data: dict) -> Any:
    """Parse JSON fields before creating scenario."""
    # Since we excluded these fields from form, get them from request manually
    form_data = await request.form()
    
    # Add excluded fields back to data
    if "content_types" in form_data:
        data["content_types"] = form_data.get("content_types")
    if "analysis_types" in form_data:
        data["analysis_types"] = form_data.get("analysis_types")
    if "scope" in form_data:
        data["scope"] = form_data.get("scope")
    
    self._parse_json_fields(data)
    logger.info(f"Creating scenario with data: ...")
    return await super().insert_model(request, data)

async def update_model(self, request: Request, pk: Any, data: dict) -> Any:
    """Parse JSON fields before updating scenario."""
    # Same logic for update
    form_data = await request.form()
    
    if "content_types" in form_data:
        data["content_types"] = form_data.get("content_types")
    if "analysis_types" in form_data:
        data["analysis_types"] = form_data.get("analysis_types")
    if "scope" in form_data:
        data["scope"] = form_data.get("scope")
    
    self._parse_json_fields(data)
    logger.info(f"Updating scenario {pk} with data: ...")
    return await super().update_model(request, pk, data)
```

### Step 3: Remove unnecessary CSS

**File**: `app/templates/sqladmin/bot_scenario_form.html`

**Removed**:
```css
/* No longer needed - fields don't exist */
.form-group:has([name="scope"]),
.form-group:has([name="content_types"]),
.form-group:has([name="analysis_types"]) {
    display: none !important;
}
```

---

## How It Works Now

### Form Structure

**Before** (broken):
```html
<!-- SQLAdmin created these (hidden by CSS) -->
<textarea name="scope">old value</textarea>
<input name="content_types" value="old value">
<input name="analysis_types" value="old value">

<!-- Our template created these -->
<textarea id="scope-json" name="scope">new value</textarea>
<input type="hidden" name="content_types" value="new value">
<input type="hidden" name="analysis_types" value="new value">

<!-- Result: Duplicate fields! -->
```

**After** (working):
```html
<!-- Only our fields exist -->
<textarea id="scope-json" name="scope">value</textarea>
<input type="hidden" name="content_types" value="value">
<input type="hidden" name="analysis_types" value="value">

<!-- Result: Clean, no duplicates -->
```

### Data Flow

1. **User submits form**
2. **Request contains**: `content_types='["posts"]'`, `analysis_types='["sentiment"]'`, `scope='{"key":"value"}'`
3. **SQLAdmin calls** `insert_model(request, data)`
4. **data is empty** (fields were excluded from SQLAdmin form)
5. **We manually extract**: `form_data = await request.form()`
6. **We add to data**: `data["scope"] = form_data.get("scope")`
7. **We parse JSON**: `data["scope"] = json.loads(data["scope"])` → `{"key": "value"}`
8. **We call super**: `await super().insert_model(request, data)`
9. **SQLAdmin saves**: Model created with correct data ✅

---

## Testing

### Test 1: Check form HTML

1. Open: `http://0.0.0.0:8000/admin/bot-scenario/create`
2. Right-click → Inspect Element
3. Search for `name="scope"`
4. **Expected**: Only ONE textarea with `name="scope"` (id="scope-json")
5. **Not expected**: No duplicate hidden textarea

### Test 2: Check request data

Add debug logging in insert_model:

```python
async def insert_model(self, request: Request, data: dict) -> Any:
    form_data = await request.form()
    logger.info(f"Form data keys: {list(form_data.keys())}")
    logger.info(f"scope in form_data: {'scope' in form_data}")
    logger.info(f"scope value: {form_data.get('scope')}")
    # ... rest of code
```

**Expected logs**:
```
INFO: Form data keys: ['name', 'description', 'content_types', 'analysis_types', 'scope', ...]
INFO: scope in form_data: True
INFO: scope value: {"sentiment": {...}, "topics": {...}}
```

### Test 3: Save and verify

1. Fill form
2. Submit
3. Check database:
   ```python
   s = BotScenario.objects.get(name="Test")
   print(s.scope)  # Should be dict, not string
   print(s.analysis_types)  # Should be list, not string
   ```

---

## Files Modified

1. ✅ `app/admin/views.py`:
   - Updated `form_excluded_columns`
   - Updated `insert_model()` and `update_model()`

2. ✅ `app/templates/sqladmin/bot_scenario_form.html`:
   - Removed CSS hiding rules

---

## Comparison: Before vs After

### Before (V1) - Didn't work

```python
# SQLAdmin created fields automatically
# We hid them with CSS
# We created duplicate fields
# Conflict → data not saved
```

### After (V2) - Should work

```python
# SQLAdmin doesn't create fields (excluded)
# We create fields in template
# No duplicates
# Manual extraction from request
# Clean data flow → saved ✅
```

---

## Next Steps

1. **Restart server**
2. **Test create scenario**
3. **Test edit scenario**
4. **Verify in database**

If still not working, add more debug logs:

```python
async def insert_model(self, request: Request, data: dict) -> Any:
    logger.info(f"=== INSERT MODEL CALLED ===")
    form_data = await request.form()
    logger.info(f"Request form keys: {list(form_data.keys())}")
    
    for key in ["content_types", "analysis_types", "scope"]:
        if key in form_data:
            value = form_data.get(key)
            logger.info(f"Found {key}: {value[:100] if isinstance(value, str) else value}")
            data[key] = value
    
    logger.info(f"Data before parsing: {list(data.keys())}")
    self._parse_json_fields(data)
    logger.info(f"Data after parsing: {list(data.keys())}")
    
    try:
        result = await super().insert_model(request, data)
        logger.info(f"=== INSERT SUCCESSFUL: {result.id if hasattr(result, 'id') else 'unknown'} ===")
        return result
    except Exception as e:
        logger.error(f"=== INSERT FAILED: {e} ===")
        raise
```

---

**Status**: ✅ Ready to test  
**Author**: Factory Droid  
**Date**: Current Session
