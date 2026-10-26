# AI Assistant

An extensible assistant for evidence-backed research and controlled business
workflows. Social-media monitoring is the first domain: authorized VK/Telegram
collection, scenario-based analysis, scheduled jobs, digests and Telegram/MAX/web
chat. Product direction makes the AI chat the primary workspace for clarifying
requests, previewing and launching work, checking progress/results and following
up; completing that journey is tracked in [P8-T39](docs/tasks/p8-t39.md).
Dedicated source, scenario, task, schedule and digest pages remain directly
accessible for setup and management. MAX transport does not imply MAX collection;
planned integrations are not shipped features. Source integration alone does
not certify production readiness.

The application is a Python modular monolith: FastAPI, SQLAlchemy, Alembic and
PostgreSQL. `python -m app.runtime` owns scheduler, worker and messenger listeners;
API/admin may run separately. Do not start duplicate runtime owners.

## Development hierarchy

[Architecture](docs/DESIGN.md) → [product directions](docs/PRODUCT_PLAN.md) →
[numbered phases](docs/phases/README.md) → [ROADMAP](ROADMAP.md) → one active
`docs/tasks/<id>.md` → accepted `docs/audit/<id>.md`.

**First session:** README, [DESIGN](docs/DESIGN.md), [AGENTS](AGENTS.md).
**Task:** one queue row/card and its selected skill; then only relevant links.
[Prompt examples](docs/PROMPT_EXAMPLES.md) cover local/remote work and PR audit.
[Subsystem index](docs/DOCS_INDEX.md) routes API, models, setup and release gates.

## Run locally

Python 3.12+, PostgreSQL 14+, a private `.env` and configured encryption keys are
required. Use [CONFIGURATION](docs/CONFIGURATION.md) for settings and
[DEPLOYMENT](docs/DEPLOYMENT.md) for supported setup/migrations/runtime commands.
Configure providers/models through the supported admin interfaces; external
publication remains gated. Never infer permission to change a public/production
schema from a quick-start command; follow [database policy](docs/COLLABORATION.md#database).
