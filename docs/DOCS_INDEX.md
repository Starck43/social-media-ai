# AI Assistant documentation index

## Product direction and target architecture

- **[Product vision](./design/vision.md)** — universal assistant principles; social monitoring as the first domain, not the product boundary.
- **[Product plan](./PRODUCT_PLAN.md)** — research, knowledge search, communication, follow-up, calculations, cases and approvals; current versus planned capabilities and phased acceptance gates.
- **[Target assistant architecture](./ASSISTANT_ARCHITECTURE.md)** — proposed connector/workflow/evidence contracts, local computer boundary, controlled outbox and scaling path. Not implemented schema/API contracts.

Product expansion supplements, rather than replaces, the readiness-first roadmap below. Reference documents describe current behavior; historical reviews retain their original baselines. A product plan is not a production certification.

## Business readiness: current decisions

- **[Implementation status / start here](./IMPLEMENTATION_STATUS.md)** — merged work, open checklists, current blocker, validation and new-session continuation.
- **[Digest retry checkpoint plan](./design/digest_delivery_retry_plan.md)** — proposed receipt storage, schema approval boundary and acceptance; not implemented.

- **[Integrated roadmap](./ROADMAP_INTEGRATED.md)** — proposed sequence, small PRs and launch gates; reviewed against dev, not a production certification.
- **[Proposal review](./design/proposal_review.md)** — dispositions of research proposals, code evidence and stale assumptions.
- **[Pre-merge delta recheck](./design/business_readiness_recheck_2026_10_08.md)** — dev changes considered before the earlier documentation merge.
- **[Tenant-safe delivery implementation](./design/tenant_safe_digest_delivery_review.md)** — routing fix, recipient setup compatibility, checks and remaining retry/concurrency work.
- **[Local experience plan](./LOCAL_EXPERIENCE_PLAN.md)** — bounded onboarding, trust, recovery and report improvements; migration boundaries marked. This is application UX, not the future local file connector.
- **[Business production readiness](./BUSINESS_PRODUCTION_READINESS.md)** — security, cost, delivery, deployment, retention, recovery and support gates.
- **[Future scale strategy](./design/future_scale_strategy.md)** — deferred embeddings, alerts, billing and capacity work with entry criteria; use alongside the new product expansion plan.

## Runtime and integration references

- **[API Reference](./API.md)** — REST endpoints, request/response schemas and authentication.
- **[CLI Reference](./CLI.md)** — task, digest, credential and role commands.
- **[Admin Panel](./ADMIN.md)** — sqladmin pages, actions, authentication and CSRF.
- **[Data Models](./MODELS.md)** — tables, fields, relations and diagrams.
- **[Configuration](./CONFIGURATION.md)** — environment variables, example configuration and keys.
- **[Channels](./CHANNELS.md)** — Telegram/MAX listener, ingest and routing.
- **[Notifications](./NOTIFICATIONS.md)** — workspace-owned recipients and fixed operator alerts.
- **[Deployment](./DEPLOYMENT.md)** — deployment examples, backups and first-deployment checklist; verify against readiness requirements before production use.

## Analysis and monitoring

- **[AI pipeline and prompts](./AI_PIPELINE_AND_PROMPTS.md)** — structured analysis, PromptBuilder and media types.
- **[Analytics aggregation](./ANALYTICS_AGGREGATION_SYSTEM.md)** — processing and storage.
- **[Analytics chains](./ANALYTICS_CHAINS.md)** — relevance filters, grouping, web navigation and digests.
- **[Prompt and scope behavior](./PROMPT_AND_SCOPE_EXPLAINED.md)** — PromptBuilder and generated schemas.
- **[Collection and platform credentials](./COLLECTION.md)** — API/session layers, vault, OAuth and Telegram collection limitations.

## Scheduling, delivery and agent

- **[Task runner and queue](./AGENT_TASKS.md)** — PostgreSQL cron schedules and jobs.
- **[Digest delivery](./DIGEST.md)** — aggregation, owned recipients and remaining retry/concurrency boundaries.
- **[Chat agent](./AGENT.md)** — tool calling, sessions, memory, confirmation and current permission limitations.
- **[Scenario creation](./CHAT_BOT_SCENARIOS.md)** — methodology versus reaction, tools, templates and UX patterns.
- **[Multi-tenancy](./TENANCY.md)** — workspaces, invitations and isolation.
