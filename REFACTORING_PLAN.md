# Refactoring plan — critical structural audit

Task: [P11-T01](docs/tasks/p11-t01.md) · Parent: [P11](docs/phases/p11-structure.md).
Reviewed application snapshot: `1a4f24648603341709ebfc8f9774b1b7cc3cc84f` (`dev`, 2026-10-27).
Resume claim: `00f530702f924d6a677d5c565a389b1e44cab88f` (registry-only).
Recovered draft: `83567d309e7e3f26e7e3f6c5ed14de9c25c341ed` on
`docs/p11-t01-refactoring-audit`; original source `03a0eea`.
This revision supersedes its R-01–R-30 numbering, including the unpublished notes
provided by the owner. Findings below were rechecked, not inherited as proof.

This is an ordered **structural plan**, not a second status registry. Existing
functional work stays in its task card (section 5). Register one bounded follow-up
at a time in [ROADMAP](ROADMAP.md); do not bulk-create implementation tasks.
No runtime, database, migrations, live/paid providers, load tests or application
pytest were run for this docs-only audit. Static inspection does not certify
production readiness or independently accept any implementation.

## 1. Executive assessment and measured baseline

Keep the modular monolith and PostgreSQL queue. The most important structural
risk is duplicated orchestration across web/API/CLI/tools: callers must remember
transaction, tenant, permission, consent and source-admission rules separately.
Size and repeated identifiers help locate seams; neither proves a defect. Do not
start with a rewrite, a mass rename, new microservices or more runtime owners.

Static Python AST/text inventory of tracked `app/**/*.py` and `cli/**/*.py`:

| Signal | Current observation | Interpretation |
| --- | --- | --- |
| Application | 435 Python files, 88,685 lines | Navigation/review cost; not a capacity measurement |
| Tests | 469 Python files, 122,268 lines | No inference that tests are excessive or redundant |
| Largest adapters | `app/web/tasks.py` 3,221; `app/admin/views.py` 2,669; `cli/main.py` 1,087 lines | Multiple responsibilities require seam review |
| Other hotspots | `app/jobs/handlers.py` 2,217; `app/services/ai/analyzer.py` 1,972; `reporting.py` 1,954; `llm_client.py` 1,641 | Refactor one tested responsibility at a time |
| Managers | `job_manager.py` 1,501; `agent_task_manager.py` 1,432; `base_manager.py` 1,422 | Persistence, orchestration and query helpers need boundaries |
| Agent tools | 40 `@tool` declarations; all 40 specify permission; 16 specify confirmation | No missing-permission exploit established; optional default is a future-registration hazard |
| API | 93 `@router` HTTP decorators under `app/api/v1` | Decorator count, not unique externally mounted route count |
| Coupling indicators | 1,783 `Any` tokens; 279 AST `except Exception` handlers; 1,333 indented app-import statements across app+CLI | Includes legitimate dynamic data, rollback guards and lazy optional imports; classify before changing |
| Formatting | 33 app/CLI files with tab-indented code | Prefer Black/isort 120 per repository, not an unapproved formatter migration |
| Workflow | No `.github/workflows` directory; 22 active task-card files | CI gap verified in this tree; not evidence that external CI does not exist |

Methods: UTF-8 `splitlines()` counts; `ast.parse` without importing application;
`ast.ExceptHandler` with `type.id == 'Exception'`; regex `\bAny\b` and `^[ \t]+(?:from|import) app\.` (MULTILINE). All 435 application/CLI files parsed successfully. These are
snapshot observations, not performance/security test results.

### Corrections to the recovered draft

- `app/core/api_scope.py` already supports `x-tenant-id` and `x-tenant-slug`,
  validates active memberships and separates account/operator routes. Do not
  propose adding workspace selection as though it were absent.
- `app/services/task_launch.py::launch_identity/launch_social_task` already
  provides tenant/actor/request-key admission and payload fingerprinting.
  Generic HTTP idempotency coverage is incomplete; task launch is not wholly
  non-idempotent. Preserve its semantics rather than add a competing receipt.
- Web task create/update and typed API/tool paths already use explicit sessions
  and `session.begin()`. The problem is uneven ownership, not absence of all
  transactions. Legacy API `tasks.py::run_task_oneoff` creates the temporary task,
  attaches sources and admits the job in separate calls without one encompassing
  transaction; decide the intended failure boundary and compensating behavior.
