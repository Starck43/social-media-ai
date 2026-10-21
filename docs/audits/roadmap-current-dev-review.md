# Current-dev ROADMAP reconciliation

## Scope and verdict

Source: `dev` **ddc9b99164c326a01a81e266fc1f26a15dd822cc**. Read the live
ROADMAP, all phase task definitions, implementation index, relevant current
code, Git history and active PR #216/#217 heads. This is a source/offline
review of the open board, not full functional/security/release acceptance.
No foreign task was accepted, closed, reassigned or silently renumbered.

The main drift is **implementation versus acceptance**, not an empty codebase.
Several TODO/BLOCKED rows should become acceptance-only or narrower follow-ups
once their owners review the evidence. Other defects are still reproducible.
The first implementation priorities are scope safety, login protection and
per-component query rights, not another rewrite of the already-delivered admin
model-test UI.

## Implementation already present or changed

| IDs | Current observation | Recommended disposition, not a status change |
| --- | --- | --- |
| P10-T01 | Public reset POST immediately returns 410; guarded operator password administration is the recovery alternative (`app/admin/endpoints.py`). New isolated probe confirms no lookup/write on public reset. | Candidate for bounded acceptance, not a new anonymous-recovery implementation. Still review actual authenticated reset/CSRF/revocation routes locally. |
| P10-T02 | `UserInDB` has no hash field. Actual Pydantic serialization and JSON schema exclude the synthetic hash. | Candidate for HTTP/OpenAPI acceptance; schema evidence alone is not the whole HTTP matrix. |
| P10-T03 | `UserUpdate` accepts only username/email; manager rejects privilege fields, reloads an active actor, denies foreign profile writes, and dedicated role change requires fresh platform admin and denies self-role change. | Candidate for route/manager acceptance, not another profile/role redesign. New mocked tests cover negative manager paths; DB races and real dependencies remain local. |
| P5-T04 | Durable context receipts and fingerprint-scoped analytics are committed via `24dfbaa`, not merely uncommitted work. Current fingerprint probes distinguish scenario/model/prompt/schema changes. | Update the old uncommitted-wording at owner review; retain IN_PROGRESS until unresolved DB permission expectation and current integration evidence are reviewed. Receipt concurrency/cleanup is not proved here. |
| P5-T06 | `17f2c3b` implements JSON expansion, Jaccard INTERSECT/UNION and explicit CAST binds. Actual SQLAlchemy TextClause compilation and guarded ID readback pass offline. | READY is plausible: prioritize coordinator acceptance, not reimplementation. SQL compilation is not PostgreSQL execution; prior live evidence remains owner-reported at its original revision. |
| P5-T07 | `ed3bd94` changes policy: URL-less media is ignored; unconfigured modalities are skipped; bounded text failover and daily coverage merge are present. New isolated classifier tests confirm current URL-less/URL-bearing/malformed-collection behavior. | Do not restore the older “missing media model always withholds parent” policy. Reconcile old fixtures/assertions to the approved semantics before accepting current analyzer coverage. This review does not retest live failover or daily DB merge. |
| P6-T03 | `1e0e3a0` implements the raw cleanup action and scoped manager. Four new handler checks pass for CSRF/admin denials, busy/missing source, and explicit source/tenant/session forwarding. | Implementation plus new mock evidence, not a new cleanup feature. Keep real source-admission/delete race, rollback, tenant and receipts/analytics preservation acceptance local. Never perform actual cleanup for this audit. |
| P10-T13 | PR #218 is merged; the source and 17 handler + 8 real Typer/Rich parser tests remain in dev. | Merged implementation, deferred owner-environment acceptance. “Draft” text in the old CLI handoff is historical, not current PR state. |
| P8-T15/T16/T17/T18/T20 | Current admin probe, role controls and diagnostics/cost UI exist. Later P8-T18 explicitly supports billing-disabled single-owner mode; enabled personal mode still uses payer admission. | These BLOCKED rows are acceptance-only, not five missing features. Earlier payer/checkbox/UI assumptions must be read through the newer contract. No new current browser/ASGI/ledger acceptance is claimed. |

## Registry contradictions requiring coordination

- **P8-T21 has two meanings:** ROADMAP says “compact inline task source picker”,
  owner `$local`, READY; the phase file says accepted DONE “related FK/m2m
  editors”, owner remote Notion agent, merged PR #219. Preserve the accepted
  FK/m2m evidence. Coordinate an unused ID and a proper phase row for the
  picker; do not silently overwrite either task or declare the picker DONE.
