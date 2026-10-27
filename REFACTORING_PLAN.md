# Refactoring plan — critical structural audit

Task: [P11-T01](docs/tasks/p11-t01.md) · Parent: [P11](docs/phases/p11-structure.md).
Reviewed application snapshot: `1a4f24648603341709ebfc8f9774b1b7cc3cc84f` (`dev`, 2026-10-27).
Resume claim: `00f530702f924d6a677d5c565a389b1e44cab88f` (registry-only).
Recovered draft: `83567d309e7e3f26e7e3f6c5ed14de9c25c341ed` on
`docs/p11-t01-refactoring-audit`; original source `03a0eea`.
This revision supersedes its R-01–R-30 numbering, including the unpublished notes
provided by the owner. Findings below were rechecked, not inherited as proof.

This is an ordered **structural plan**, not a second status registry. Existing
functional work stays in its task card (section 5). All 18 items are now
registered as P11-T03–P11-T20 by the owner's explicit
2026-10-27 request (superseding the earlier one-at-a-time registration advice).
Scope/acceptance live in those cards; priority/complexity/status/owner live only
in ROADMAP. Claim execution one dependency-ready task at a time.
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

## Registered follow-ups

Detailed findings and acceptance moved into the linked task cards. The source
R-IDs remain stable for audit attribution; they are not a second task registry.
All are children of [P11](docs/phases/p11-structure.md). Current priority and
complexity are in [ROADMAP](ROADMAP.md#execution-queue), not duplicated here.

| Finding | Task | Structural deliverable |
| --- | --- | --- |
| R-01 | [P11-T03](docs/tasks/p11-t03.md) | Offline CI safety net and supported baseline |
| R-02 | [P11-T04](docs/tasks/p11-t04.md) | Shared task application orchestration |
| R-03 | [P11-T05](docs/tasks/p11-t05.md) | Explicit task transaction ownership |
| R-04 | [P11-T06](docs/tasks/p11-t06.md) | Typed use-case contracts, safe errors and presenters |
| R-05 | [P11-T07](docs/tasks/p11-t07.md) | Incremental package-boundary enforcement |
| R-06 | [P11-T08](docs/tasks/p11-t08.md) | Public API compatibility and bounded request policy |
| R-07 | [P11-T09](docs/tasks/p11-t09.md) | Fail-closed typed agent-tool registration |
| R-08 | [P11-T10](docs/tasks/p11-t10.md) | Operator CLI boundaries and machine-output contract |
| R-09 | [P11-T11](docs/tasks/p11-t11.md) | Web and admin use-case decomposition |
| R-10 | [P11-T12](docs/tasks/p11-t12.md) | Durable action-audit contract and support correlation |
| R-11 | [P11-T13](docs/tasks/p11-t13.md) | Manager and execution-module decomposition |
| R-12 | [P11-T14](docs/tasks/p11-t14.md) | Canonical model imports and semantic helper reuse |
| R-13 | [P11-T15](docs/tasks/p11-t15.md) | Reproducible package and configuration contract |
| R-14 | [P11-T16](docs/tasks/p11-t16.md) | Test and reference navigation without evidence loss |
| R-15 | [P11-T17](docs/tasks/p11-t17.md) | Localizable presentation boundaries |
| R-16 | [P11-T18](docs/tasks/p11-t18.md) | Typed connector and job extension seams |
| R-17 | [P11-T19](docs/tasks/p11-t19.md) | Capacity telemetry and reproducible scale evidence |
| R-18 | [P11-T20](docs/tasks/p11-t20.md) | Select and specify bounded enterprise requirements |

Start with the offline safety net, then the shared use-case and explicit
transaction slice. Consolidate typed boundaries before adapter decomposition.
Urgent permission/data-cleanup fixes and the existing P8 milestone must not wait
for all of P11. Later locale/connector/capacity/enterprise items have explicit
selection/measurement boundaries; TODO is not blanket activation permission.
Apply SOLID/DRY only where semantics agree, retain KISS/YAGNI and keep structural
moves separate from behavior changes. Do not replace the ORM or add runtime
owners without a measured, separately authorized requirement.

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

Claim [P11-T03](docs/tasks/p11-t03.md) for the offline safety net, or prioritize
the recorded P5-T04 permission defect if its owner resumes. Then deliver the
P11-T04/T05 operation slice alongside P8-T39. Registration of every item does
not make all later structural work prerequisites to the existing P8 milestone.

Implementation must be claimed separately. This audit and registration change
documents only. Independent review should verify priority, each evidence path, task-card
ownership, compatibility/deferral preservation and link integrity; it is not a
request to run the full application suite or spend on live providers.
