# AI Assistant documentation index

## Start here

1. **[Project map](./PROJECT_MAP.md)** — six visual views: current context, code responsibilities, data flow, agent sequence, delivery foundation and planned hybrid topology.
2. **[Implementation status](./IMPLEMENTATION_STATUS.md)** — merged work, current blockers and continuation. Check the active PR for work not yet merged; PR #12's newer checklist is not automatically this file's state.
3. **[Documentation guide](./DOCUMENTATION_GUIDE.md)** — ownership, proposed/in-review/merged/deployed distinctions, maintenance and known reference discrepancies.

Baseline for this navigation pass: dev `234a23d`, 2026-10-09. No files are moved or removed. A roadmap is not a shipped-feature list; historical test results are not today's checks.

## Reading paths

| Goal | Read in order |
| --- | --- |
| Understand the project | PROJECT_MAP → PRODUCT_PLAN → ASSISTANT_ARCHITECTURE |
| Continue engineering work | IMPLEMENTATION_STATUS + current PR → ROADMAP_INTEGRATED → relevant subsystem reference |
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

## Delivery work — status and component evidence

- **[Implementation status](./IMPLEMENTATION_STATUS.md)** — merged foundation versus unactivated end-to-end retry.
- **[Retry design](./design/digest_delivery_retry_plan.md)** — intended recovery/acceptance; not proof of complete activation.
- **[Schema unit](./design/digest_delivery_state_schema_review.md)** — nullable checkpoint field/migration and historical checks.
- **[Checkpoint contract](./design/digest_checkpoint_contract_handoff.md)** — versioned metadata/state machine.
- **[Single-part transport](./design/digest_single_part_transport_handoff.md)** — opt-in transport component.
- **[HTML parts](./design/digest_html_parts_handoff.md)** — deterministic part boundaries/hash verification.
- **[Checkpoint store](./design/digest_checkpoint_store_handoff.md)** — locks, persistence and ownership checks.
- **[PR #12](https://github.com/Starck43/social-media-ai/pull/12)** — at this baseline draft/in review; atomic fresh snapshot and newer task checklist, not merged.

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
- **[Deployment](./DEPLOYMENT.md)** — operating examples; validate against release gates before production.
- **[CLI](./CLI.md)** — operator commands.
- **[API](./API.md)** — REST contracts.
- **[Admin](./ADMIN.md)** — administration/authentication/CSRF.
- **[Models](./MODELS.md)** — tables/relationships; migration and some historical reference claims need the targeted corrections listed in DOCUMENTATION_GUIDE.

## Historical design, UI reviews and archive

Other files under [design/](./design/) retain their original review scope/baseline; [archive/](./design/archive/) is historical input, not the current roadmap. Do not move/delete reviews just to simplify the sidebar. Add per-topic links when a historical report supports a current decision.

Documentation organization work and checks: [handoff](./design/documentation_organization_handoff.md).
