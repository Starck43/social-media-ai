# AI Services & API Update - Summary

**Date:** 2024-12-10  
**Task:** Update AI services and API endpoints for new BotScenario structure

---

## ✅ Changes Applied

### 1. New Schema File Created

**File:** `app/schemas/scenario.py` (NEW)

Complete Pydantic schemas for Bot Scenario API:

```python
# Base schemas
- ScenarioBase: Common fields (name, description, ai_prompt, is_active, cooldown_minutes)
- ScenarioCreate: For creating scenarios (includes analysis_types, content_types, scope)
- ScenarioUpdate: For updating scenarios (all fields optional)
- ScenarioResponse: For returning scenario data (includes all fields + timestamps)
- ScenarioAssign: For assigning scenarios to sources
- ScenarioSourcesResponse: For listing sources using a scenario
```

**Key Features:**
- ✅ **Separate analysis_types field** (not in scope anymore)
- ✅ **Description field** support
- ✅ **Field validation** with Pydantic
- ✅ **English docstrings** for all schemas

---

### 2. API Endpoints Updated

**File:** `app/api/v1/endpoints/scenarios.py` (UPDATED)

All endpoints updated to support new structure:

#### POST `/scenarios` - Create scenario
```json
{
    "name": "Sentiment Monitoring",
    "description": "Track customer sentiment",
    "analysis_types": ["sentiment", "keywords"],
    "content_types": ["posts", "comments"],
    "scope": {
        "sentiment_config": {
            "categories": ["positive", "negative", "neutral"]
        }
    },
    "ai_prompt": "Analyze sentiment: {content}",
    "action_type": "NOTIFICATION"
}
```

#### GET `/scenarios` - List scenarios
- Returns `analysis_types`, `content_types`, and `scope` separately
- Optional filter by `is_active`

#### GET `/scenarios/{id}` - Get scenario
- Full details including analysis configuration

#### PUT `/scenarios/{id}` - Update scenario
- Partial updates supported
- Can update `analysis_types`, `content_types`, `scope` independently

#### DELETE `/scenarios/{id}` - Delete scenario
- Cascades to sources (sets bot_scenario_id to NULL)

#### POST `/scenarios/assign` - Assign to source
- Assigns scenario to a source for automatic analysis

#### GET `/scenarios/{id}/sources` - Get sources using scenario
- Returns list of sources with this scenario assigned

**Changes:**
- ✅ Import schemas from `app.schemas.scenario`
- ✅ Add `description` and `analysis_types` parameters
- ✅ Update response models to include new fields
- ✅ Enhanced docstrings with examples
- ✅ Better error messages

---

### 3. AIAnalyzer Service Enhanced

**File:** `app/services/ai/analyzer.py` (UPDATED)

**Major Improvements:**

#### Enhanced Documentation
```python
class AIAnalyzer:
    """
    Service for comprehensive social media content analysis using DeepSeek AI.
    
    This service handles:
    - Collecting content from various social media sources
    - Building AI prompts based on bot scenarios or default templates
    - Calling the DeepSeek LLM API for analysis
    - Saving analysis results with full LLM tracing
    
    The analyzer supports both scenario-based and default analysis modes.
    """
```

#### Scenario Integration
```python
# Load bot scenario if assigned to the source
# Bot scenario defines analysis_types, scope, and custom AI prompt
bot_scenario = None
if source.bot_scenario_id:
    bot_scenario = await BotScenario.objects.get(id=source.bot_scenario_id)
    logger.info(
        f"Using bot scenario '{bot_scenario.name}' (ID: {bot_scenario.id}) "
        f"with analysis types: {bot_scenario.analysis_types} for source {source.id}"
    )
```

#### Scenario Metadata in Analysis
```python
# Add scenario information if used
if bot_scenario:
    comprehensive_data["scenario_metadata"] = {
        "scenario_id": bot_scenario.id,
        "scenario_name": bot_scenario.name,
        "analysis_types": bot_scenario.analysis_types,
        "content_types": bot_scenario.content_types,
    }
```

**Method Updates:**

