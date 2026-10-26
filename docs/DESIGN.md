# Project architecture

AI Assistant is a modular Python monolith for authorized collection, evidence-based
analysis and controlled business actions. Social monitoring is its first vertical.
[README](../README.md) describes the product; [PRODUCT_PLAN](PRODUCT_PLAN.md)
owns future directions and links every [phase plan](phases/README.md).

## Execution model

The AI chat is the primary user workspace across request, clarification,
preview, execution, status, results and follow-up. Dedicated source, scenario,
task, schedule and digest pages remain directly accessible for setup and
management. This is the target interaction contract; completion is tracked in
[P8-T37](tasks/p8-t37.md).

Chat / web / API / CLI → permission-scoped services → ORM managers → PostgreSQL.
A single `python -m app.runtime` owns scheduler, worker and messenger listeners;
API/admin may run separately. Jobs carry bounded execution state; external
collection, model calls and delivery remain authorized adapter operations.
PostgreSQL/SQLAlchemy/Alembic own persistence; Celery/Redis and Streamlit are frozen.

| Responsibility | Code entry | Contract (read only when needed) |
| --- | --- | --- |
| Runtime / scheduling / queue | `app/runtime.py`, `app/worker.py`, `app/jobs/`, `app/tasks/` | [AGENT_TASKS](AGENT_TASKS.md) |
| HTTP / web / admin / CLI | `app/main.py`, `app/api/`, `app/web/`, `app/admin/`, `cli/` | [API](API.md), [ADMIN](ADMIN.md), [CLI](CLI.md) |
| Conversation / tools / memory | `app/agent/` | [AGENT](AGENT.md) |
| Sources / collection / analysis | `app/services/monitoring/`, `app/services/social/`, `app/services/ai/` | [COLLECTION](COLLECTION.md), [AI pipeline](AI_PIPELINE_AND_PROMPTS.md) |
| Reports / delivery | `app/services/digest/`, `app/services/notifications/`, `app/channels/` | [DIGEST](DIGEST.md), [NOTIFICATIONS](NOTIFICATIONS.md), [CHANNELS](CHANNELS.md) |
| Data / access | `app/models/`, `app/schemas/`, `app/services/tenancy/`, `app/core/` | [MODELS](MODELS.md), [TENANCY](TENANCY.md) |

## Development hierarchy

Product direction → numbered phase plan → active task card → accepted audit summary.
[ROADMAP](../ROADMAP.md) is the short open-work index, not a plan or transcript.
Plans link tasks; cards link parents, related tasks, relevant contracts and checks.
Acceptance replaces a plan's task link with its audit summary. Historical evidence
is lazy-loaded from audits or Git; it never proves behavior at a newer revision.

Scale within the monolith until measured queue age, latency, storage, provider
budgets or isolation requirements justify a change. Record significant changes
in a numbered phase/revised plan; small independent tasks need no invented phase.
The [subsystem index](DOCS_INDEX.md) routes other contracts, release gates and setup.
