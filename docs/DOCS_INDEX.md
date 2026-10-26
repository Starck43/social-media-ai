# Documentation router

First session: [README](../README.md), [DESIGN](DESIGN.md), [AGENTS](../AGENTS.md).
Then [ROADMAP](../ROADMAP.md) → one card. This index is optional; load a contract
only when needed and record the relevant section/purpose in the task card.

| Need | Canonical document |
| --- | --- |
| Product directions / phase plans | [PRODUCT_PLAN](PRODUCT_PLAN.md) → [phases](phases/README.md) |
| Role / short task prompts | [PROMPT_EXAMPLES](PROMPT_EXAMPLES.md) → selected shared skill |
| Claim, READY, audit, synchronization, cleanup | [COLLABORATION](COLLABORATION.md), selected section |
| Chat tools / sessions / memory | [AGENT](AGENT.md) |
| Jobs / schedules / typed task plans | [AGENT_TASKS](AGENT_TASKS.md) |
| Collection / selection / VK authorization | [COLLECTION](COLLECTION.md), [VK application](VK_STANDALONE_APP.md) |
| Structured analysis / LLM contracts | [AI pipeline](AI_PIPELINE_AND_PROMPTS.md) |
| Analytics / reports / delivery | [Analytics](ANALYTICS_AGGREGATION_SYSTEM.md), [DIGEST](DIGEST.md), [CHANNELS](CHANNELS.md), [NOTIFICATIONS](NOTIFICATIONS.md) |
| Identity / ownership / permissions / cutover | [TENANCY](TENANCY.md), [ACCESS_MIGRATION](ACCESS_MIGRATION.md) |
| ORM / deletion | [MODELS](MODELS.md), [MODEL_DELETION](MODEL_DELETION.md) |
| Interfaces | [API](API.md), [CLI](CLI.md), [ADMIN](ADMIN.md) |
| Environment / deployment | [CONFIGURATION](CONFIGURATION.md), [DEPLOYMENT](DEPLOYMENT.md) |
| Test procedures / reusable fixtures | [tests/AGENTS](../tests/AGENTS.md), [shared test presets](../tests/shared/README.md) |
| Release criteria | [BUSINESS_PRODUCTION_READINESS](BUSINESS_PRODUCTION_READINESS.md) |
| Earlier deliveries / scaling decisions | [IMPLEMENTATION_STATUS](IMPLEMENTATION_STATUS.md) → phase/audit/PR evidence |

## File ownership

- `docs/phases/`: active phase plans and compact completed summaries; each links
  its product parent, active tasks and accepted audits. No task-status copies.
- `docs/tasks/`: active task/question detail, parent/related links, documents read,
  checks and PR handoff. ROADMAP alone owns open status/owner/dialogue.
- `docs/audit/`: accepted task summaries, blocked diagnostics and lazy historical
  evidence. Accepted orphan tasks leave only an audit here.
- Technical contracts remain in `docs/`; shared test JSON is in
  `tests/shared/fixtures/`. There is no `docs/design` or `docs/validation` folder.

Link to the owner of a fact instead of duplicating it. Phase history records
what changed/why, date and source SHA; use Git/PRs for full retrospective diffs.