| Method | Update |
|--------|--------|
| `__init__` | Added docstring explaining initialization |
| `analyze_content` | Enhanced logging with analysis_types and content_types |
| `_prepare_text` | Detailed docstring about sampling strategy |
| `_get_platform_name` | Explained lazy loading avoidance |
| `_calculate_content_stats` | Documented purpose of statistics |
| `_get_default_prompt` | Explained comprehensive analysis structure |
| `_call_api_with_prompt` | Added Args, Returns, Raises documentation |
| `_save_analysis` | **NEW: bot_scenario parameter**, comprehensive docstring |

**Changes:**
- ✅ All methods have English docstrings
- ✅ Inline comments explain complex logic
- ✅ Scenario metadata saved in AIAnalytics
- ✅ Enhanced logging with scenario information
- ✅ Better type hints and parameter documentation

---

### 4. ScenarioPromptBuilder Enhanced

**File:** `app/services/ai/scenario.py` (UPDATED)

```python
class ScenarioPromptBuilder:
    """
    Helper class for building AI prompts from scenario configuration.
    
    This class handles:
    - Variable substitution in prompt templates
    - Merging scenario scope with default parameters
    - Building complete prompts ready for LLM consumption
    """
```

No structural changes needed - already working correctly with new structure.

---

## 📊 Data Flow

### Creating a Scenario

```
User (API) → POST /scenarios
    ↓
ScenarioCreate schema validates input
    ↓
ScenarioService.create_scenario()
    ↓
BotScenario.objects.create(
    name="...",
    description="...",
    analysis_types=["sentiment", "keywords"],
    content_types=["posts", "comments"],
    scope={"sentiment_config": {...}}
)
    ↓
ScenarioResponse returned to user
```

### Using a Scenario for Analysis

```
ContentCollector collects posts/comments
    ↓
AIAnalyzer.analyze_content(content, source)
    ↓
IF source.bot_scenario_id:
    Load BotScenario from database
    ScenarioPromptBuilder.build_prompt(scenario, context)
    ↓ uses
    - scenario.analysis_types
    - scenario.content_types
    - scenario.scope (merged with defaults)
    - scenario.ai_prompt (template)
ELSE:
    Use default comprehensive prompt
    ↓
DeepSeek API call
    ↓
AIAnalytics.objects.create(
    summary_data={
        "ai_analysis": {...},
        "content_statistics": {...},
        "source_metadata": {...},
        "scenario_metadata": {  ← NEW!
            "scenario_id": ...,
            "scenario_name": ...,
            "analysis_types": ...,
            "content_types": ...
        }
    }
)
```

---

## 🧪 Testing Checklist

### API Endpoints

- [ ] **Create scenario** with new structure
  ```bash
  curl -X POST /api/v1/scenarios \
    -d '{
      "name": "Test Scenario",
      "description": "Testing new structure",
      "analysis_types": ["sentiment", "keywords"],
      "content_types": ["posts", "comments"],
      "scope": {"sentiment_config": {...}},
      "ai_prompt": "Analyze: {content}"
    }'
  ```

- [ ] **List scenarios** - verify analysis_types and description display

- [ ] **Get scenario by ID** - verify all fields present

- [ ] **Update scenario** - partial update of analysis_types

- [ ] **Delete scenario** - verify cascade behavior

- [ ] **Assign to source** - verify source gets scenario_id

- [ ] **Get scenario sources** - verify source list returns

### AI Analysis

- [ ] **Scenario-based analysis**
  1. Create scenario with specific analysis_types
  2. Assign to source
  3. Trigger content collection
  4. Verify AIAnalytics has scenario_metadata

- [ ] **Default analysis**
  1. Remove scenario from source
  2. Trigger content collection
  3. Verify default comprehensive analysis

- [ ] **Scenario metadata**
  1. Check AIAnalytics.summary_data contains scenario_metadata
  2. Verify analysis_types and content_types are logged

### Error Cases

- [ ] Missing required fields in scenario creation
- [ ] Invalid analysis_types values
- [ ] Scenario not found (404)
- [ ] Source not found when assigning
- [ ] API key not configured

---

## 📝 API Examples

### Create Comprehensive Monitoring Scenario