- `app/worker.py` is a real worker entry point, not a dead Celery artifact.
  Do not delete it or start it alongside runtime; read deployment ownership first.
- New tests must keep `test_<task-id>_<topic>.py` and shared fixtures in
  `tests/shared/` per [tests/AGENTS](tests/AGENTS.md). Do not rename by behavior
  en masse or invent an unregistered task marker. Test/app size ratio proves nothing.
- Keep audit history and its links. A hundred audit files is not a reason to
  compress/delete evidence. Duplicate helper names and local imports need semantic
  review; they do not establish shared behavior or an import cycle.
- P2-T15 code and `docs/ENTITLEMENTS.md` are integrated but validation is explicitly
  deferred. Never relocate all tenant limits into this catalog: the task explicitly
  preserves `Tenant.PLAN_LIMITS`, `effective_limits()` and the deployment member cap.

## 2. Execution order and safety rules

Priority order: **R-01 → R-02 → R-03**, then bounded R-04/R-05 alongside the owning
functional task, then interface hardening R-06–R-10. R-11–R-14 follow extracted
seams. R-15–R-18 are demand/measurement-led, not prerequisites to the VK milestone.
A reproducible authorization/atomicity defect takes precedence over cleanup.

Apply SOLID through cohesive use-cases, explicit dependencies and narrow ports;
DRY only for identical semantics; KISS/YAGNI for infrastructure. No generic
repository/DI framework, universal workflow engine or ORM rewrite is required.
Each future task must name compatibility, owned paths, checks and rollback. Keep
structural moves separate from behavior changes; preserve public paths during a
bounded transition, then retire aliases only when every consumer is migrated.
Do not hold P8-T33/T39/T40 hostage to all of P11 or require a global format pass.

### R-01 — Establish a small, reproducible safety net (first)

**Evidence:** no in-tree Actions workflow; Black/isort/mypy configured in
`pyproject.toml`; README requires Python 3.12+, package metadata still says >=3.10.
**Change:** agree on supported Python/install profiles, then CI with pinned tools,
Black/isort 120 checks, syntax/import-boundary checks, secret scanning and an
explicitly classified no-DB test subset. Inventory baseline violations without
weakening tests. Formatter replacement needs its own decision. Alembic graph
inspection must not import env/config or connect to a DB implicitly.
**Acceptance:** deterministic install and focused checks on supported Python;
no secret/DB/provider requirement for the offline lane; exceptions are narrow,
owned and non-growing. Baseline formatting only on touched/extracted modules.
**Risk/rollback:** tool/version drift and accidental DB fixtures; review fixture
imports before selecting CI tests, keep DB checks separately authorized.

### R-02 — Extract application orchestration, starting with tasks (DIP/SRP)

**Evidence:** `app/web/tasks.py::task_create/task_update`,
`app/api/v1/endpoints/tasks.py`, `app/agent/toolset/tasks.py`, `cli/main.py`
repeat task manager calls, date/source/cron handling and transaction wiring.
Shared `task_plans.py`, `task_launch.py`, `task_results.py` already exist.
**Change:** define narrow typed create/update/delete/launch query/use-case seams
around existing services, not a parallel replacement. Actor, workspace, sources,
payer and authorization are explicit. Interfaces translate transport only;
execution-time checks remain in trusted policy/services/managers. First migrate
one vertical operation through all adapters, then extend to sources/scenarios,
digests and provider catalog only as their tasks require.
**Acceptance:** same allowed/denied behavior and data-window/source/payer semantics
across five surfaces; adapter tests pin mapping, use-case tests pin business rules;
no unreviewed bypass or new service-permission grant. No naive grep prohibition on
read-only model imports. P8-T39 owns the actual task-journey/parity delivery.
**Dependency/risk:** R-01; snapshot tests before extraction; retain adapter shims
until consumers migrate. Coordinate active owned paths before editing.

### R-03 — Make transaction ownership explicit, not ambient