- **P8-T22 exists in ROADMAP but has no task definition in p8.** Its task and
  source-run UI are visibly implemented (`401a636`, `35e34f5`), but acceptance,
  exclusive ownership and remaining scope must be restored by its owner.
- **P3-T01 points to a closed PR and obsolete execution pin.** GitHub #176 and
  #202 are closed without merge; #202 states the acceptance modules are already
  in dev and P3-T01 remains open. Use current checked-in DB/ASGI tests, not old
  `0c18bab`, and preserve original evidence. This review updates wording only.
- Open PRs **#216** (`b9faf8d`, P10-T11) and **#217** (`c4161b5`, P10-T12)
  remain separate; neither head is an ancestor of this dev. Their ID reservations
  must survive new allocations. P10-T11/T12 are not completed current-dev work.
- `IMPLEMENTATION_STATUS.md` contains historical UNWIRED billing rows while
  phase P2 records later accepted personal-ledger/wiring slices. Do not create
  another legacy tenant-budget implementation from that old snapshot. Activation,
  production verification and owner deferrals remain separate.

## Still-open contracts verified in this review

| IDs | Evidence and narrow next action |
| --- | --- |
| P6-T04 | `agent_task_sources.source_id` cascades on source deletion; empty task links mean all active sources. The new source-resolution counterexample widens a previously explicit task to another active source after the final link disappears. Fix at the existing manager/transaction boundary; preserve genuine all-active tasks. Actual delete/task-edit races need PostgreSQL. |
| P10-T04 | Normal missing-user/bad-password login returns False without awaiting `track_login_attempt`; attempt state is session-based. New expected-failure probe demonstrates the missing ordinary-failure count. Shared cross-surface/process limit and valid dummy verification still need work. |
| P10-T05 | Both legacy API DB probes still return exception text. Two synthetic sentinel tests fail the redaction contract. Reuse bounded readiness errors, leaving root readiness behavior intact. |
| P10-T07 | Digest brief receives source/scenario filters, while sentiment/topics/content mix/engagement/provider stats are called with days only. New mocked aggregate probe reproduces the mismatch. Fix one shared row set/date/source/scenario context, including rollups. |
| P10-T08 | Task-only dashboard offer still loads analytics counts; the new denied-query probe fails. Add component-specific gates before loading queries and rendering links. |
| P10-T09 | Current `job_count`/`queued_count` describe at most three preview rows. A running prune and analyze both get `collect=False`. Passing observation probes confirm these are not global totals/type-correct labels. Follow T08 in the same file lane. |
| P10-T10 | Real offline wheel builds successfully but omits nested runtime code/assets and puts cryptography only in dev extras. See wheel evidence below. Fix packaging before distributing the wheel; do not confuse build exit 0 with working installation. |
| P10-T06 | Admin probe and some analyzer paths are sanitized, but general client transport errors still embed provider response text (for example OpenAI HTTP failure). Existing journal/accounting must survive any centralization. Only source review here, not complete error/log/accounting-path acceptance. |
| P5-T01 | New malformed-member probe shows `classify_content` calls `.get()` on a None attachment member. Treat this as a bounded hostile-input candidate, not permission to reinterpret approved URL-less-media policy. No runtime fix in this audit. |
| P5-T05 | Map-reduce is design-stage. The active text path still makes a batch-shaped request; new text failover is not item-level map/reduce or a per-run spend budget. Land bounded diagnostics/chunk/partial-persistence slices after receipts and P5-T07 regression reconciliation; “any model/any volume” remains a goal. |
| P10-T12 / PR #217 | Unchanged PR217 tests over this dev archive run 27 tests and exit 1 (19 failures, 7 errors including subtests). Directly verified: task_list NameError; omitted scenario can be cleared; cron expression not persisted with next_run; source replacement/validation and partial-date contracts fail. The branch is useful, but must be rebased/reviewed against current receipt/force policy before integration. Do not count absent `clear_scenario` alone as a new security regression. |

## Remaining board: what can be checked remotely

- **P1-T01:** no-DB admission logic/SQL construction can be checked here; the
  named two-connection PostgreSQL race/rollback and ASGI tenant acceptance
  cannot. Preserve the recorded interrupted-run/uncertain-test-schema blocker.
  **P1-T02** remains a selection slot, not evidence of an additional known bug.
