# Documentation router

Not a startup reading list. Start with README/AGENTS and one task card; consult
this index only to find a relevant contract. Record actual reads in that card.

## Read by purpose

| Purpose | Document / section |
| --- | --- |
| Product directions → phase → task | [PRODUCT_PLAN](PRODUCT_PLAN.md) → [ROADMAP](../ROADMAP.md) → `tasks/<id>.md` |
| Locate implementation | [PROJECT_MAP](PROJECT_MAP.md) |
| Claim/evidence/acceptance or delegation | [COLLABORATION](COLLABORATION.md), corresponding heading |
| Testing / migration target | [test-runner](../.agents/skills/test-runner/SKILL.md), [test contract](../tests/AGENTS.md), [database](COLLABORATION.md#database) |
| Release/activation audit | [BUSINESS_PRODUCTION_READINESS](BUSINESS_PRODUCTION_READINESS.md), one selected gate |
| Audit a delivered slice | [IMPLEMENTATION_STATUS](IMPLEMENTATION_STATUS.md), then its original PR/phase record |

## Subsystem contracts — load only what changes

| Area | Contract |
| --- | --- |
| Chat tools / sessions / memory | [AGENT](AGENT.md) |
| Jobs / schedules / runtime | [AGENT_TASKS](AGENT_TASKS.md) |
| Sources / collection | [COLLECTION](COLLECTION.md), [VK application](VK_STANDALONE_APP.md) |
| Typed analysis / LLM pipeline | [AI_PIPELINE_AND_PROMPTS](AI_PIPELINE_AND_PROMPTS.md) |
| Analytics / drill-down | [ANALYTICS_AGGREGATION_SYSTEM](ANALYTICS_AGGREGATION_SYSTEM.md) |
| Reports / delivery / messages | [DIGEST](DIGEST.md), [CHANNELS](CHANNELS.md), [NOTIFICATIONS](NOTIFICATIONS.md) |
| Identity / ownership / access | [TENANCY](TENANCY.md) |
| ORM / deletion | [MODELS](MODELS.md), [MODEL_DELETION](MODEL_DELETION.md) |
| Interfaces | [API](API.md), [CLI](CLI.md), [ADMIN](ADMIN.md) |
| Environment / deployment | [CONFIGURATION](CONFIGURATION.md), [DEPLOYMENT](DEPLOYMENT.md) |

## Folder ownership

- `tasks/`: per-task/question notebook and parent phase scope/closed-task history.
  ROADMAP alone owns open status/owner; cards link documents read and checks.
- `design/`: substantial feature decisions/contracts, only when too detailed for
  a task card. No task status/duplicate queue. Existing examples: [shell](design/assistant-shell.md),
  [collection](design/collection-plan.md), [task scope](design/task-selection.md).
- `audit/`: historical reviews and detailed test/failure evidence linked by a
  task/PR. No routine requirement to create a report or preload this directory.
- `phases/`: whole closed phases; [archive index](phases/README.md).
- `validation/`: compatibility location for machine-consumed JSON fixture files.
  No new narrative docs; retain pinned paths until consumers migrate together.

Facts have one owner: direction/extension contracts in PRODUCT_PLAN, development
procedures in COLLABORATION, release criteria in BUSINESS_PRODUCTION_READINESS,
source-specific behavior in subsystem docs, task detail in its card, status in
ROADMAP, delivered evidence in PR/phase history. Link instead of copying.
