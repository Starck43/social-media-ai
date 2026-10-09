# AI Assistant documentation index

## Development: one three-level reading path

1. **[Global plan](./PRODUCT_PLAN.md)** — what the product is intended to achieve.
2. **[Engineering stages](./ROADMAP_INTEGRATED.md)** — A–G order and exit gates.
3. **[Current-stage tasks and design catalog](./design/README.md)** — occupied PRs,
   merged bounded work, independent candidates and topic-specific evidence.

Supporting references: [project map](./PROJECT_MAP.md),
[integration/evidence ledger](./IMPLEMENTATION_STATUS.md),
[documentation maintenance rules](./DOCUMENTATION_GUIDE.md).

Navigation cleanup merged in PR #26 as `10212b5e`, 2026-10-09. A roadmap is not a shipped-feature
list; owner reports, historical counts and current acceptance are distinct.
Active PR #22 owns its shared status/next-session edits; do not overwrite it.

## Reading paths

| Goal | Read in order |
| --- | --- |
| Understand the project | PROJECT_MAP → PRODUCT_PLAN → ASSISTANT_ARCHITECTURE |
| Continue engineering work | PRODUCT_PLAN → ROADMAP_INTEGRATED → design/README + active PR → relevant contract |
| Evaluate cloud/hybrid installation | DEPLOYMENT_ARCHITECTURE → BUSINESS_PRODUCTION_READINESS → CONFIGURATION / DEPLOYMENT |
| Configure monitoring | COLLECTION → CHAT_BOT_SCENARIOS → AGENT_TASKS → DIGEST |
| Review isolation/security | TENANCY → AGENT → NOTIFICATIONS → production gates |
| Investigate schema/API | MODELS + actual model/migration source → API / CLI; see known discrepancies |

## Product direction and target architecture — plans

- **[Product vision](./design/vision.md)** — universal assistant principles; social monitoring is the first domain.
- **[Product plan](./PRODUCT_PLAN.md)** — capability areas, phased expansion and current/planned separation.
- **[Target assistant architecture](./ASSISTANT_ARCHITECTURE.md)** — connector/workflow/evidence boundaries; not implemented schema/API contracts.
- **[Cloud and hybrid deployment plan](./DEPLOYMENT_ARCHITECTURE.md)** — personal/server connectors, local models, data policy and later autonomous installation; not an implemented deployment profile.
- **[Deployment plan handoff](./design/deployment_architecture_handoff.md)** — historical preparation/checks; current merge status comes from GitHub/tracker.
- **[Product direction handoff](./design/product_direction_handoff.md)** — prior delivery and its continuation.

## Delivery work — merged scope versus acceptance

PR #12 integration is merged, not the old "next task". Default-off delivery,
activation and end-to-end acceptance remain distinct. Use the
[implementation ledger](./IMPLEMENTATION_STATUS.md) and
[delivery catalog](./design/README.md#delivery-contracts-and-acceptance-evidence)
for original contract/schema/transport/parts/store/snapshot/job evidence.
Do not infer new passing tests or live sender activation from a merge.

## Business readiness and prioritization

- **[Integrated roadmap](./ROADMAP_INTEGRATED.md)** — small work packages and readiness-first order.
- **[Business production readiness](./BUSINESS_PRODUCTION_READINESS.md)** — security, costs, operation, retention and acceptance gates.
- **[Local experience plan](./LOCAL_EXPERIENCE_PLAN.md)** — application UX, not the local file connector.
- **[Future scale strategy](./design/future_scale_strategy.md)** — optional investments with entry criteria.
- **[Proposal review](./design/proposal_review.md)** — earlier research dispositions and code evidence.
- **[Pre-merge delta recheck](./design/business_readiness_recheck_2026_10_08.md)** — historical baseline verification.
- **[Tenant-safe delivery review](./design/tenant_safe_digest_delivery_review.md)** — bounded routing evidence; not the whole retry gate.

## Current subsystem references — verify against source

- **[Agent](./AGENT.md)** — tools, sessions, memory, confirmation and known permission limits.
- **[Task runner and queue](./AGENT_TASKS.md)** — PostgreSQL schedules/jobs.
- **[Collection and credentials](./COLLECTION.md)** — API/session layers, vault, OAuth and platform limitations.
- **[Digest delivery](./DIGEST.md)** — report generation, recipients and delivery constraints.
- **[Channels](./CHANNELS.md)** — Telegram/MAX listeners and transports.
- **[Notifications](./NOTIFICATIONS.md)** — owned workspace recipients and fixed operator alerts.
- **[Multi-tenancy](./TENANCY.md)** — workspaces, invites and isolation.
- **[Scenario creation](./CHAT_BOT_SCENARIOS.md)** — methodology versus reaction, templates and tools.
- **[AI pipeline and prompts](./AI_PIPELINE_AND_PROMPTS.md)** — analysis and media contracts.
- **[Analytics aggregation](./ANALYTICS_AGGREGATION_SYSTEM.md)** — processing/storage.
- **[Analytics chains](./ANALYTICS_CHAINS.md)** — chain relevance, grouping and navigation.
- **[Prompt and scope behavior](./PROMPT_AND_SCOPE_EXPLAINED.md)** — prompt/schema assembly.

## Operator and developer references

- **[Configuration](./CONFIGURATION.md)** — environment settings and keys.
- **[Deployment](./DEPLOYMENT.md)** — draft installation examples; [observation and conservative recovery](./DEPLOYMENT.md#observation-and-conservative-recovery) is the incident entry point. Changes need separate approval; neither guide nor health probe certifies production.
- **[CLI](./CLI.md)** — operator commands.
- **[API](./API.md)** — REST contracts.
- **[Admin](./ADMIN.md)** — administration/authentication/CSRF.
- **[Models](./MODELS.md)** — tables/relationships; migration and some historical reference claims need the targeted corrections listed in DOCUMENTATION_GUIDE.

## Historical design, UI reviews and archive

Use the [categorized design catalog](./design/README.md), not a directory-wide reading order. [archive/](./design/archive/) preserves historical evidence, not the current roadmap. Keep active contract paths stable while open PRs depend on them; archive preparation records with compatibility links instead of deleting their evidence.

Documentation organization work and checks: [handoff](./design/archive/documentation_organization_handoff.md).
