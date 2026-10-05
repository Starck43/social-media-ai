# Chat-Based Scenario Creation System

## Architectural Principle

```
AgentScenario  = "how to analyse" (the analysis lens)
AgentTask      = "when to look + what to do" (the reaction)
Chat Agent     = interactive wizard for creating/modifying scenarios via tool calling
```

A scenario answers **what to collect**, **which metrics to extract**, and **how to prompt the LLM**.
A task answers **when to run**, **which sources**, **what action** (comment/dm/notify), and **guards** (limits, cooldowns).

### Why separate?

A scenario is reusable across tasks. A rule stored on the scenario would change meaning every time a differently-scheduled task picked it up.

See also:
- `docs/AGENT.md` — agent runtime, tools, memory
- `docs/AGENT_TASKS.md` — task scheduling, job queue, reactions
- `docs/PROMPT_AND_SCOPE_EXPLAINED.md` — prompt assembly, scope variables, auto-fields

---

## Data Model

### AgentScenario (analysis lens)

| Field | Type | Purpose |
|---|---|---|
| `name` | string | Human-readable name |
| `content_types` | JSON array | What to collect (`posts`, `comments`, `mentions`, `videos`, `stories`) |
| `analysis_types` | JSON array | Which metrics to extract (`sentiment`, `keywords`, `topics`, `competitor`, `brand_mentions`, `trends`) |
| `analyze_type` | enum | Grouping mode (`themes` / `days` / `sources` / `users`) |
| `scope` | JSON | Per-metric config (e.g. `{"keywords": {"max_keywords": 10}}`) |
| `base_prompt` | text | Core LLM instruction (required) |
| `media_overrides` | JSON | Per-media-type overrides: `{"image": "...", "video": "..."}` (optional) |
| `summary_prompt` | text | Final summary prompt (optional) |

**Planned migration:** replace the current 5 separate prompt fields (`text_prompt`, `image_prompt`, `video_prompt`, `audio_prompt`, `unified_summary_prompt`) with `base_prompt + media_overrides + summary_prompt`.

### AgentTask (reaction)

| Field | Type | Purpose |
|---|---|---|
| `cron_expr` | string | Schedule (croniter) |
| `job_type` | enum | `collect` / `digest` / `prune` / `analyze` / `learn` / `reflect` |
| `agent_scenario_id` | FK | Which analysis lens to apply |
| `trigger_type` / `trigger_config` | string/JSON | When to react (see `docs/AGENT_TASKS.md#the-reaction`) |
| `action_type` | enum | What to do: `comment` / `dm` / `notify` |
| `rate_limit_per_hour` | int | Guard |
| `cooldown_seconds` | int | Guard |
| `requires_approval` | bool | Guard |
| `blacklist` / `whitelist` | JSON | Guard lists |

---

## Prompt System

### Variable Resolution

```python
AVAILABLE_VARIABLES = {
    "text": "Collected content (post text/comments)",
    "platform": "Platform name (VK, Telegram)",
    "date_range": "Analysis period (2026-09-28 - 2026-10-05)",
    "trigger_condition": "Reaction condition description (from AgentTask)",
    "source_name": "Source name",
    "scenario_name": "Scenario name",
    # Scope-derived (any key from scope.<type>)
    "max_keywords": "scope.keywords.max_keywords",
    "max_topics": "scope.topics.max_topics",
    "sentiment_categories": "scope.sentiment.categories (comma-separated)",
}
```

### Prompt Assembly (`prompts.py::get_prompt()`)

1. Start with `base_prompt` as the base.
2. If `media_overrides[media_type]` exists — append as a `## Additionally for {media_type}` section.
3. Inject variables from `AVAILABLE_VARIABLES` (replace `{var}` with resolved values).
4. **JSON detection:** use regex `\{[^{}]*"[^"]+"\s*:` to detect if the user already wrote a JSON structure — if so, skip adding `COMMON_FIELDS` automatically.
5. Otherwise, append auto-generated fields per `json_schema_builder`.

### Validation

On scenario save, run `validate_prompt(base_prompt)` and `validate_prompt(media_overrides)`. Return a **warning** (not error) listing unknown variables:

```
Warning: unknown variable {foo}. Available: text, platform, date_range, ...
```

---

## Scenario Templates (Presets)

