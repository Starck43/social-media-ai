# LLM Mapping Architecture Decision

## Problem

`BotScenario` has confusing provider selection with duplicated fields:

```python
BotScenario:
    # LEGACY (FK fields)
    text_llm_provider_id: int | None
    image_llm_provider_id: int | None
    video_llm_provider_id: int | None
    
    # NEW (JSON field)
    llm_mapping: dict = {
        "text": {
            "provider_id": 1,
            "model_id": "gpt-4",
            "provider_type": "openai"
        }
    }
    
    # Strategy
    llm_strategy: str = "cost_efficient"
```

**Issues:**

1. ❌ **Duplication**: `llm_mapping` stores data already in `LLMProvider`
2. ❌ **Complexity**: Two ways to specify same thing (FK vs JSON)
3. ❌ **Unused strategy**: `llm_strategy` exists but not applied
4. ❓ **Unclear fallback**: What if nothing specified?

## Questions Answered

### 1. What do these fields provide?

**Purpose**: Allow per-scenario provider overrides

- **With override**: Use specific provider for this scenario
- **Without override**: Auto-resolve based on strategy

### 2. What if providers not specified?

**Should use auto-resolve based on `llm_strategy`**:

```python
# Scenario with no providers specified
scenario = BotScenario(
    name="Monitor VK",
    content_types=["posts", "videos"],
    llm_strategy="cost_efficient",  # Use this!
    text_llm_provider_id=None,  # Auto-resolve
    image_llm_provider_id=None,  # Auto-resolve
)

# Resolver chooses:
# text → DeepSeek (cheap)
# video → GPT-4 Vision (expensive but needed)
```

### 3. Does content structure change per provider?

**NO - Structure is always the same:**

```python
LLMMapping:
    provider_id: int       # Which provider to use
    provider_type: str     # "openai", "deepseek", etc
    model_id: str          # "gpt-4-vision-preview"
    capabilities: list     # ["text", "image", "video"]
```

This is **runtime data**, not stored in DB.

### 4. Should mapping be in BotScenario or LLMProvider?

**Answer: Neither should store runtime mapping!**

**Storage:**
- `BotScenario` → FK references (optional overrides)
- `BotScenario` → `llm_strategy` (fallback logic)
- `LLMProvider` → Provider data (model, API, capabilities)

**Runtime:**
- `LLMProviderResolver` → Builds mapping on-the-fly
- No JSON storage needed!

## ✅ IMPLEMENTED SOLUTION

### Final Architecture (as of migration 0028):

```python
BotScenario:
    # Content to analyze
    content_types: list[str] = ["posts", "videos"]
    
    # Strategy for auto-resolve (MAIN FIELD)
    llm_strategy: str = "cost_efficient"  # or "quality" or "multimodal"
    
    # Optional explicit overrides (FK only)
    text_llm_provider_id: int | None = None
    image_llm_provider_id: int | None = None
    video_llm_provider_id: int | None = None
```

### ❌ REMOVED (Migration 0028):

```python
# This field has been DELETED from database and model
# llm_mapping: dict = {}  # ← REMOVED
```

**Status**: Fully implemented and deployed.

### Logic Flow:

```python
async def resolve_providers(scenario: BotScenario) -> dict[str, LLMMapping]:
    """
    Resolve LLM providers for scenario.
    
    Priority:
    1. Explicit FK overrides (if specified)
    2. Auto-resolve by llm_strategy (fallback)
    """
    result = {}
    
    # Check explicit overrides first
    if scenario.text_llm_provider_id:
        provider = await LLMProvider.objects.get(id=scenario.text_llm_provider_id)
        result["text"] = LLMMapping(
            provider_id=provider.id,
            provider_type=provider.provider_type,
            model_id=provider.model_name,
            capabilities=provider.capabilities
        )
    
    # Same for image, video...
    
    # Fill missing with auto-resolve
    if "text" not in result:
        # Use llm_strategy to choose provider
        resolved = LLMProviderResolver.resolve_for_content_types(
            content_types=scenario.content_types,
            strategy=scenario.llm_strategy  # ← USE THIS!
        )
        result.update(resolved)
    
    return result
```

## Benefits

### Before (Current):

```python
# Confusing: Two ways to specify same thing
scenario = BotScenario(
    text_llm_provider_id=1,  # FK
    llm_mapping={             # JSON (duplicate!)
        "text": {"provider_id": 1, "model_id": "gpt-4"}
    }
)

# Which one wins? Unclear!
```

### After (Simplified):

```python
# Option 1: Explicit override
scenario = BotScenario(
    llm_strategy="cost_efficient",
    text_llm_provider_id=1  # Only this provider for text
)

# Option 2: Full auto-resolve
scenario = BotScenario(
    llm_strategy="quality"  # Resolver chooses best
)

# Clean and clear!
```

## Migration Plan

### Step 1: Make `llm_mapping` nullable

```sql
ALTER TABLE social_manager.bot_scenarios 
ALTER COLUMN llm_mapping DROP NOT NULL;
```

### Step 2: Update resolver to prioritize FK fields

```python
# In llm_provider_resolver.py
@classmethod
def resolve_from_bot_scenario(cls, bot_scenario):
    result = {}
    
    # Priority 1: FK overrides
    if bot_scenario.text_llm_provider:
        result["text"] = LLMMapping.from_provider(bot_scenario.text_llm_provider)
    
    # Priority 2: Auto-resolve missing ones
    if not result:
        result = cls.resolve_for_content_types(
            content_types=bot_scenario.content_types,
            strategy=bot_scenario.llm_strategy  # ← USE STRATEGY
        )
    
    return result
```

### Step 3: Update analyzer_v2 to use resolver

```python
# In analyzer_v2.py
async def analyze_content(self, content, source):
    bot_scenario = await BotScenario.objects.get(id=source.bot_scenario_id)
    
    # Resolve providers (FK first, then auto-resolve)
    providers = LLMProviderResolver.resolve_from_bot_scenario(bot_scenario)
    
    # Use resolved providers
    if "text" in providers:
        text_result = await self._analyze_text(
            content["text"],
            provider=providers["text"]
        )
```

### Step 4: Deprecate `llm_mapping` (future)

After confirming FK approach works:
1. Remove `llm_mapping` column from database
2. Remove from model
3. Clean up resolver code

## Summary

**Decision: Use FK fields + llm_strategy, remove llm_mapping**

| Field | Purpose | Required? |
|-------|---------|-----------|
| `text_llm_provider_id` | Explicit override for text | No (auto-resolve) |
| `image_llm_provider_id` | Explicit override for images | No (auto-resolve) |
| `video_llm_provider_id` | Explicit override for videos | No (auto-resolve) |
| `llm_strategy` | Auto-resolve strategy | Yes (default: "cost_efficient") |
| ~~`llm_mapping`~~ | ~~JSON storage~~ | **❌ DELETE** |

**Why?**
- ✅ Simple: One way to specify (FK or auto-resolve)
- ✅ Flexible: Can override specific types
- ✅ Smart: Auto-resolves missing ones by strategy
- ✅ Clean: No data duplication
