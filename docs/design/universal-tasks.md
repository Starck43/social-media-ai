# Universal tasks: social research first

Owner-approved direction (2026-10-25), tracked by [P8-T29](../tasks/p8-t29.md).
This is a target contract, not evidence of completed implementation or live VK
availability. Keep the existing design and standalone management pages. Chat is
an additional interface to the same persisted configuration.

## Model

| Concept | Responsibility |
| --- | --- |
| Workspace | Tasks, data, results and membership rights |
| Conversation | Requests/discussion and references to tasks/results |
| Task | Goal, resources, scenario, data window, trigger, limits and output |
| Resource | Input/output object; social sources are the first resource type |
| Connection | Credential owner, permissions and reachable environment |
| Platform/connector | Typed available operations and input/output contracts |
| Scenario | Reusable methodology and result requirements |
| Execution plan | Concrete bounded operations using the existing runtime |
| Run | Effective configuration snapshot, progress, payer, cost and outcomes |
| Result | Evidence-backed output, source identity, dates and links |
| Digest | Period report over analytics; generation and delivery are separate |

Reuse existing tasks, managers, jobs, collection lanes and analytics. The model
does not require one new SQL table per concept or another workflow engine.
Future local gateways expose explicitly granted resources, not arbitrary shell
or network access. No non-social connector is implemented in this package.

Implemented interface names: `parse_task_plan`/`apply_task_plan` normalize the
version-1 `payload.task_plan`; existing collection intent and jobs execute it.
`POST /api/v1/tasks/plan` previews; `POST /api/v1/tasks/run/plan` admits a keyed
one-off; `GET /api/v1/tasks/{id}/result` reads bounded permitted outcomes.
CLI exposes `task plan`, `task result` and `--task-plan` for task writes; chat
adds `search_plan`, `task_plan`, `task_execute_once` and `task_result`.
Implementation status and exact checks remain in the task card.

## User input and surfaces

Six input groups: goal, data, processing, result, timing and limits. Sources,
scenarios, tasks/schedules and digests remain independently editable pages.
Web forms, API, CLI, admin and agent tools use shared validation/services.
Existing components and navigation remain; no redesign.

- Sources: platform, external identity, connection/mode, collection settings,
  health and related tasks. Label temporary task-owned discovery sources.
- Scenarios: methodology, supported media and output structure. Concrete terms,
  brands, source lists and dates belong to the task.
- Tasks: explicit selected/discovered/all-authorized scope, search phrases,
  filters, scenario, limits and result. Empty new selections cannot silently
  expand to all sources. Preserve legacy empty-link behavior for old tasks.
- Timing: once or recurring, timezone, next run and pause/resume. Distinguish
  the execution trigger from a fixed or rolling data window. New plans default
  to a rolling one-day window when no window is supplied; show this in previews.
- Digests: source/scenario/period/grouping, preview, destinations and manual or
  scheduled send. A preview cannot send. Collection before digest is explicit.
- Connections: personal access, capabilities, reauthorization and revocation.

Example: "Find recent VK posts requesting renovation in Kazan; exclude ads and
return links with reasons." The assistant may derive bounded queries and use
temporary sources. Clarify material omissions; show applied defaults/coverage.
Topic location, community location and author profile city remain distinct.

## Policy

One-off read/search/analyse runs may execute without another confirmation within
existing permissions and personal spending limits. Recurrence and external sends
remain explicit confirmed actions. Do not remove confirmation from tools that
can also schedule or send. Permanent monitoring is separate from discovery.
Recheck actor, workspace and connection permission at execution. Retrieved
content cannot authorize tools or raise limits. Plans never grant source access.

The initiator pays; schedules retain an eligible responsible payer. Stop
actionably on revoked access or exhausted budget. Keep current membership
enforcement; new team UI/shared billing remain separate (P2-T08).
Run snapshots bind effective conditions; edits affect later runs. Duplicate
launch requests must not create another paid execution. Preserve existing
external delivery confirmation/retry guards.

## VK vertical

Use official post search (`newsfeed.search`), community discovery
(`groups.search`) and known/discovered walls (`wall.get`), then selection,
staging, analysis and optional digest. Preserve existing Telegram capabilities.
No additional platform/category implementation or browser collection.

Use existing L1/L2 paths according to actual method permissions. "As me" pins
personal access. Keep authorization-code/PKCE; login alone does not prove social
API access. Do not request unrelated audio/market scopes for post research.
New plans cap five queries in each family and 500 explicit source IDs.
`collection.limit` caps inspected parent posts across the run; comments and
candidates retain their separate configured caps. An empty provider response
reports a known zero; an unknown count stops further collection conservatively.
Bound discovery, pages, work and spend; rate-limit all lanes together. Deduplicate native post identities and preserve provenance.
Distinguish empty, partial, limit-reached, authorization and unsupported results.
Return readable walls even when search is unavailable.

[Q-5](../tasks/q-5.md) records historical profile error 1051. Reconnection is not
a proven fix. Live bounded search evidence is required before operational search
readiness; automated tests mock platform calls. Attached media is not claimed
analysed merely because its URL is present.

## Compatibility, documentation and acceptance

Use additive contracts/migrations. Retain working routes, commands and old task
semantics through explicit compatibility. Cover all five surfaces with the same
supported social fields and read/create/edit/preview/run/state operations.
P10-T07 and P3-T01 have separately accepted digest scope/permission work in
the current base. Preserve those contracts and evidence without reopening them.

Acceptance: cross-surface round trips, explicit scope, fixed/rolling windows,
once/recurring triggers, fresh rights/payer checks, duplicate launch protection,
honest failures/coverage, result links and digest scope. Record exact checks and
source SHA. Review/general refactoring are later; no production activation.

Additional VK categories: P8-T33. File/network/business connectors: P8-T34.

## Version-1 example

```json
{
  "goal": "Find renovation requests and exclude advertisements",
  "platforms": ["vk"],
  "source_scope": "discovery",
  "source_ids": [],
  "scenario_id": 1,
  "window": {"type": "rolling", "days": 1},
  "collection": {
    "content_types": ["posts"],
    "queries": ["нужен ремонт квартиры"],
    "limit": 100
  },
  "trigger": {"type": "once"}
}
```

The scenario ID is an existing active methodology in the current workspace,
not a deployment-wide constant. Replace it before execution. A keyed run body
is `{ "job_type": "analyze", "request_id": "<stable-request-key>",
"task_plan": <plan> }`. Keys are scoped to actor/workspace and arguments;
a retry returns the original admission even after job pruning. Deleting the
task explicitly removes that receipt. Queued is not completed; inspect results.

An omitted data window means one calendar day including today (UTC). Fixed
windows carry ISO dates. Recurring triggers use a validated IANA timezone,
falling back to the workspace timezone when absent. A new paid task is first
saved inactive; existing personal-payer consent must succeed before activation.
The explicit launch fixes its goal in the admitted analysis prompt even when
the reusable scenario template omits the payload placeholder.