- **P2-T08/T09:** explicitly owner-deferred. Corporate billing and live
  post-activation settlement verification are not this audit's next work.
- **P3-T01/T02:** confirmation and fail-closed pure/mocked boundaries are
  remotely testable (26 current confirmation tests pass); actual permission,
  revocation, replay/commit races and remaining digest send acceptance are local.
- **P4-T01 / P7-T02:** staging delivery recovery, restore and runtime-ownership
  proof cannot be replaced by sandbox unit tests. No sends or runtime startup.
- **P6-T01:** code is already delivered; rollout remains owner-blocked.
  **P6-T02:** select a bounded export/offboarding/retention contract; do not
  automatically close it from the separate raw cleanup action.
- **P7-T01:** Compose still publishes `5432:5432`; it is not loopback-only.
  Configuration diff/static checks are possible here, actual host/network proof
  is local/staging. Do not start Compose (API startup runs Alembic).
- **P8-T07:** monitoring already has `last_checked` selection machinery; a
  permission-gated stale/never-checked UI signal is the remaining contract.
- **P8-T08:** connections distinguish connected/renewable/reauth/missing using
  metadata; attention currently includes reauth/missing, not a near-expiry
  warning. Extend read-only metadata policy; do not decrypt or mislabel renewable.
- **P8-T09:** queue UI already has overdue count/age, stale-running and
  unconfirmed outcomes; ten current offline signal tests pass. Reconcile the
  narrower quarantine/retry-receipt acceptance instead of rebuilding the page.
- **P8-T10:** API live/readiness does not prove scheduler/worker/listener
  freshness. Runtime identity needs a separate truthful observable.
- **P8-T11:** queue unknown-outcome counts are not a cross-job/digest
  incomplete-delivery aggregate. Reuse receipts; no new resend semantics.
- **P8-T12:** personal billing/financial visibility exists, but the full
  unknown-price fleet/exposure aggregate is not accepted by this review.
  **P8-T13** requires a named human owner and agreed pilot measures, not code.
- **P9-T01:** a default-disabled capability contract can be designed/tested
  with mocks; actual activation is dependent on its foundation gates and
  owner authorization, never implied by a green unit suite.

## Suggested order (risk/dependency, not ownership transfer)

1. Repair registry identity/links with task owners; do not close foreign rows.
2. Implement **P6-T04, P10-T04 and P10-T08** in separate bounded lanes: prevent
   unintended source scope, ordinary-login protection gaps and forbidden reads.
3. Refresh/review **PR #217** for chat-task configuration integrity; first
   reconcile current input/receipt policy, then run local persistence/rights.
4. Repair the stale isolated fixtures and add current P5-T07 regressions without
   weakening assertions. Review/accept already-present P5-T06, P6-T03 and the
   P10-T01–T03 fixes instead of writing replacements.
5. Deliver **P5-T05** incrementally with real bounded-output/partial-progress
   evidence; add **P10-T05** as a small privacy fix, then the broader P10-T06
   and P10-T07 contracts. Serialize T08/T09 shared dashboard files.
6. Complete **P10-T10** before wheel-based distribution. Keep owner-deferred
   production, billing activation, retention rollout and staging proofs deferred.

## Executed isolated evidence

Separate Python 3.13.14 venv; selected package versions follow requirements or
pyproject build constraints. Every existing test/import graph was inspected
before execution. Runs use explicit files, no pytest/root conftest, and a
meta-path guard refuses undeclared application imports. Explicit module-local
and sys.modules doubles are still permitted: this is not a general sandbox.
No application/config/.env/engine startup, real DB, secrets or provider calls.

### Current existing checks

Command shape: `python tests/run_roadmap_offline.py tests/<file>`.
Nonzero runs are recorded as failures/blockers, never converted into skips.

| File | Tests run | Exit | Result |
| --- | ---: | ---: | --- |
| `test_llm_cli_contracts.py` | 17 | 0 | OK |
| `test_llm_cli_parser.py` | 8 | 0 | OK |
| `test_runtime_confirmation_authorization.py` | 26 | 0 | OK |
| `test_queue_recovery_admission.py` | 36 | 1 | FAILED (errors=22) |
| `test_jobs_queue_signals.py` | 10 | 0 | OK |
| `test_staged_count_scope.py` | 15 | 1 | FAILED (errors=14) |
| `test_staged_retention_tenant_scope.py` | 12 | 0 | OK |
| `test_analyzer_reported_errors.py` | 34 | 1 | FAILED (errors=35) |
| `test_partial_analysis_coverage.py` | 19 | 1 | FAILED (errors=19) |
| `test_media_skip_coverage.py` | 22 | 1 | FAILED (errors=25) |
| `test_scenario_output_contract.py` | 33 | 1 | FAILED (errors=11) |