**Evidence:** `BaseManager._get_owned_session(session)` commits its own session
only when none is supplied; task controllers manually pass sessions, whereas
legacy `/tasks/run` assembles multiple committed calls. Agent task create also
performs payer consent after its create transaction.
**Change:** use-case owns an explicit session/UoW passed to participating managers;
retain current transaction helpers where adequate. Define which configuration,
source links, consent and enqueue steps must be atomic versus recoverable.
Never hold a DB transaction open across provider/network calls. Do not hide all
sessions in a new global ContextVar or turn missing UoW into log-only success.
**Acceptance:** inject failure at each write step, prove rollback/no orphan or a
specified compensating state; concurrent duplicate admission and tenant isolation
remain safe. DB/concurrency proof requires a later authorized test environment.
**Dependency/risk:** R-02 seam; don't change commit ownership silently or assert
that static inspection has reproduced a production race.

### R-04 — Consolidate commands, results, errors and presenters (ISP/DRY)

**Evidence:** API schemas in `app/schemas`, hand-written tool JSON Schema,
Typer/Jinja form coercion, and progress helpers in `app/web/tasks.py`,
`task_progress.py`, `analysis_progress.py` repeat transport/domain mapping.
**Change:** typed use-case commands/results, transport-specific adapters and shared
permission-filtered result builders. Generate tool parameter schemas from the
same validation contract where practical. Preserve HTTP/form/CLI compatibility;
not every API schema or form field is a domain object. Introduce a small domain
error taxonomy with boundary mappings and safe messages, preserving unknown
external outcomes. Broad catches at rollback/worker boundaries may remain.
**Acceptance:** boundary tests for missing/unknown/extra fields, limits and timezone;
no cross-tenant fields or raw provider errors; contract-version readers for saved
payloads. Existing task-plan schemas are reused, not cloned. P8-T40 owns its run
snapshot/composition schema; this item covers reusable structural seams only.
**Dependency/risk:** R-02/03; avoid schema churn and losing valid partial results.

### R-05 — Enforce real package boundaries incrementally

**Evidence:** manager imports from interfaces; input helpers in
`AgentTaskManager`; local app imports span 1,333 statements across app/CLI.
**Change:** map actual cycles and define contracts for the newly extracted
application packages first. Permit legitimate lazy optional/provider imports.
Do not declare the entire `app.agent` a transport layer or `app.core` a pure
bottom layer without examining runtime/config/model dependencies. Static
import-lint rules start with a reviewed baseline and tighten per extraction.
**Acceptance:** at least the migrated aggregate cannot import adapters from its
application core; import/import-time smoke checks preserve optional integration
startup behavior; no `# noqa` blanket hiding dependencies.
**Dependency/risk:** R-02; mechanical moves must update all re-exports and loaders.

## 3. Interface hardening (structural work not already owned by a task)

### R-06 — API compatibility, bounded lists and request policy

**Evidence:** free-text and structured `HTTPException.detail` coexist;
`tasks.py::run_task_oneoff` differs from typed plan launch; only one
`@limiter.limit` decorator found in app (admin change-password);
`app/core/api_scope.py` uses a public-path allow-list; `/test-db` legacy routes
and Q-10's doubled credential path are part of current contracts.
**Change:** inventory the externally mounted route/auth matrix, bounded list
responses, error codes and expensive operations. Add safe request correlation,
page-size bounds and per-actor/workspace admission budgets. Reuse current launch
keys; where generalized idempotency is needed define atomic key+payload binding,
TTL/replay, conflict/in-flight semantics and unknown-effect reconciliation.
Propose stable error envelope/OpenAPI snapshots and route aliases before breaking
old clients. Request correlation IDs are not idempotency keys. A multi-process
rate limiter needs an explicitly selected consistent store; do not revive Redis
by default while it is frozen. Q-10 retains its compatibility decision.
**Acceptance:** authenticated membership/selector failures, stale/revoked JWT,
unknown paths/methods, same-key concurrent replay/different-payload conflicts,
bounded lists, redaction and 429 retry behavior. Public paths never expand by a
prefix mistake. Preserve already-supported tenant headers; document versions.
**Dependency/risk:** R-02–R-04 for mutations; start read-only route contract checks
under R-01. Existing P8-T40 owns duplicate composed starts, not this generic API work.

### R-07 — Agent registration fails closed; schemas stay provider-neutral

