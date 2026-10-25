# AI Assistant

An extensible assistant for evidence-backed research and controlled business
workflows. Social-media monitoring is the first domain: authorized VK/Telegram
collection, scenario-based analysis, scheduled jobs, digests and Telegram/MAX/web
chat. MAX transport does not imply MAX collection; planned integrations are not
shipped features. Source integration alone does not certify production readiness.

The application is a Python modular monolith: FastAPI, SQLAlchemy, Alembic and
PostgreSQL. `python -m app.runtime` owns scheduler, worker and messenger listeners;
API/admin may run separately. Do not start duplicate runtime owners.

## Development hierarchy

[Product directions](docs/PRODUCT_PLAN.md) → [open queue](ROADMAP.md) → one
`docs/tasks/<id>.md` card → only its linked subsystem/design/evidence sections.
Parent phase files retain accepted task history; `docs/phases/` holds closed phases.

**First session:** read this README, [AGENTS](AGENTS.md), then one selected skill.
**Each task:** open its card; record documentation actually read and checks run.
Do not preload all overview or subsystem files.

| Need | Read only when relevant |
| --- | --- |
| Short prompts: remote work, local DB help and PR audit | [PROMPT_EXAMPLES](docs/PROMPT_EXAMPLES.md) |
| Find code and runtime responsibilities | [PROJECT_MAP](docs/PROJECT_MAP.md) |
| Product scope / proposed extension contracts | [PRODUCT_PLAN](docs/PRODUCT_PLAN.md) |
| Claim, evidence, acceptance, delegation, DB or cleanup procedure | [COLLABORATION](docs/COLLABORATION.md), selected section |
| Release / activation acceptance | [BUSINESS_PRODUCTION_READINESS](docs/BUSINESS_PRODUCTION_READINESS.md), selected gate |
| Audit previously delivered work | [IMPLEMENTATION_STATUS](docs/IMPLEMENTATION_STATUS.md), selected area |
| API, models, tenancy, collection and other contracts | [DOCS_INDEX](docs/DOCS_INDEX.md), then one subsystem |

## Run locally

Python 3.12+, PostgreSQL 14+, a private `.env` and configured encryption keys are
required. Use [CONFIGURATION](docs/CONFIGURATION.md) for settings and
[DEPLOYMENT](docs/DEPLOYMENT.md) for supported setup/migrations/runtime commands.
Configure providers/models through the supported admin interfaces; external
publication remains gated. Never infer permission to change a public/production
schema from a quick-start command; follow [database policy](docs/COLLABORATION.md#database).