Failure classification, not a runtime diagnosis:

- Queue admission fixture is missing the current `get_current_user` import;
  some fallback imports were blocked, and cancellation tests time out waiting
  for work that never starts. The PostgreSQL admission task is NOT validated.
- Staged-count AST harness lacks `normalize_attachments` (14 errors).
- Reported-error analyzer harness lacks current suitability/privacy/classifier
  bindings; normal app imports are refused. Some teardown errors are additional
  to test-body errors.
- Partial/media fixtures lack scenario `is_active`; they fail before meaningful
  coverage assertions. Older missing-model/URL expectations also need explicit
  reconciliation to the approved newer policy, not blind assertion changes.
- Scenario-output analyzer fixture lacks `filter_by_context_receipts` (11
  errors); pure schema cases do run. Do not report the whole file as passing.

### New observations

`python tests/run_roadmap_offline.py tests/test_roadmap_reconciliation.py`:
**30 tests, 23 passed, 7 expected failures, exit 0**. Expected failures represent
OPEN defects: ordinary login counting, two legacy DB error envelopes, task-only
analytics read, malformed attachment member, digest scope mismatch and last-link
source expansion. They are not green feature acceptance. New passing probes
cover current reset/schema/manager, fingerprint/media policy, scoped cleanup
handler paths and actual SQLAlchemy compilation/readback wiring.

A dedicated archive of current dev plus the unchanged test file from PR217
`c4161b5` ran `test_chat_task_contracts.py`: **27 tests, exit 1, 19 failures,
7 errors** (subtests can produce more outcomes than test methods). This overlay
is not an unchanged PR217-head run and adds no PR217 runtime code to dev.

New test harness setup initially omitted PeriodType.DAY/SQLAlchemy text; those
explicit doubles were corrected before the final new-test run. No production
source or pre-existing assertion was modified to get a pass.

### Wheel evidence

Clean source archive of the pinned dev; inspected setuptools-only build config,
no setup.py. Command: `PIP_NO_INDEX=1 python -m pip wheel . --no-deps
--no-build-isolation --wheel-dir <isolated-output>` with setuptools 84.0.0 and
wheel 0.48.0 satisfying pyproject build constraints. Build exit 0.

`social_media_ai-0.4.2-py3-none-any.whl` contains only **14 entries**.
`app/runtime.py` and `cli/main.py` exist, but `app/core/config.py`,
`cli/commands/llm.py` and web templates are missing. METADATA lists
`cryptography>=42.0.0; extra == "dev"`, not a runtime dependency. The wheel
was inspected, not installed/imported; no install smoke success is claimed.

### Versions used

```
annotated-doc==0.0.5
annotated-types==0.8.0
black==26.10.1
click==8.5.0
dnspython==2.9.0
email-validator==2.3.0
idna==3.20
isort==9.0.2
markdown-it-py==4.2.0
mdurl==0.1.2
mypy_extensions==1.1.0
packaging==26.3
pathspec==1.1.1
pip==24.2
platformdirs==4.13.5
pydantic==2.14.1
pydantic_core==2.50.1
Pygments==2.21.0
pytokens==0.4.1
rich==15.0.0
setuptools==84.0.0
shellingham==1.5.4
SQLAlchemy==2.1.4
typer==0.27.3
typing_extensions==4.16.0
typing-inspection==0.4.4
wheel==0.48.0
```

Formatting: pinned Black/isort on the two new files; AST parsing and
`git diff --check`. No migration/reset/drop/stamp, runtime/Compose startup,
live action, force-push, direct dev push or deployment. Old dirty sandbox
worktrees remain intact. Only this audit's new evidence/annotations are owned.

## Integration handoff

P10-T14 is READY for audit review, not a code-fix or phase acceptance. Exact
published audit SHA is in its PR. Recheck dev and active task owners before any
implementation; no global guarantee follows from the selected tests. Keep
foreign statuses until the coordinating owner reviews current evidence. Resolve
the P8-T21 semantic collision with its owners, not an automatic renumber/delete.