**Evidence:** `app/agent/tools.py::tool` defaults `required_permission=None`;
`_check_tool_permission` omits the gate for None, although all 40 current tools
supply it. Registry is mutable/global; serializers are OpenAI-shaped.
**Change:** require catalog permission or a reviewed explicit open-tool policy,
validate registry names/schema at registration, derive typed inputs/results and
put provider serializers at the provider boundary. Keep existing one-use,
argument-bound confirmation checks and execution-time tenant/actor rechecks.
Use injectable registry instances only when test isolation/lifecycle requires it;
no heavyweight DI container. Explicit tool budgets and redacted metadata audit
must not store prompts, credentials or full argument payloads by default.
**Acceptance:** missing/unknown policy registration rejected, duplicate names
rejected, invalid input never invokes handler; direct/confirmed dispatch have
matching denials, replay and revoked-right tests. No new PUBLIC sentinel may
silently mean "any caller". Version public tool contracts only when exposed.
**Dependency/risk:** R-02/04; registration changes can break lazy loading.

### R-08 — Separate operator trust from customer CLI access

**Evidence:** `cli/main.py::_run_platform` intentionally uses
`tenant_scope(bypass=True)` plus `operator_permission_scope`; scoped commands
already re-enter actor/tenant contexts. This is an operator tool, not a proven
remote vulnerability. JSON output exists for selected commands, not consistently.
**Change:** label/distribute this CLI as operator-only; require explicit target
workspace for tenant mutations and trustworthy operator identity for audit.
Extract task commands from main into domain modules over the same use-cases.
Specify stable exit codes and `--output json` without leaking secrets. A future
customer CLI is a token-authenticated HTTP adapter, never this DB bypass path;
ship it only with a selected customer/API credential requirement.
**Acceptance:** absent/ambiguous tenant rejected for destructive commands; operator
and workspace rights never confused; machine output/exit-code contract tested;
legacy operator automation gets a documented transition.
**Dependency/risk:** R-02/06; no unnecessary customer client in the current VK pilot.

### R-09 — Split web/admin along tested use-case seams

**Evidence:** `app/web/tasks.py` mixes routes, form parsing, orchestration and
presentation; `app/admin/views.py` combines domains; templates live in
`app/templates` and `app/web/templates`. Multiple roots alone are not a defect.
**Change:** extract forms/presenters/routes by aggregate; sqladmin remains an
operator adapter to trusted policies, not a second implementation of mutations.
Inventory template loader precedence before proposing consolidation. Inventory
static caching, CSP, dependency/build cost before selecting an assets pipeline.
Keep current product design, URLs, overview and deep links (P8-T39).
**Acceptance:** loader/route/form/CSRF and permission checks preserved, no template
shadowing; browser fixtures updated with moves; no UI redesign or speculative
JavaScript framework migration.
**Dependency/risk:** R-02/04; preserve SQLAdmin hooks and authorized operator behavior.

### R-10 — Durable action audit and support correlation

**Evidence:** per-attempt/usage, job, entitlement and delivery evidence exists;
no general cross-surface action audit model identified in `app/models`.
`app/core/logger.py` provides logging, not a substitute for an action ledger.
**Change:** define the minimal actor/workspace/action/resource/outcome/request/job
metadata contract and retention/access/redaction policy, then add use-case audit
hooks and read-only support views/export. Successful transactional changes should
have transactional audit evidence; denied/rolled-back attempts need a distinct
safe logging path. Never log secrets/raw tool arguments or equate logs with receipts.
**Acceptance:** denied/success/rollback/unknown outcome distinction, tenant-filtered
reads/export, retention and immutable-event permissions; migration/cleanup checks
only in a separately authorized implementation task. Reuse existing billing and
delivery identities instead of a parallel money ledger.
**Dependency/risk:** R-02/03; maps to PRD-02/06/08 without claiming those gates passed.

## 4. Subsequent structural work and demand-led preparation

### R-11 — Decompose persistence and execution modules without replacing them

**Evidence:** `base_manager.py` implements QuerySet/Prefetch/lookup compilation;
`job_manager.py` mixes queue/lifecycle/query concerns; `jobs/handlers.py` and
AI analyzer/reporting/client modules mix orchestration with provider/report logic.
**Change:** extract query compilation/prefetch, lifecycle/claim helpers and
per-job handlers behind stable public methods. Extract input helpers from task
manager after R-02. Keep SQLAlchemy and current manager semantics; no speculative
ORM rewrite, fixed module-length rule or blanket ban on the existing lookup DSL.
**Acceptance:** preserve filtering/relations, transaction ownership, lock/fencing,
usage settlement and error semantics; move-only diffs first, focused regressions
next. Cover rollback/duplicate/lease-loss behavior at the authorized test target.
**Dependency/risk:** R-01–04; lookup/prefetch and concurrent state transitions are
high-risk even when a split looks mechanical.