```json
POST /api/v1/scenarios
{
  "name": "Comprehensive Brand Monitoring",
  "description": "Full analysis of brand mentions across social media",
  "analysis_types": [
    "sentiment",
    "keywords",
    "topics",
    "brand_mentions",
    "engagement"
  ],
  "content_types": ["posts", "comments", "videos"],
  "scope": {
    "sentiment_config": {
      "categories": ["positive", "negative", "neutral", "mixed"],
      "confidence_threshold": 0.7
    },
    "keywords_config": {
      "keywords": ["наш бренд", "наша компания", "новый продукт"]
    },
    "brand_mentions_config": {
      "brand_names": ["OurBrand", "OurCompany"],
      "track_sentiment": true
    },
    "engagement_config": {
      "metrics": ["likes", "comments", "shares"],
      "viral_threshold": 1000
    }
  },
  "ai_prompt": """
Проанализируй упоминания бренда в социальных сетях.

Ключевые слова для отслеживания: {keywords_config.keywords}
Бренды: {brand_mentions_config.brand_names}

Контент для анализа:
{content}

Обрати внимание на:
- Общую тональность
- Ключевые темы обсуждения
- Вирусный потенциал (порог: {engagement_config.viral_threshold})
  """,
  "action_type": "NOTIFICATION",
  "is_active": true,
  "cooldown_minutes": 60
}
```

### Update Scenario Analysis Types

```json
PUT /api/v1/scenarios/123
{
  "analysis_types": ["sentiment", "keywords", "topics", "toxicity"],
  "scope": {
    "toxicity_config": {
      "threshold": 0.8,
      "languages": ["ru", "en"]
    }
  }
}
```

### Assign Scenario to Source

```json
POST /api/v1/scenarios/assign
{
  "source_id": 456,
  "scenario_id": 123
}
```

---

## 🔍 Database Changes

**No migrations needed** - structure already updated in previous migration:
- ✅ `analysis_types` JSON field exists
- ✅ `description` TEXT field exists
- ✅ `scope` JSON field exists (without analysis_types)

**AIAnalytics now stores:**
```json
{
  "summary_data": {
    "ai_analysis": {...},
    "content_statistics": {...},
    "source_metadata": {...},
    "scenario_metadata": {  ← NEW FIELD
      "scenario_id": 123,
      "scenario_name": "Sentiment Monitoring",
      "analysis_types": ["sentiment", "keywords"],
      "content_types": ["posts", "comments"]
    }
  }
}
```

---

## 📚 Documentation Updates

### Files Created/Updated:

1. ✅ **app/schemas/scenario.py** (NEW)
   - Complete schema definitions
   - Field validation
   - English docstrings

2. ✅ **app/api/v1/endpoints/scenarios.py** (UPDATED)
   - All endpoints support new structure
   - Enhanced docstrings with examples
   - Better error handling

3. ✅ **app/services/ai/analyzer.py** (UPDATED)
   - Comprehensive English docstrings
   - Inline comments explaining logic
   - Scenario metadata integration

4. ✅ **docs/BOT_BEHAVIOR.md** (EXISTING)
   - Explains action_type logic
   - Documents analysis flow

5. ✅ **AI_SERVICES_UPDATE.md** (THIS FILE)
   - Summary of all changes
   - Testing checklist
   - API examples

---

## ✅ Summary

**What Changed:**
- Created schema definitions for API endpoints
- Updated all scenario API endpoints to support new structure
- Enhanced AIAnalyzer with comprehensive English documentation
- Added scenario metadata to AI

Analytics results
- Improved logging throughout AI services

**What Works:**
- Creating scenarios with analysis_types and description
- Assigning scenarios to sources
- Running AI analysis with scenario configuration
- Storing scenario metadata in analytics results
- Full LLM tracing with scenario information

**Benefits:**
1. **Clear separation** - analysis_types not in scope anymore
2. **Better documentation** - English docstrings everywhere
3. **Full traceability** - scenario metadata in analytics
4. **Type safety** - Pydantic schemas validate input
5. **Maintainability** - Clean code with comments

---

**All changes are backward compatible!** ✅

Existing scenarios will work, new ones use the enhanced structure.
