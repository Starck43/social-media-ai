# Documentation router

First session: [README](../README.md), [DESIGN](product/DESIGN.md), [AGENTS](../AGENTS.md).
Then one [ROADMAP](../ROADMAP.md) row → its card. This index is optional and is
not a reading checklist. Follow AGENTS' lazy rule; load only the section needed
for the operation, not all parents/related/history links.

| Need | Canonical document |
| --- | --- |
| Product directions / phase plans | [PRODUCT_PLAN](product/PRODUCT_PLAN.md) → [phases](phases/README.md) |
| Business modules P13–P19 (owner TZ, review, decisions) | [BUSINESS_MODULES](product/BUSINESS_MODULES.md) |
| Workspace entry points, navigation, chat layout, surface parity | [WORKSPACE_UX](product/WORKSPACE_UX.md) |
| Future deployment modes, model routing, access clients | [PLATFORM_TOPOLOGY](product/PLATFORM_TOPOLOGY.md) |
| Role / short task prompts | [PROMPT_EXAMPLES](process/PROMPT_EXAMPLES.md) → selected shared skill |
| Claim, READY, audit, synchronization, cleanup | [COLLABORATION](process/COLLABORATION.md), selected section |
| Chat tools / sessions / memory | [AGENT](agent/AGENT.md) |
| Jobs / schedules / typed task plans | [AGENT_TASKS_PLAN](agent/AGENT_TASKS_PLAN.md) |
| Collection / selection / VK authorization | [COLLECTION](connectors/COLLECTION.md), [VK application](connectors/VK_STANDALONE_APP.md) |
| Structured analysis / LLM contracts | [AI pipeline](agent/AI_PIPELINE_AND_PROMPTS.md) |
| Analytics / reports / delivery | [Analytics](business/ANALYTICS_AGGREGATION_SYSTEM.md), [DIGEST](business/DIGEST.md), [CHANNELS](connectors/CHANNELS.md), [NOTIFICATIONS](business/NOTIFICATIONS.md) |
| Identity / ownership / permissions / cutover | [TENANCY](platform/TENANCY.md), [ACCESS_MIGRATION](platform/ACCESS_MIGRATION.md) |
| Tariffs / module entitlements / subscriptions | [ENTITLEMENTS](platform/ENTITLEMENTS.md), [SUBSCRIPTIONS](platform/SUBSCRIPTIONS.md); planned user-level subject and shared allowances: [P2-T17](tasks/p2-t17.md) |
| Contacts, identities, consent and contact privacy | [CONTACTS](business/CONTACTS.md) |
| ORM / deletion | [MODELS](agent/MODELS.md), [MODEL_DELETION](agent/MODEL_DELETION.md) |
| Local file connector / private file workspace contracts | [FILE_WORKSPACE_ROADMAP](file-workspace/FILE_WORKSPACE_ROADMAP.md) |
| Interfaces | [API](platform/API.md), [CLI](platform/CLI.md), [ADMIN](platform/ADMIN.md) |
| Environment / deployment | [CONFIGURATION](platform/CONFIGURATION.md), [DEPLOYMENT](platform/DEPLOYMENT.md) |
| Read-only operator inventories (membership, source ownership) | [OPERATOR_INVENTORIES](platform/OPERATOR_INVENTORIES.md) |
| Test procedures / reusable fixtures | [tests/AGENTS](../tests/AGENTS.md), [shared test presets](../tests/shared/README.md) |
| Release criteria | [BUSINESS_PRODUCTION_READINESS](product/BUSINESS_PRODUCTION_READINESS.md) |
| Earlier deliveries / scaling decisions | [IMPLEMENTATION_STATUS](archive/IMPLEMENTATION_STATUS.md) → phase/audit/PR evidence |

## Folders

Top-level `docs/` holds only this router; current contracts are grouped by subject:

| Folder | Contents |
| --- | --- |
| `product/` | What the product is meant to do: [PRODUCT_PLAN](product/PRODUCT_PLAN.md), [BUSINESS_MODULES](product/BUSINESS_MODULES.md), [WORKSPACE_UX](product/WORKSPACE_UX.md), [PLATFORM_TOPOLOGY](product/PLATFORM_TOPOLOGY.md), [DESIGN](product/DESIGN.md), [BUSINESS_PRODUCTION_READINESS](product/BUSINESS_PRODUCTION_READINESS.md), [PACKAGE_BOUNDARIES](product/PACKAGE_BOUNDARIES.md). |
| `platform/` | Core runtime, identity and interfaces: tenancy/access/entitlements/subscriptions, privacy, config, deployment, API/CLI/admin, capacity and operator inventories, workspace offboarding. |
| `agent/` | Chat agent, tasks and queue, prompts/LLM contracts, ORM and deletion behavior. |
| `connectors/` | External sources and authorization: collection, channels, VK application, connector accounts/config/seams, Telegram session isolation, local connector protocol. |
| `file-workspace/` | Private file workspace and local file connector contracts (entry point: [FILE_WORKSPACE_ROADMAP](file-workspace/FILE_WORKSPACE_ROADMAP.md)). |
| `business/` | Communication and business modules: contacts, notifications, digest, analytics, action activation/audit. |
| `process/` | How agents and owners work: [COLLABORATION](process/COLLABORATION.md), [PROMPT_EXAMPLES](process/PROMPT_EXAMPLES.md). |
| `phases/`, `tasks/`, `audit/`, `checks/`, `archive/` | Plans, active detail, accepted evidence, one-off check packages and closed history — see below. |

## File ownership

- `docs/phases/`: active phase plans and compact completed summaries; each links
  its product parent, active tasks and accepted audits. No task-status copies.
- `docs/tasks/`: active task/question detail, parent/related links, documents read,
  checks and PR handoff. ROADMAP alone owns open status/owner/dialogue. A card
  leaves this folder once its row closes (see below).
- `docs/audit/`: accepted task summaries, blocked diagnostics and lazy historical
  evidence, grouped by phase (`pN/`), questions (`q/`), `history/` and `shared/`;
  see its [index](audit/README.md). Accepted orphan tasks leave only an audit here.
- `docs/archive/`: history that current work no longer needs — superseded routers,
  closed one-off reviews/validations and completed task cards
  (`archive/tasks/<id>.md`) whose audit already carries the result. Nothing here
  is a current contract.
- Technical contracts live in the subject folders above; shared test JSON is in
  `tests/shared/fixtures/`. There is no `docs/design` or `docs/validation` folder.

Link to the owner of a fact instead of duplicating it. Phase history records
what changed/why, date and source SHA; use Git/PRs for full retrospective diffs.

## Mechanical reference and test resources

- [Generated OpenAPI/Typer tables](generated/README.md) — reproducible metadata,
  not policy or execution permission.
- [Test resources and relocation map](../tests/README.md) — explicit offline,
  core-template, browser and DB/external boundaries; audit history remains intact.