### R-12 — Canonical imports and small helpers, only after semantic review

**Evidence:** `app/models/source.py` re-exports the canonical collection model;
`models/analysis/llm_model.py` and `models/scheduling/digest_run.py` are aliases;
`_positive_id`, `_brief`, `_error_kind` and plural helpers repeat across packages.
**Change:** migrate internal consumers to canonical domain imports one domain at
a time; inventory migrations, scripts, tests, package exports and external users
before deleting compatibility paths. Deduplicate only equal behavior, keeping
explicit limits/type/error handling at call sites. Do not unify by function name.
**Acceptance:** old saved migration scripts/bootstrap and import-time model
registration still work; no duplicate SQLAlchemy metadata; edge tests for bool,
non-int, invalid IDs, truncation and pluralization retain intentional differences.
**Dependency/risk:** R-01/05/11 as relevant; retiring aliases is not an early
prerequisite to an urgent correctness fix.

### R-13 — Reproducible package/configuration contract

**Evidence:** `requirements.txt` is pinned while `pyproject.toml` has permissive
ranges; both include frozen Celery/Redis. Python metadata/README and package/API
versions differ; project author/URLs are placeholders. Flat `Settings` mixes DB,
security, collection and LLM concerns. `app/celery` is frozen; `app/worker.py`
remains a real supported-path candidate, not dead code by name.
**Change:** choose canonical dependency declarations plus a generated reviewed
lock/export and separate dev/optional profiles; establish supported interpreter
and package version source. Review import/release consumers before moving frozen
code/dependencies to an extra. Narrow settings views by subsystem first; if nested
settings are selected, preserve every existing env key, precedence and default
through compatibility tests. Align visible product labels to AI Assistant; leave
repository/database infrastructure identifiers unchanged unless separately chosen.
**Acceptance:** install/import smoke checks for supported profiles with absent
optional integrations, parity of dependency export, version, env/default/secret
redaction and packaged templates. No change to tenant plan policy, deployment
member ceiling or billing contracts is smuggled into settings cleanup.
**Dependency/risk:** R-01; P2-T15 validation stays deferred, not satisfied here.

### R-14 — Test/document navigation with stable evidence

**Evidence:** browser `.cjs` files and historical fixtures live at tests root;
large handwritten API/CLI reference docs coexist with code; helper code appears
in task-focused suites. Size alone does not establish duplication.
**Change:** move reusable fixtures to `tests/shared/` and browser assets by
purpose only when assigned, updating every path-sensitive loader/import. Keep
new test task-ID naming from tests/AGENTS. Classify offline/DB/browser/external
resources by actual imports/fixtures. Generate only stable reference tables from
OpenAPI/Typer; keep policy and decisions handwritten. Link accepted audit history
lazily; no archival rewrite that destroys links or source attribution.
**Acceptance:** test discovery and intended fixture behavior unchanged; reference
output reproducible; links resolve; no fewer authorization/concurrency assertions
just to reduce test volume. Separate move and behavior commits.
**Dependency/risk:** R-01; root conftest cannot be bypassed based on filenames.

### R-15 — Localizable presentation without changing domain policy

**Evidence:** Russian strings/plural helpers are embedded in web, CLI, tools and
services; `app/core/logger.py` also contains Russian comments despite English
code/docs convention. No unsupported numeric string count is used as evidence.
**Change:** put safe message IDs/parameters at presenter and error boundaries;
select a catalog approach only with an actual locale requirement. Keep Russian
product wording by default and English source comments. Do not translate machine
codes, permission keys, stored states or provider identifiers.
**Acceptance:** plural/interpolation and HTML escaping tests; existing displayed
meaning and API codes unchanged; fallback locale predictable. This is not a
blanket corporate-launch requirement or approval for mass text churn.
**Dependency/risk:** R-04/09; prevent user-controlled formatting/markup injection.

### R-16 — Typed connector/job capabilities without a new framework

