# P22 — Universal tasks

[Product direction](../product/PRODUCT_PLAN.md#universal-tasks) · [Architecture and journeys](../product/UNIVERSAL_TASKS.md) · [Execution queue](../../ROADMAP.md#p22)

## Outcome

A person can save a deadline note, coordinate a team obligation, run social analysis or gather correspondence using one Task identity and one creation/editing flow. The task can acquire automation later. Operator installation, user authorization, task resources, optional LLM scenario, execution evidence and approval have explicit owners. External task systems own their issues and transitions.

## Implementation order

1. [P22-T01 — Task identity, ownership and human work](../audit/p22/p22-t01.md). DONE 2026-11-21 at `9d9ec4f3`. Canonical local tasks, participants and recurrence.
2. [P22-T02 — Connector capabilities, installations and account grants](../audit/p22/p22-t02.md). DONE 2026-11-21 at `f85ee56e`. Scoped resolver and explicit account bindings.
3. [P22-T03 — Task execution profiles, steps and resource bindings](../audit/p22/p22-t03.md). DONE 2026-11-21 at `95271ec1`. Versioned plans, scoped bindings and pinned methodology.
4. [P22-T04 — Run admission, scheduler and worker integration](../tasks/p22-t04.md). Depends on P22-T03.
5. [P22-T05 — Task actions, decisions and confirmed deletion](../tasks/p22-t05.md). Depends on P22-T01, P22-T04.
6. [P22-T06 — Legacy ID mapping, compatibility writers and backfill](../tasks/p22-t06.md). Depends on P22-T01, P22-T03, P22-T04, P22-T05.
7. [P22-T07 — Unified API, CLI and AI toolset](../tasks/p22-t07.md). Depends on P22-T03, P22-T04, P22-T05, P22-T06.
8. [P22-T08 — Unify task navigation, labels, creation and detail views](../tasks/p22-t08.md). Depends on P22-T07, P22-T14.
9. [P22-T09 — Admin connector setup and task operations](../tasks/p22-t09.md). Depends on P22-T02, P22-T05, P22-T07.
10. [P22-T10 — Correspondence task and sender-grouped Messages](../tasks/p22-t10.md). Depends on P22-T02, P22-T03, P22-T04, P22-T07.
11. [P22-T11 — External tasks and team tracker commands](../tasks/p22-t11.md). Depends on P22-T02, P22-T05, P22-T07.
12. [P22-T12 — Workspace cutover, reconciliation and legacy retirement](../tasks/p22-t12.md). Depends on P22-T06, P22-T07, P22-T08, P22-T09, P22-T10, P22-T11.
13. [P22-T13 — Jira issue adapter and Slack task context](../tasks/p22-t13.md). Depends on P22-T11.
14. [P22-T14 — Task-scoped agent chat and shared transcript UI](../tasks/p22-t14.md). Depends on P22-T03 and P22-T07. Supplies the drawer to P22-T08.

T01 and T02 supply the foundations for T03. T06 owns legacy writes and migration. T07 owns the new interface namespace and command adapters. Coordinate the legacy regression fixtures at their boundary. T08, T09, T10 and T11 can implement independent surfaces after their prerequisites. T08 uses capability fixtures until the pilots land. T12 integrates the verticals. T13 extends the provider set independently.

## Baseline examined for planning

- [Current workspace flow](../product/WORKSPACE_UX.md#tasks-as-the-universal-tool), [P20-T10 acceptance](../audit/p20/p20-t10.md), [planner phase](p16-planner-calendar.md), [social plans](../agent/AGENT_TASKS_PLAN.md) and [queue](../agent/AGENT_TASKS_QUEUE.md).
- [Connection storage](../connectors/CONNECTOR_CONFIGURATION.md), [source authority](../audit/q/q-21-source-authority.md), [product structure](../product/DESIGN.md), [documentation router](../DOCS_INDEX.md), [README](../../README.md), [business-module contracts](../product/BUSINESS_MODULES.md), [shared rules](../../AGENTS.md) and [collaboration](../process/COLLABORATION.md#tasks).
- PlannerTask, AgentTask, Job, AgentScenario, Platform, WorkspaceConnection, SourceObject, UnifiedMessage, MessageThread and BusinessCommand model definitions. Planner visibility and completion, linked assignments, social credentials and provider task projections informed the design.

Open cards describe the remaining implementation and checks. Accepted entries link to their review and verification evidence.
