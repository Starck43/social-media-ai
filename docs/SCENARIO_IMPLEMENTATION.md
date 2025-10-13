# Bot Scenario Implementation Guide

**Version:** 2.0  
**Last Updated:** 2024-12-10

## Overview

This document describes the current implementation of the Bot Scenario system for AI-powered social media monitoring and analysis.

---

## Table of Contents

1. [Data Structure](#data-structure)
2. [Admin Interface](#admin-interface)
3. [Service Layer](#service-layer)
4. [Examples](#examples)
5. [Roadmap](#roadmap)

---

## Data Structure

### BotScenario Model

```python
class BotScenario:
    id: int
    name: str                        # "Customer Sentiment Monitoring"
    description: str                 # "Track customer feedback and sentiment"
    
    # What to collect from social networks
    content_types: JSON              # ["posts", "comments"]
    
    # Which analysis types to apply
    analysis_types: JSON             # ["sentiment", "keywords"]
    
    # Configuration parameters (no analysis_types here!)
    scope: JSON                      # {"brand_name": "MyBrand", "sentiment_config": {...}}
    
    # AI prompt template with variables
    ai_prompt: Text                  # "Analyze {brand_name} mentions on {platform}..."
    
    # Action after analysis (None = analysis only)
    action_type: BotActionType       # "comment" | "reply" | "notification" | None
    
    # Service fields
    is_active: bool
    cooldown_minutes: int
```

### Field Responsibilities

| Field | Purpose | Example |
|-------|---------|---------|
| **content_types** | **WHAT** to collect | `["posts", "comments"]` |
| **analysis_types** | **WHICH** analysis to apply | `["sentiment", "keywords"]` |
| **scope** | **PARAMETERS** for analysis | `{"brand_name": "MyBrand", "sentiment_config": {...}}` |
| **ai_prompt** | **INSTRUCTIONS** for LLM | `"Analyze {brand_name}..."` |
| **action_type** | **WHAT TO DO** after analysis | `"notification"` or `None` |

### Scope Structure

Scope contains:
1. **Analysis configuration** - parameters grouped by analysis type
2. **Custom variables** - used in prompts

```json
{
  "sentiment_config": {
    "categories": ["positive", "negative", "neutral", "mixed"],
    "confidence_threshold": 0.75,
    "emotion_analysis": true
  },
  "keywords_config": {
    "keywords": ["quality", "price", "delivery"],
    "extract_entities": false
  },
  "brand_name": "MyCompany",
  "focus_area": "customer satisfaction"
}
```

**Key points:**
- Configuration grouped by analysis type: `{analysis_type}_config`
- Custom variables are flat at scope root
- No `analysis_types` in scope (separate field!)

---

## Admin Interface

### Creating a Scenario

**Step 1:** Basic Information
```
Name: Customer Sentiment Monitoring
Description: Track customer reviews and feedback tone
```

**Step 2:** Choose Preset (Optional)
- Quick start with pre-configured scenarios
- 8 real-world presets available
- Can customize after selection

**Step 3:** Select Content Types
```
☑ Posts
☑ Comments
☐ Videos
☐ Stories
```

**Step 4:** Select Analysis Types
```
☑ Sentiment Analysis
☑ Keywords Extraction
☐ Trends Detection
☐ Toxicity Detection
```

**Step 5:** Configure Scope
- Visual editor shows parameters for selected analysis types
- JSON editor for advanced users
- Add custom variables for prompts

**Step 6:** Write AI Prompt
```
Analyze customer sentiment about {brand_name} from {platform}.
Focus: {focus_area}
Track keywords: {keywords_config[keywords]}

Content:
{content}

Return JSON with: overall_sentiment, sentiment_score, key_topics, customer_concerns.
```

**Step 7:** Choose Bot Action
- None (analysis only)
- Comment
- Reply
- Notification
- Moderation

### Presets

8 ready-to-use scenarios:

1. **Customer Sentiment** - Track product/service reviews
2. **Competitor Monitoring** - Watch competitor activity
3. **Crisis Detection** - Early detection of PR issues
4. **Influencer Tracking** - Monitor key opinion leaders
5. **Community Moderation** - Auto-moderate discussions
6. **Trend Discovery** - Find emerging topics
7. **Product Feedback** - Collect user feedback
8. **Comprehensive Analytics** - All analysis types

---

## Service Layer

### Architecture

```
┌─────────────────────────────────────┐
│       ContentCollector              │
│   (orchestrates collection)         │
└─────────────┬───────────────────────┘
              │
              ▼
┌─────────────────────────────────────┐
│         AIAnalyzer                  │
│  1. Load BotScenario                │
│  2. Build prompt from scenario      │
│  3. Merge scope with defaults       │
│  4. Call LLM                        │
│  5. Save results                    │
└─────────────┬───────────────────────┘
              │
              ▼
┌─────────────────────────────────────┐
│    ScenarioPromptBuilder            │
│  - Build context                    │
│  - Inject variables                 │
│  - Merge defaults                   │
└─────────────────────────────────────┘
```

### Analysis Flow

```python
# 1. Collect content
content = await collector.collect_from_source(source)

# 2. Analyzer checks if source has scenario
if source.bot_scenario_id:
    scenario = await BotScenario.objects.get(id=source.bot_scenario_id)
    
    # 3. Build context
    context = {
        'platform': 'VK',
        'source_type': 'group',
        'total_posts': 100,
        'content': "...",
        'date_range': {'first': '2024-01-01', 'last': '2024-01-31'}
    }
    
    # 4. Build prompt with variables
    prompt = ScenarioPromptBuilder.build_prompt(scenario, context)
    
    # 5. Call LLM
    result = await analyzer._call_api_with_prompt(prompt)
    
    # 6. Save analysis
    await AIAnalytics.objects.create(...)
```

### Default Parameters

Common parameters are auto-injected from `analysis_constants.py`:

```python
DEFAULT_ANALYSIS_PARAMS = {
    "language": "ru",
    "min_confidence": 0.6,
    "max_samples": 100,
}

SENTIMENT_DEFAULTS = {
    "categories": ["positive", "negative", "neutral", "mixed"],
    "confidence_threshold": 0.7,
    ...
}

# Similar defaults for: trends, engagement, keywords, topics, toxicity, demographics
```

Scope parameters override defaults.

---

## Examples

### Example 1: Customer Feedback Monitoring

**Scenario:**
```json
{
  "name": "Customer Feedback",
  "description": "Monitor reviews and complaints",
  "analysis_types": ["sentiment", "keywords", "topics"],
  "content_types": ["posts", "comments"],
  "scope": {
    "sentiment_config": {
      "categories": ["positive", "negative", "neutral"],
      "emotion_analysis": true
    },
    "keywords_config": {
      "keywords": ["bug", "error", "problem", "great", "excellent"],
      "extract_entities": false
    },
    "topics_config": {
      "max_topics": 8
    },
    "product_name": "MyApp",
    "version": "2.0"
  },
  "ai_prompt": "...",
  "action_type": "notification"
}
```

**Result:**
- Collects posts and comments
- Applies sentiment + keywords + topics analysis
- Merges scope with defaults
- Sends notification if negative sentiment detected

---

### Example 2: Competitor Tracking

**Scenario:**
```json
{
  "name": "Competitor Monitor",
  "description": "Track competitor posts and audience reaction",
  "analysis_types": ["engagement", "topics", "trends"],
  "content_types": ["posts", "videos"],
  "scope": {
    "engagement_config": {
      "metrics": ["likes", "comments", "shares", "views"],
      "viral_threshold": 5000
    },
    "trends_config": {
      "min_mentions": 10,
      "track_growth": true
    },
    "competitor_names": ["CompanyA", "CompanyB"],
    "market_segment": "SaaS"
  },
  "ai_prompt": "...",
  "action_type": null
}
```

**Result:**
- Analysis only (no bot action)
- Tracks viral content
- Identifies trending topics
- Weekly reports

---

### Example 3: Crisis Detection

**Scenario:**
```json
{
  "name": "PR Crisis Alert",
  "description": "Early detection of negative sentiment spikes",
  "analysis_types": ["sentiment", "toxicity", "trends"],
  "content_types": ["posts", "comments", "mentions"],
  "scope": {
    "sentiment_config": {
      "categories": ["negative", "mixed"],
      "confidence_threshold": 0.8
    },
    "toxicity_config": {
      "threshold": 0.6,
      "detect_harassment": true,
      "detect_hate_speech": true
    },
    "trends_config": {
      "min_mentions": 15,
      "time_window_hours": 6
    },
    "alert_threshold": "high",
    "monitored_topics": ["scandal", "lawsuit", "complaint"]
  },
  "ai_prompt": "...",
  "action_type": "notification"
}
```

**Result:**
- Real-time monitoring
- Immediate alerts on crisis signals
- Tracks negative sentiment growth
- 6-hour window for fast response

---

### Example 4: Community Moderation

**Scenario:**
```json
{
  "name": "Auto-Moderator",
  "description": "Detect and flag toxic comments",
  "analysis_types": ["toxicity", "sentiment"],
  "content_types": ["comments"],
  "scope": {
    "toxicity_config": {
      "threshold": 0.7,
      "detect_harassment": true,
      "detect_hate_speech": true,
      "detect_threats": true,
      "detect_profanity": true,
      "auto_moderate": false
    },
    "community_rules": [
      "No harassment",
      "No hate speech",
      "Be respectful"
    ]
  },
  "ai_prompt": "...",
  "action_type": "moderation"
}
```

**Result:**
- Scans all comments
- Flags toxic content
- Creates moderation tasks
- Respects community rules

---

## Roadmap

### Phase 1: Core Improvements (Q1 2025)

#### 1.1 Enhanced Analysis Types

**Add new analysis types:**

```python
class AnalysisType(Enum):
    # Existing
    SENTIMENT = "sentiment"
    TRENDS = "trends"
    ENGAGEMENT = "engagement"
    KEYWORDS = "keywords"
    TOPICS = "topics"
    TOXICITY = "toxicity"
    DEMOGRAPHICS = "demographics"
    
    # New
    VIRAL_DETECTION = "viral_detection"      # Predict viral potential
    INFLUENCER_ACTIVITY = "influencer"       # Track influencer engagement
    COMPETITOR_TRACKING = "competitor"       # Monitor competitors
    CUSTOMER_INTENT = "intent"               # Detect purchase intent
    BRAND_MENTIONS = "brand_mentions"        # Track brand mentions
    HASHTAG_ANALYSIS = "hashtag_analysis"    # Analyze hashtag performance
```

#### 1.2 Multi-Source Scenarios

Allow scenarios to work across multiple sources:

```python
class BotScenario:
    source_filters: JSON  # Filter criteria for auto-assignment
    # Example: {"platform_type": "VK", "source_type": "GROUP", "tags": ["customer_service"]}
```

#### 1.3 Conditional Actions

Add conditions for bot actions:

```python
class BotScenario:
    action_conditions: JSON
    # Example:
    # {
    #   "trigger_on": "sentiment_score < 0.3",
    #   "min_confidence": 0.8,
    #   "require_human_approval": true
    # }
```

---

### Phase 2: Advanced Features (Q2 2025)

#### 2.1 Scenario Chains

Chain scenarios for complex workflows:

```
Scenario 1 (Detection) → Scenario 2 (Analysis) → Scenario 3 (Response)
```

#### 2.2 A/B Testing

Test different prompts and configurations:

```python
class ScenarioVariant:
    scenario_id: int
    variant_name: str
    ai_prompt: Text
    scope: JSON
    traffic_percentage: int
```

#### 2.3 Performance Analytics

Track scenario effectiveness:

```python
class ScenarioMetrics:
    scenario_id: int
    total_executions: int
    avg_analysis_time: float
    success_rate: float
    llm_cost: Decimal
    action_success_rate: float
```

#### 2.4 Prompt Templates Library

Reusable prompt building blocks:

```python
PROMPT_TEMPLATES = {
    "sentiment_basic": "Analyze sentiment: {content}",
    "sentiment_detailed": "Analyze sentiment with emotions and context: {content}",
    "toxicity_strict": "Flag any toxic content: {content}",
    ...
}
```

---

### Phase 3: AI Improvements (Q3 2025)

#### 3.1 Multi-Model Support

Support multiple LLM providers:

```python
class BotScenario:
    llm_provider: str  # "deepseek" | "openai" | "anthropic" | "local"
    llm_model: str     # "deepseek-chat" | "gpt-4" | "claude-3"
```

#### 3.2 Fine-Tuned Models

Train custom models for specific domains:

```python
class FineTunedModel:
    name: str
    base_model: str
    training_data: JSON
    scenarios: List[BotScenario]
```

#### 3.3 RAG (Retrieval-Augmented Generation)

Add context from knowledge base:

```python
class KnowledgeBase:
    scenario_id: int
    documents: List[Document]
    embeddings: JSON
```

#### 3.4 Self-Improving Prompts

Auto-optimize prompts based on results:

```python
class PromptOptimization:
    original_prompt: str
    optimized_prompts: List[str]
    performance_scores: List[float]
    best_prompt: str
```

---

### Phase 4: Integration & Automation (Q4 2025)

#### 4.1 Webhook Actions

Trigger external services:

```python
class WebhookAction:
    scenario_id: int
    webhook_url: str
    payload_template: JSON
    auth_config: JSON
```

#### 4.2 Schedule-Based Scenarios

Run scenarios on schedule:

```python
class ScenarioSchedule:
    scenario_id: int
    cron_expression: str  # "0 9 * * *" (daily at 9am)
    timezone: str
```

#### 4.3 Real-Time Stream Processing

Process content in real-time:

```python
class StreamScenario:
    scenario_id: int
    stream_sources: List[str]
    buffer_size: int
    processing_interval: int
```

#### 4.4 Collaborative Filtering

Recommend scenarios based on usage:

```python
def recommend_scenarios(user_id: int, current_sources: List[Source]) -> List[BotScenario]:
    # ML-based recommendations
    pass
```

---

### Phase 5: Enterprise Features (2026)

#### 5.1 Multi-Tenant Support

Isolate scenarios by organization:

```python
class Organization:
    id: int
    name: str
    scenarios: List[BotScenario]
    users: List[User]
    quota: ScenarioQuota
```

#### 5.2 Approval Workflows

Require approval for sensitive actions:

```python
class ApprovalWorkflow:
    scenario_id: int
    required_approvers: List[User]
    approval_rules: JSON
```

#### 5.3 Audit Logs

Track all scenario executions:

```python
class ScenarioAuditLog:
    scenario_id: int
    executed_at: datetime
    executed_by: User
    input_data: JSON
    output_data: JSON
    duration_ms: int
```

#### 5.4 Cost Management

Track and limit LLM costs:

```python
class CostManagement:
    organization_id: int
    monthly_budget: Decimal
    current_spend: Decimal
    cost_alerts: List[Alert]
```

---

## Migration Notes

### From Version 1.0 to 2.0

**Key Changes:**
1. `analysis_types` moved from `scope` to separate field
2. `description` field added
3. Scope now only contains configuration parameters
4. Admin UI completely redesigned

**Migration Steps:**

```bash
# 1. Run migration
alembic upgrade head

# 2. Data automatically migrated:
#    - scope.analysis_types → analysis_types field
#    - scope cleaned (analysis_types removed)

# 3. Update existing scenarios via admin:
#    - Add descriptions
#    - Review scope parameters
#    - Test prompts with new structure
```

**Breaking Changes:**
- Prompt templates must use new variable names
- Scope structure changed (grouped by `{type}_config`)
- ScenarioPromptBuilder API changed

---

## Best Practices

### 1. Naming Conventions

```
✅ Good: "Customer Sentiment Monitoring"
❌ Bad: "scenario1"

✅ Good: "PR Crisis Detection - Social Media"
❌ Bad: "crisis"
```

### 2. Scope Organization

```json
{
  "sentiment_config": {
    "categories": ["positive", "negative", "neutral"]
  },
  "custom_variable": "value"
}
```

**Rules:**
- Analysis configs grouped: `{analysis_type}_config`
- Custom variables flat at root
- Use descriptive names
- Document complex structures

### 3. Prompt Design

```python
# ✅ Good prompt
"""
Analyze {brand_name} mentions on {platform}.
Focus: {focus_area}
Keywords: {keywords_config[keywords]}

Content:
{content}

Return JSON: {{"sentiment": "...", "summary": "..."}}
"""

# ❌ Bad prompt
"Analyze this: {content}"
```

### 4. Testing

```python
# Test scenario before production
async def test_scenario(scenario_id: int):
    scenario = await BotScenario.objects.get(id=scenario_id)
    
    # Mock content
    test_content = [
        {"text": "Great product!", "date": "2024-01-01"},
        {"text": "Terrible service", "date": "2024-01-01"}
    ]
    
    # Run analysis
    result = await analyzer.analyze_content(test_content, source)
    
    # Verify results
    assert result is not None
    assert 'sentiment' in result.summary_data
```

### 5. Performance

- Use `cooldown_minutes` to avoid rate limits
- Limit `content_types` to essential only
- Choose appropriate `analysis_types`
- Monitor LLM costs

---

## Troubleshooting

### Scenario not working

**Check:**
1. `is_active = True`
2. Source has `bot_scenario_id` set
3. Content types match source content
4. Prompt has all required variables
5. Scope contains all variables used in prompt

### Variables not replaced

**Error:** `Missing variable 'brand_name' in prompt`

**Fix:**
```json
{
  "scope": {
    "brand_name": "MyBrand"
  }
}
```

### Analysis returns empty

**Check:**
1. Content collected successfully
2. LLM API key configured
3. Prompt format correct
4. Response parseable as JSON

---

## Support

For questions or issues:
- Check this documentation
- Review preset examples
- Test in staging first
- Check logs in `AIAnalytics.response_payload`

---

## Conclusion

The Bot Scenario system provides flexible AI-powered social media monitoring with:
- Clear data structure
- Intuitive admin interface
- Powerful service layer
- Extensible architecture
- Real-world presets

Start with presets, customize as needed, and scale to complex workflows.