**Evidence:** `jobs/handlers.py` has a handler registry but `web/tasks.py` still
branches on job type; connector concerns span `services/collection`, `monitoring`
and `social`; VK client is 1,139 lines.
**Change:** where a real extension needs it, declare narrow typed job/connector
capabilities and separate provider operations from platform-neutral pipeline.
Extract a bounded package seam before moving entire folders. Preserve provenance,
credential ownership, consent and distinct read/write capabilities.
**Acceptance:** one added capability uses existing authorization/paging/retry
contracts without duplicating all adapters; disabled capabilities stay disabled.
P8-T33 owns additional VK result categories; P8-T34 owns the selected non-social
pilot and remains deferred. Neither must wait for a general registry rewrite.
**Dependency/risk:** R-02/04; avoid a universal connector schema that erases category
semantics or treats people/search results as permanent Sources.

### R-17 — Measure capacity before changing topology

**Evidence:** queue/attempt/health evidence exists; `core/logger.py` uses console
and rotating text handlers without a shared request/use-case/job context contract.
This audit measured code only, not p95, database pressure or tenant fairness.
**Change:** choose workload/SLO and safe telemetry schema; connect HTTP/use-case/job
identities without unbounded tenant labels or customer content. Measure queue age,
completion latency, DB pool/lock waits, provider budgets and per-tenant admission
under synthetic workloads; state numerical targets in the implementation task.
Choose fair admission/pool bounds/indexes from those results. Partitioning, replicas
or separate workers require a measured case and explicit architecture decision.
**Acceptance:** reproducible workload, isolation/noisy-neighbor and recovery report,
with cost and p95/queue-age evidence, not a file-count argument. Preserve one
runtime owner and existing claim fencing; no extra worker/scheduler/listener is
started as part of this plan. P7-T02 still owns staging operations proof; PRD-04,
05/08 and PRODUCT_PLAN Scale remain the release/expansion gates.
**Dependency/risk:** R-01/10; synthetic/load execution separately authorized;
telemetry should not become a high-cardinality or privacy liability.

### R-18 — Enterprise seams only for an owner-selected requirement

**Evidence:** current identity is user/membership/role/JWT based; the reviewed API
has no general service-account/token management contract. Existing shared billing
P2-T08 is deferred, and Q-12 already owns pre-public email confirmation/recovery.
**Change:** keep actor/workspace identity explicit so later machine credentials,
SSO/OIDC/SAML, SCIM, organization policy and data-residency can have bounded tasks.
Before building, select one customer/use-case, trust model, revocation/rotation,
role mapping, retention/legal boundary and acceptance matrix. Do not infer that
corporate billing is organization identity, or that all enterprise features are
mandatory for the first managed B2B release.
**Acceptance:** for a selected feature, revoked/mis-scoped credentials and
cross-tenant operations fail closed; existing user sessions and operator boundary
remain compatible; audit/export/privacy requirements are explicit. No new
infrastructure or legal-compliance claim is authorized by listing these options.
**Dependency/risk:** R-06/08/10 as selected; commercial scope stays with P2-T08,
privacy/activation with their release gates, not a duplicate roadmap here.

## 5. Reconciliation with every open card and queue-only follow-up

All 22 `docs/tasks/*.md` files and all open ROADMAP entries were reviewed.
No task was deleted: there is no verified duplicate/superseded acceptance lane.
Merged code, similar files or historical green checks do not close a task. Other
owners/statuses and explicit deferrals are preserved. Structural suggestions
above do not silently become additional mandatory dependencies of existing tasks.

