# Product vision: universal AI Assistant

## Direction

**AI Assistant turns distributed information into evidence-backed business decisions and controlled actions.** Users express an outcome, choose authorized sources and review a compact plan instead of manually searching many services. Social monitoring is the first working domain; research, personal knowledge search, communication, follow-up and case workflows are the expansion direction.

The product name remains **AI Assistant** (ИИ Ассистент); the repository remains `social-media-ai`. This document replaces the former social-only vision. It describes direction, not feature availability or production certification.

Detailed capability plan and delivery phases: [PRODUCT_PLAN](../PRODUCT_PLAN.md). Target extension contracts: [ASSISTANT_ARCHITECTURE](../ASSISTANT_ARCHITECTURE.md). Reliability priorities: [ROADMAP_INTEGRATED](../ROADMAP_INTEGRATED.md) and [BUSINESS_PRODUCTION_READINESS](../BUSINESS_PRODUCTION_READINESS.md).

## Universal process

Goal → authorized sources → collect/search → normalize/verify → analyze/calculate → propose → approve when required → deliver/execute → audit/follow-up.

The core is domain-neutral. Templates may describe competitor intelligence, a client order, procurement, customer support or document approval. Bitrix24, 1C, email, local files, YouTube and VK Video are candidate integrations, not mandatory product dependencies or implemented promises.

## Current foundation

Documentation describes:
- PostgreSQL-backed schedules/jobs, a runtime combining scheduler, worker and messenger listener;
- VK collection via API/user authorization, Telegram Bot API push and MTProto historical collection;
- scenario-based structured analysis, aggregates and source navigation;
- Telegram/MAX chat and digest delivery, workspace-scoped notifications;
- encrypted credentials, tenant-scoped data, persistent memory and feedback;
- OpenAI-compatible/Anthropic LLM clients with DB-managed provider/model configuration.

Reference contracts: [collection](../COLLECTION.md), [analysis](../AI_PIPELINE_AND_PROMPTS.md), [agent](../AGENT.md), [tasks](../AGENT_TASKS.md), [digest](../DIGEST.md), [tenancy](../TENANCY.md). Existing readiness gaps remain: confirmation is not complete authorization, current cost tracking is not a billing-grade cap, and scheduled-run bookkeeping does not prove exactly-once external delivery. MAX transport is not MAX collection.

## Principles to preserve

- Agent is an interaction/planning layer over bounded, deterministic execution; LLM output does not authorize actions or replace the scheduler.
- Scenario = methodology; task = schedule/sources/reaction/targets; report grouping = read-time projection.
- Official APIs and authorized access first. No bypass of privacy, access controls or platform rules.
- Evidence, coverage and uncertainty are visible; never market incomplete observation as full analytics.
- Authorization, budgets, tenant isolation, approvals and retries are server-side contracts.
- Calculations and commercial totals use deterministic validated arithmetic.
- Secrets never enter prompts/chats/logs. Private local and business data require explicit scopes and retention policies.
- Risky sends/deletes/business approvals require human control; signature validity requires separate legal/provider review.
- Small tested changes and measured scale, not a wholesale microservice rewrite.

## Evolving assumptions

A single VPS and PostgreSQL queue are the current starting topology, not permanent scale limits. MCP, authorization brokers, exports, embeddings and additional infrastructure are evaluated by demonstrated need, security, cost and operability rather than prohibited categorically.

Do not revive frozen Celery/Redis or Streamlit as the default runtime. Existing rights remain relevant; fail-closed authorization must be strengthened, not discarded as legacy RBAC. Do not fine-tune models or introduce an autonomous LLM scheduler without a separate evidence-backed decision.

Monitoring's staged content is not a permanent raw archive. Future correspondence/case/document history requires explicit purpose-bound storage and deletion rules. Shared hosting and business workflows do not imply permission to retain everything forever.

## Delivery order

Close existing safety/operability gates → trustworthy monitoring pilot → evidence-backed research expansion → authorized local/mail knowledge search → verified unified communication/outbox → reminders and audits → configurable cases/calculations → versioned remote approval/signature integration → measured B2B/self-service scale.

Each step needs a bounded pilot and acceptance evidence. The expanded vision does not supersede the readiness-first order or authorize application changes.