Location: `app/services/ai/scenario_templates.py`. Implemented as
`TEMPLATES: dict[str, dict]` — slug → the same fields a scenario stores — plus
`get_template`, `list_templates` and `expand_template` (the last one validates
the enums, generates the scope and merges the caller's overrides, so the tool
and the wizard produce the same row).

```python
TEMPLATES = {
    "brand_monitoring": {
        "name": "Brand Monitoring",
        "content_types": ["posts", "comments", "mentions"],
        "analysis_types": ["sentiment", "brand_mentions", "keywords"],
        "analyze_type": "themes",
        "scope": {"keywords": {"max_keywords": 10}},
        "base_prompt": "You are a brand analyst. Track mentions and sentiment.",
    },
    "competitor_watch": {
        "name": "Competitor Watch",
        "content_types": ["posts", "comments"],
        "analysis_types": ["competitor", "sentiment", "trends"],
        "analyze_type": "days",
        "scope": {"competitor": {"targets": []}},
        "base_prompt": "You are a competitive intelligence analyst...",
    },
    "customer_support": {
        "name": "Customer Support Monitor",
        "content_types": ["posts", "comments", "mentions"],
        "analysis_types": ["sentiment", "keywords"],
        "analyze_type": "themes",
        "scope": {"sentiment": {"categories": ["Positive", "Negative", "Neutral"]}},
        "base_prompt": "You are a customer support analyst...",
    },
    "trend_spotter": {
        "name": "Trend Spotter",
        "content_types": ["posts", "comments"],
        "analysis_types": ["trends", "keywords"],
        "analyze_type": "days",
        "scope": {},
        "base_prompt": "You are a trend analyst...",
    },
    "toxicity_guard": {
        "name": "Toxicity Guard",
        "content_types": ["posts", "comments"],
        "analysis_types": ["sentiment"],
        "analyze_type": "themes",
        "scope": {"sentiment": {"categories": ["Positive", "Neutral", "Toxic"]}},
        "base_prompt": "You are a content safety moderator...",
    },
}
```

---

## Agent Tools for Scenario Management

All tools live in `app/agent/toolset/scenarios.py` and register into
`TOOL_REGISTRY` by the usual side-effecting import. Each returns structured JSON. Dangerous actions use `pending_confirmation` flow.

| Tool | Purpose | Confirm? |
|---|---|---|
| `scenario_list` | List scenarios for workspace | No |
| `scenario_get(id)` | Details of one scenario | No |
| `scenario_templates` | Available presets | No |
| `scenario_suggest_prompt(description)` | LLM generates a prompt from a description | No |
| `scenario_validate_prompt(prompt)` | Check variables | No |
| `scenario_create(name, template_key?, overrides)` | Create from preset or full fields | Yes |
| `scenario_update(id, changes)` | Partial update | Yes |
| `scenario_clone(source_id, new_name, changes)` | Clone with modifications | Yes |
| `scenario_delete(id)` | Delete | Yes |

### Confirmation Flow

Uses the existing `agent_sessions.state['pending_confirmation']` — the runtime
gates every `confirm=True` tool the same way, so a scenario write waits for an
explicit «да» exactly like `task_add` or `source_add`:

```json
{
  "pending_confirmation": {
    "name": "scenario_create",
    "args": {"name": "...", "template_key": "brand_monitoring"},
    "expires_at": "2026-10-05T13:00:00+00:00"
  }
}
```

User replies "yes"/"no" — agent executes or cancels.

### Agent Memory for Preferences

Store user preferences in `agent_memory` under `scope=scenario_prefs`
(`app/services/ai/scenario_prefs.py`):

```
scope=scenario_prefs, key=default_language, value=ru
scope=scenario_prefs, key=preferred_analyze_type, value=days
scope=scenario_prefs, key=brands, value=["Fanta","Sprite"]
```

Lists are stored as JSON text (`value` is a `Text` column). `remember()` writes
after a successful create/update, `apply_to_draft()` fills the *empty* fields of
the next draft — in `scenario_create` and in the first step of the web wizard —
so an explicit choice from the current conversation always outranks a remembered
one.

### System Prompt Fragment

`SCENARIO_SECTION` in `app/agent/prompts.py`, appended by
`build_system_prompt()` (appended *after* the base prompt, so a deployment that
overrides the prompt via `AGENT_SYSTEM_PROMPT` still gets the procedure — the
tools are registered either way):