| Existing item | Disposition / authoritative work (not duplicated in this plan) |
| --- | --- |
| P2-T08 | Preserve owner-deferred shared/corporate payer scope; not a general SSO/organization task. |
| P2-T09 | Preserve production-only billing/settlement proof; no live verification authorized. |
| P2-T15 | Correct stale pre-integration wording; foundation now in dev, validation and independent acceptance remain deferred to project end. R-13 does not move existing PLAN_LIMITS/caps. |
| P4-T01 | Preserve ambiguous/stopped delivery staging recovery evidence; R-10 audit is no substitute for no-resend proof. |
| P5-T04 | Clarify remaining recorded structured-DELETE web gating failure, receipts/cleanup requirements and historical-only test attribution. R-02 extraction must not reintroduce force bypasses. |
| P6-T01 | Preserve deploy-gated retention rollout, backup and irreversible-prune approval. No plan authorizes deployment or cleanup. |
| P7-T02 | Preserve staging install/upgrade/rollback/restore/runtime-ownership proof; not replaced by R-17 telemetry/capacity design. |
| P8-T15 | Preserve metadata vs offline mock vs bounded real model checks; shared local-acceptance matrix linked, not a new implementation request. |
| P8-T17 | Preserve diagnostics/explicit POST consent/CSRF/payer acceptance; overlaps files with T15 but has distinct criteria. |
| P8-T18 | Preserve single-owner vs personal-billing admission tests; no new runtime owner. |
| P8-T20 | Preserve explicit zero/unknown cost, persistence/truncation and accessible copy feedback. Historical 45/45 had a foreign DB writer; exclusive rerun still required. |
| P8-T29 | Replace stale P8-T37 pending-integration statement with accepted audit link; exact-current-revision parity and Q-5 live method/profile gate remain open. |
| P8-T33 | Preserve optional bounded verified VK categories after minimum pilot; no invented hard dependency on a P11 connector registry. |
| P8-T34 | Preserve owner-deferred non-social pilot, transfer to selected future phase; no scope explosion into all connectors. |
| P8-T39 | Put shared-use-case/transaction/parity checks here for the task journey; correct P8-T37 dependency. R-02/04 only guide extraction, not a competing task composer. |
| P8-T40 | Put versioned composed-run snapshot, idempotent stages/starts and current entitlement foundation dependency here; reuse P2-T15 without claiming its audit passed or enabling checkout. |
| P8-T41 | Add a revision-pinned cross-surface evidence matrix; consumes Q-5/T39/T40 without retroactively accepting T29 or T15. |
| P11-T01 | This docs-only ordered plan, recovered-draft corrections, matrix and scoped card edits; remains subject to independent review after integration. |
| Q-2 | Preserve original source 2939/media provenance question; refactoring cannot establish unavailable API evidence. |
| Q-5 | Preserve official VK method/profile/deployment-network evidence; timeout remains inconclusive. |
| Q-10 | Preserve doubled credential-disable route compatibility decision; R-06 must not silently break it. |
| Q-12 | Preserve owner-deferred pre-public email-confirmation-then-recovery ordering; do not implement auth recovery as incidental API cleanup. |
| P6-T05 queue-only catalog follow-up | Preserve `mark-read` catalog assertion repair in `tests/admin/test_admin_authorization.py`; not accepted or masked by a new baseline. |
| P6-T05 queue-only tenant-cleanup follow-up | Preserve `test_delete_reports_workspace_that_lost_its_agent_model` cleanup defect; no test ran or left synthetic data in this audit. |

### Scoped card corrections and why

- P8-T29/T39: P8-T37 is ACCEPTED at `f695e6478537733b92973f1ba50ed9cc51bf2fba`
  per [audit](docs/audit/p8-t37.md), not pending. Its old greens do not prove this
  review revision. Ownership of T29's independent live gate is unchanged.
- P2-T15 and PRODUCT_PLAN: remove contradictory "not in dev"/wholly-future
  descriptions; use [ENTITLEMENTS](docs/ENTITLEMENTS.md) and its deferred validation.
  Preserve the original historical handoff sections as history, not current orders.
- P8-T39: own one shared task orchestration/validation path and explicit atomic or
  compensated failure boundary; do not bolt an independent P11 engine onto it.
- P8-T40: current capability foundation exists; all-granted catalog v1 is not
  paid-plan packaging, live checkout or proven acceptance. Composition owns its
  run snapshot and duplicate/partial/retry semantics.
- P8-T41: distinguish static/offline, DB and separately authorized live evidence;
  name actor/tenant/operation/expected vs actual outcome at one exact integrated SHA.
- P5-T04 and P8-T15/17/18/20: copy only the actionable pending acceptance summary
  from their existing audit diagnostics, not a new PASS or status change. Shared
  verification can be run once at one revision and attributed per distinct criterion.

## 6. Suggested next bounded task and handoff

Register one R-01 offline safety-net task, or prioritize the recorded P5-T04
permission defect if its owner resumes; do not block that fix on all structural
work. Then deliver one R-02/R-03 task-operation slice through P8-T39. Keep the
existing P8 milestone deliverable while later structural work remains selectable.

Implementation must be planned/claimed separately. This audit changes documents
only. Independent review should verify priority, each evidence path, task-card
ownership, compatibility/deferral preservation and link integrity; it is not a
request to run the full application suite or spend on live providers.
