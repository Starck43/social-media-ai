# AI Assistant documentation index

This index lists the canonical project documentation. Git history and pull requests
own historical reviews, implementation narratives and full test transcripts.

## Development reading path

1. [Product plan](PRODUCT_PLAN.md) — product outcomes and delivery phases.
2. [Business readiness](BUSINESS_PRODUCTION_READINESS.md) — stable engineering and release gates.
3. [Current work](design/README.md) — the only prioritized queue and ownership board.
4. Open one subsystem document relevant to the selected task.

Use [implementation status](IMPLEMENTATION_STATUS.md) only to identify delivered
foundations and their PRs. Verify current behavior in code, migrations and tests.

## Product and architecture

- [Project map](PROJECT_MAP.md) — current runtime, repository boundaries and data flow.
- [Product plan](PRODUCT_PLAN.md) — intended capabilities and delivery order.
- [Target architecture](ASSISTANT_ARCHITECTURE.md) — proposed extension contracts.
- [Business readiness](BUSINESS_PRODUCTION_READINESS.md) — acceptance and release gates.

## Runtime subsystems

- [Agent](AGENT.md) — interactive sessions, tools and permission boundaries.
- [Agent tasks](AGENT_TASKS.md) — PostgreSQL schedules and jobs.
- [Collection](COLLECTION.md) — sources, credentials and collection limits.
- [AI pipeline](AI_PIPELINE_AND_PROMPTS.md) — scenarios, prompts, typed outputs and media coverage.
- [Analytics](ANALYTICS_AGGREGATION_SYSTEM.md) — aggregation, chains and drill-down behavior.
- [Digest](DIGEST.md) — report generation and delivery state.
- [Channels](CHANNELS.md) — Telegram and MAX runtime boundaries.
- [Notifications](NOTIFICATIONS.md) — tenant recipients and operator alerts.
- [Tenancy](TENANCY.md) — workspaces, membership and isolation.

## Interfaces and operation

- [Models](MODELS.md) — ORM and migration-backed schema reference.
- [API](API.md), [CLI](CLI.md) and [Admin](ADMIN.md) — supported interfaces.
- [Configuration](CONFIGURATION.md) and [Deployment](DEPLOYMENT.md) — setup and operation.
- [Collaboration](COLLABORATION.md) — local and remote development handoff.
- [Agent workflow](../AGENTS.md) and [workflow details](../.agent/workflow.md) — concise instructions, audit and delegation lifecycle.
- [Current work](design/README.md) and [implementation status](IMPLEMENTATION_STATUS.md) — allocation and delivery index.

## Maintenance rule

Each fact has one owner: product direction in `PRODUCT_PLAN.md`, target boundaries
in `ASSISTANT_ARCHITECTURE.md`, readiness criteria in
`BUSINESS_PRODUCTION_READINESS.md`, current allocation in `design/README.md`, and
subsystem behavior in its subsystem document. Link to the owner instead of copying
its text. Keep detailed evidence in Git and PRs.

## Source audit reports

- [2026-10-15 dev audit](audits/2026-10-15-dev.md) — source-pinned findings,
  scope limits and ten TODO acceptance contracts; no implemented-fix or release claim.