```markdown
## Создание сценариев
1. СНАЧАЛА узнай цель: что анализировать и зачем.
2. Предложи подходящий шаблон из scenario_templates.
3. Нет подходящего — проведи мастер: content_types → analysis_types →
   analyze_type → описание задачи.
4. Сгенерируй промпт через scenario_suggest_prompt.
5. Проверь переменные через scenario_validate_prompt.
6. Покажи превью и запроси подтверждение.
7. Вызови scenario_create — владелец подтвердит вызов отдельным «да».

НИКОГДА не создавай сценарий молча: превью и подтверждение обязательны.
```

The fragment is written in Russian like the rest of the prompt: the agent
answers the owner in their language, and a procedure the model has to translate
before following is a procedure it follows less reliably.

---

## UX Patterns

### Pattern 1: Quick Create from Template

```
User: I want to track my brand mentions on VK
Bot: Found template "Brand Monitoring". It includes:
  • Sentiment and brand mention analysis
  • Top-10 keywords
  • Theme grouping
  Create scenario "My Brand VK" from this template? [Yes/No/Customize]
```

### Pattern 2: Interactive Wizard

```
User: Create a competitor analysis scenario
Bot: Let's go step by step.

  1️⃣ Which content types?
     [a] Posts + comments (recommended)
     [b] Posts only
     [c] Everything including videos and stories

  2️⃣ Which metrics? (pick several)
     • competitor — competitor activity
     • sentiment — tone analysis
     • keywords — keywords
     • trends — trends

  3️⃣ How to group results?
     [themes] by topics
     [days] by day ← recommended for competitors
     [sources] by source

  4️⃣ Describe the task in your words — I'll generate a prompt.
     Example: "Compare our brand X with competitors Y and Z,
              track their new products and audience reaction"
```

### Pattern 3: Clone + Modify

```
User: Copy scenario "VK Monitoring" for Telegram
Bot: Cloned #12 → #15 "Telegram Monitoring".
     Adjusted: platform-specific prompt hints.
     Link to Telegram sources?
```

### Pattern 4: Explain and Teach

```
User: What is scope in a scenario?
Bot: Scope is a JSON config for each analysis type.

  Example:
  {
    "keywords": {"max_keywords": 10},
    "sentiment": {"categories": ["Positive", "Negative", "Neutral"]},
    "competitor": {"targets": ["BrandA", "BrandB"]}
  }

  Values from scope auto-inject into the prompt as variables:
  {max_keywords} → 10
  {sentiment_categories} → "Positive, Negative, Neutral"

  Show scope of your scenario #12?
```

---

## Implementation Priorities

### MUST HAVE

- [x] Replace 5 prompt fields with `base_prompt + media_overrides + summary_prompt`
- [x] Create `prompt_variables.py` with `AVAILABLE_VARIABLES`
- [x] Prompt variable validation on scenario save
- [x] JSON detection via regex parsing (not keyword matching)

### SHOULD HAVE

- [x] `scenario_templates.py` with 5+ presets
- [x] Tools: `scenario_list`, `scenario_get`, `scenario_templates`
- [x] Tools: `scenario_create`, `scenario_update`, `scenario_clone`, `scenario_delete`
- [x] Tool: `scenario_suggest_prompt` (LLM-generated prompt)
- [x] Tool: `scenario_validate_prompt`
- [x] Confirmation flow for create/update/delete
- [x] Agent memory for preferences (`scope=scenario_prefs`)
- [x] Update agent system prompt — scenario section

### NICE TO HAVE

- [x] Web UI: scenario creation wizard (matching chat UX)
- [ ] Gallery of public/community templates
- [ ] A/B testing of prompts (save both variants)
- [ ] Scenario quality metrics (which give best results)

---

## Key Insight

**The chat agent becomes the "face" of scenario creation.** Instead of making users navigate 15 admin form fields, the agent:

1. **Understands intent** ("I want to track competitors")
2. **Matches a template** or runs through the wizard
3. **Generates a prompt** via LLM from a plain description
4. **Validates** and shows a preview
5. **Remembers preferences** across sessions
6. **Requires confirmation** before dangerous actions

The admin UI stays for power users and debugging; 90% of scenarios will be created via chat — that's the **AI-native UX**.
