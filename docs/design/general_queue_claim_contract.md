# Ordinary job claim and outcome contract

## Status and bounded scope

The source observations below were drafted at historical baseline
`0be47bbfb0d13e63cd0509dd072e17ad0047e9db`. The claim-fencing implementation
merged through PR #51 as `351f3b6`, with owner evidence on exact `dbc054c`.
Job/task atomicity merged through [PR #53](https://github.com/Starck43/social-media-ai/pull/53)
as `243866281f1bb2e3ed87827601e2705e82b6c3df`; owner-tested head
`e3e222673bb39debe2b07cdf4fd7d95b71a8dac1`: 27 unit, 15 existing PostgreSQL
and 7 new PostgreSQL checks, all exit 0. These are owner-reported focused
checks, not a full suite or a combined-dev verification.
Web claim-loss feedback merged through [PR #52](https://github.com/Starck43/social-media-ai/pull/52)
as `10d160901b7305a0ed22d15fd146b7573d6a399b`, final head `4f664786`;
its executable files equal owner-tested `f613839` (15 standalone, exit 0).
Operator cancellation [PR #54](https://github.com/Starck43/social-media-ai/pull/54)
is merged as `ee43592a05d706168e7b80ece87ea17e2de34641`, final `7316a084`;
owner-reviewed/tested at `4d65af4e55c6b3096db2278ce2589cfc17f1525e`:
14 cancellation + 15 web outcome standalone, config check and 10 PostgreSQL,
sequential/all exit 0; [owner evidence](https://github.com/Starck43/social-media-ai/pull/54#issuecomment-6097327068).
No code corrections or author-run tests; the final delta was docs-only.
Merged does not establish deployment, acceptance or a combined-dev green suite.
The cleanup_done follow-up merged as `9d63892c` through [PR #55](https://github.com/Starck43/social-media-ai/pull/55)
from dev `ee43592a`, owner-reviewed/tested at exact
`1eb561fdebbcf2a556bdfca7089ae79d1625cda8`: 6 standalone + read-only config check
and 8 PostgreSQL, sequential/all exit 0; [owner evidence](https://github.com/Starck43/social-media-ai/pull/55#issuecomment-6097728246).
No code corrections, author-run tests or merged-head/full-suite claim.

Implemented boundary: immutable acquired identity, locked tenant/generation-bound
ordinary outcome writes, explicit committed/lost receipts, and exact-row direct
acquisition. Ordinary callers consume their own receipt rather than reloading a
newer generation. Claim loss raises a bounded error at caller entry points, so
legacy non-failed-is-success adapters cannot turn it into a success response.
Direct enqueue/acquire races raise NOT_ACQUIRED without running a handler;
`run_job_now` retains its existing None-on-not-acquired interface.

The prior #51 boundary projected Task after Job commit. This follow-up moves
terminal Task updates into the claim-locked Job transaction; a referenced
Task that cannot be updated is a write failure, not a clean completion.
Task-write failures now precede the commit receipt. Notifications remain
post-commit and cannot cause handler retry or change an acknowledged outcome.
The ID-only manager methods remain non-atomic legacy compatibility APIs;
every ordinary dispatcher finalizer supplies a claim. Legacy receipt projection
flags remain readable for compatibility, not a fallback for the atomic writer.
Checkpoint digest retains its separate dispatch/finalization protocol.

Prepared source-isolated and existing-schema PostgreSQL tests retain ownership,
loss, competing finalizers and direct-race checks, and strengthen commit/rollback
cases for both rows. Post-commit Task-failure tests now specify pre-commit Task
failure rollback; they are not simply dropped or made optional. The previous
#51 results do NOT certify this changed boundary. These prepared checks have
NOT been executed by the author; the #53 owner results are recorded above.
Deployment/acceptance remain separate.

No migration, new job status, provider retry policy, sender activation, model
layout, permissions grant or accounting ledger is authorized by this document.
The checkpoint digest publisher/finalizer remains its own protected contract.

## Source-grounded observations

Line ranges refer to the source baseline, not future refactored locations.

| Boundary | Current behavior | Risk or limitation |
| --- | --- | --- |
| JobManager `_claim`, lines 55–86 | Locks a due pending row with SKIP LOCKED; increments attempts and writes started_at | Atomic acquisition does not fence subsequent writes |
| JobManager `mark_done`, lines 163–176 | Reads Job, updates by id, then records AgentTask separately | An old attempt can overwrite a newer claim; a task-write failure can surface after Job committed |
| JobManager `mark_failed`, lines 178–227 | Reads current attempt count and writes retry/terminal result by id | A stale failure can reschedule or fail another attempt and use its retry count |
| Dispatcher `execute_job`, lines 180–262 | Runs handler and generic bookkeeping in one exception boundary | A persistence/bookkeeping exception can be mistaken for a handler failure |
| Dispatcher direct paths, lines 314–340 | Ignores `start_running`'s Boolean and reloads by id | A caller that did not acquire the claim can execute an already-owned row |
| Dispatcher result reads, lines 264–312 | Reloads the row after execution | A newer attempt's status/result can be presented as this caller's result |
| Digest `_check_claim` / `finalize_digest_job` | Checks tenant, type, task, running status, attempts and started_at | This is a useful existing identity pattern, not ordinary-queue or task/job atomicity proof |
| AgentTask `record_result`, lines 259–274 | Uses a separate manager write with completion wall time | Task summary is neither atomic with Job nor a proven latest-scheduled-run projection |
| Web jobs retry/cancel, lines 318–386 | A missing run result may produce success flash; cancel reads then writes by id | UI needs explicit non-execution/lost-claim feedback; cancellation has its own write race |

Direct entry points observed in the complete current app/cli tree: worker
`run_pending_once`, exact-job `run_job_now`, `run_task_directly`, `run_job_inline`,
web tasks/sources/jobs, API task run endpoints, admin task action, agent
`collect_now`, CLI task run and CLI collect. `BotActionManager.mark_failed` is a
separate action lifecycle, not a Job finalizer.

These are source findings, not reproduced production incidents. In particular,
web cancellation's comment about stopping at every step is not evidence that
all generic handlers check cancellation before each external effect.

## Claim identity and acquisition

A claim is an immutable value captured from the row returned by a successful,
committed acquisition. Proposed fields:

- `job_id`, `tenant_id`, `job_type`, `agent_task_id` (including an explicit None);
- `attempts` as the acquired attempt generation;
- `started_at` as the persisted, non-NULL timestamp for that generation.

Do not use `locked_at` as the identity token: heartbeats legitimately change it.
Do not derive any identity field from handler output or payload overrides.
Do not construct a claim from an arbitrary `get(id=...)` after losing acquisition.
Validate types, positive IDs/attempts and aware timestamps at the typed boundary;
compare persisted timestamp values, not locale/string formatting. Existing
started_at/attempts columns are sufficient for the first slice; no schema token
is introduced. Legacy rows without a valid claim are not silently repaired.

Global worker acquisition is explicitly trusted cross-workspace work. Ordinary
outcome writes still require the concrete claim's tenant; None must never mean
all tenants. Interactive callers retain their existing permission/tenant gates.
This proposal does not expand operator/service authority.

For direct execution, replace the Boolean-then-unqualified-reload pattern with
an acquisition that returns the committed Job and its exact claim. A failed
acquisition returns NOT_ACQUIRED and executes **zero handlers**. It must not
borrow a fresh row now owned by a worker. Due-time semantics must be explicit:
normal acquisition does not execute a future-dated Job early.

## Ordinary outcome write protocol

All ordinary success, returned failure, exception retry/terminal failure and
unknown-handler outcomes use the same claim check. No worker path may fall back
to an unfenced ID-only write when validation fails.

A short database transaction must check and mutate the same row. Either use a
conditional UPDATE with returned row evidence or lock the tenant-bound row,
compare the full identity, then mutate it before committing. An earlier unlocked
SELECT followed by update_by_id is not equivalent. The write predicate includes:

```sql
id = :job_id
AND tenant_id = :tenant_id
AND job_type = :job_type
AND agent_task_id IS NOT DISTINCT FROM :agent_task_id
AND status = 'running'
AND attempts = :attempts
AND started_at = :started_at
```

This is a predicate sketch, not a runnable SQL/migration command. Tenant/schema
resolution must use the existing application context and mapped table.

Proposed in-memory acknowledgements (not new DB statuses):

| Acknowledgement | Meaning | Allowed subsequent action |
| --- | --- | --- |
| COMMITTED_DONE | This claim's successful/skipped outcome committed | Use committed receipt; success notification only for non-skipped result |
| COMMITTED_FAILED | This claim's terminal failure committed | Failed task/notification projection from this receipt |
| COMMITTED_RETRY | This claim's permitted retry committed | Report retry; no completion notification |
| CLAIM_LOST | Row missing or status/identity no longer matches | No Job/task/cost write, notification or handler replay by this caller |
| NOT_ACQUIRED | Caller never acquired the claim | No handler execution; do not report successful execution |

Database errors are **not** CLAIM_LOST. If commit acknowledgement is uncertain,
report persistence uncertainty to the caller/operator, suppress completion
notification and stop this caller's automatic retry/finalization path. Do not
turn a write failure into a new handler exception retry or mark the row done by
inference. Preserve bounded diagnostics without raw customer/provider payloads.

The first slice cannot durably distinguish every unknown commit from an ordinary
crashed running row. Existing stale recovery might later requeue such a row.
That remains a release/recovery limitation; stopping the dispatcher now is NOT a
claim of safe unattended later recovery. Recovery must not clear evidence,
replay an external effect or reset a receipt merely to obtain a green result.

Use the acquired attempt's budget for the existing retry ceiling/backoff, not a
newer's attempt count. Preserve `allow_retry=False` on manual/inline calls and
terminal returned-failure policy. Do not redefine collection counters, skipped
semantics, safe failure audit shapes or checkpoint uncertainty categories.

Known LLM cost, including explicit zero, is retained only on a committed matching
outcome; absent/unknown usage stays unknown. A stale attempt's already-incurred
spend cannot be attached to a newer attempt. The missing per-attempt accounting
record remains a separate design problem; do not solve it by dropping unknown
cost to zero or copying it into the current Job's cost.

## Commit boundary, task projection and notifications

The dispatcher must distinguish handler execution, outcome persistence and
post-commit projection. Ordinary Job and referenced AgentTask terminal changes
must share a commit; a Task-write error aborts that attempt's database write,
not a handler retry. Once a matching outcome has committed, a notification
failure must never schedule that handler again or rewrite the successful Job. A committed receipt remains authoritative.
Report post-commit notification degradation separately; do not suppress the
acknowledged Job result. Pre-commit Task failures and unknown commit ACKs return
no success receipt; callers stop without an immediate finalizer/handler replay.

CLAIM_LOST and NOT_ACQUIRED must be propagated to callers, not erased by an
unqualified post-execution row reload. Reading the current Job for observation
is allowed, but label it as current row state, not this attempt's completion.
Web/CLI/API/agent adapters must not convert None or lost-claim responses into a
success flash. Adapter changes are a coordinated follow-up to the typed contract,
not an excuse for changing unrelated UI.

Two follow-up guarantees are deliberately separate:

1. **Job/task atomicity — selected ordinary-outcome slice.** The JobManager
   owns one new session and one `session.begin()` boundary. Hold the full-claim
   Job row lock while selecting the referenced AgentTask by BOTH task ID and
   claim tenant with FOR UPDATE. Terminal done/failed outcomes update
   `last_status`, `last_error` and `last_run_at` in that same session before
   returning an acknowledged receipt. Taskless Jobs need no Task write; retry
   outcomes preserve the existing no-terminal-summary policy.

   Use mapped ORM rows, not QuerySet.update, record_result or a nested manager
   call that opens/commits another session. A missing/foreign referenced Task is
   a write error, not successful zero-row projection and not CLAIM_LOST. No
   fallback to a detached post-commit Task write is permitted. Existing legacy
   ID-only manager paths retain their separate, non-atomic compatibility behavior.

   A Task write/flush/cancellation error before commit rolls back both database
   changes; the caller stops without handler replay or a second finalizer. A
   lost commit acknowledgement establishes NEITHER rollback NOR claim loss:
   both rows may have committed, and no completion notification is authorized
   without an acknowledged receipt. Notifications remain outside the transaction.
   Receipts remain authoritative if a later notification callback fails.

   Lock order is Job then Task. FK deletion or other reverse-lock paths can
   deadlock; database aborts are surfaced without automatic retry/replay. This
   slice does not redesign deletion, introduce global locks or promise absence
   of deadlocks. No external/provider effect participates in this transaction.
   Job result/cost writes also roll back with the database transaction; an
   already charged LLM call is NOT rolled back. Missing durable per-attempt
   spend evidence remains an accounting/release limitation, not zero spend.
   The known replay/recovery limitation stays open.

   Prepared evidence must cover visibility before commit, failure after actual
   Task flush, zero-row/foreign-Task rejection, coupled commit-ACK loss,
   competing finalizers, retry/taskless paths and cancellation rollback. Earlier
   #51 green evidence used a POST-COMMIT Task projection and does not certify
   this changed commit boundary; the applicable tests must be owner-run again.
2. **Latest-run task summary.** Two distinct Jobs for one AgentTask can complete
   out of order even when both claims are valid. Atomicity alone does not decide
   which completion should replace last_status. The current task has no
   last_job_id/generation pointer; last_run_at serves trigger and completion.
   Do not advertise ordering or add a pointer/migration without a separate
   contract and approval.

A post-commit best-effort notification is not a durable exactly-once outbox.
Cancellation authority is distinct from worker ownership. The merged operator
writer below conditions its mutation on the observed generation; it does not
acquire the worker's claim or establish authority merely by constructing a value.
Web guards and manager tenant-context checks remain required.

### Bounded operator cancellation — merged

`JobManager.cancel_running` accepts a validated immutable `JobClaim` snapshot
from the route's observed running row. It locks the row matching concrete tenant,
id, type, task (including NULL), running status, attempts and started_at before
writing failed / static operator reason / current UTC finished_at. Heartbeat
locked_at is not generation identity. Existing result, cost, attempts, timing
and audit fields are preserved; ORM updated_at may advance.

True is returned only after acknowledged commit; False means no matching running
snapshot and no mutation. Database/flush/commit failures propagate without a
success flash, inferred rollback, immediate retry or another cancellation write.
The route keeps CSRF, platform-role and permission guards, explicit tenant lookup
and superuser bypass behavior. Missing/malformed generation fails closed; stale
snapshot is an error redirect, never a success. Success states only that
cancellation was recorded, not that an in-flight external effect stopped.

This intentionally retains the existing Job-only operator cancellation policy:
no Task summary, digest checkpoint, spend-ledger or notification update is added.
Task summaries can therefore remain at their prior result after operator cancel;
this is not the ordinary worker terminal-outcome path delivered by #53.
Cancellation is not a provider abort or proof of safe replay. Legacy malformed
running rows need separately authorized investigation, not an ID-only fallback.
Recovery, task-summary ordering and per-attempt spend remain OPEN.

Prepared checks cover full snapshot identity, scope/bypass, heartbeat, stale
reclaim/terminal rows, competing cancel/completion, audit preservation,
pre-commit rollback and committed acknowledgement loss. Source-isolated route
checks cover guards, malformed identity and truthful flashes. None were run by
the author; the focused owner results at exact `4d65af4` are recorded above.
The owner used the existing reviewed test environment, without schema changes.
A later docs-only evidence update does not change the tested executable files.

### Bounded completed-job cleanup — prepared

At dev `ee43592a`, cleanup_done first selected old done Jobs, then deleted each
by id and returned the selected list length. A competing change after that read
could escape the status/age predicate; partial progress also made the selected
count different from actual deletions. It required a SELECT plus up to N DELETEs.

The prepared follow-up delegates one `QuerySet.delete` with status=done and
finished_at strictly before the computed UTC cutoff. Tenant scope or explicit
worker bypass comes from the existing guarded queryset. The returned count is
its actual affected-row count after that API completes, not a candidate count.
No per-row fallback, preload or immediate retry is added. Under PostgreSQL's
existing READ COMMITTED behavior, a concurrent row update that wins the lock is
rechecked against the DELETE predicate; changed status/age can spare the row.

This preserves the current 24-hour default and custom retention semantics,
including the existing zero-as-default behavior. NULL completion timestamps,
fresh/boundary done rows and pending/running/failed rows are not eligible.
Database/commit failures propagate; lost acknowledgement can leave rows already
deleted and is not proof of rollback or permission to replay a destructive call.
Existing database FK/cascade behavior is not redesigned. No batching policy,
retention approval, checkpoint retention guarantee or stronger recovery claim.

Prepared checks: six source-isolated delegation cases, nine existing-schema
PostgreSQL cases for tenant/status/age boundaries, bounded bypass, duplicate
cleaners, lock races, rollback and committed ACK loss. The owner ran these at
exact `1eb561fd` with results above; the author ran none. Injected pre-commit
failure rolls back, whereas committed ACK loss leaves the row deleted, raises
and does not issue another DELETE; it does not establish rollback.
The independent web job_delete guard merged as `c7b3693e` through PR #56, owner-tested `0cac44fd`
(11 standalone, exit 0), observed-tenant correction reviewed. This package does
not change that route or claim independently observed PostgreSQL coverage.

### Attempt-budget admission and operational stops — prepared

Source/base dev `59d775cc`. `_claim` and legacy start_running share one SQL
predicate: attempts >= 0, max_attempts > 0 and attempts < max_attempts. The
predicate is part of the locked SELECT/conditional UPDATE, before an increment;
first and last allowed acquisitions remain valid. An exhausted oldest pending
row cannot monopolize worker selection ahead of another eligible row.

reap_stale first conditionally stops stale running or due pending rows with an
unavailable budget (exhausted, NULL, negative counter or nonpositive limit).
It writes failed / actual stop time / a static "outcome unconfirmed" prefix plus
previous error, without resetting attempts, locks, start/run time, payload,
result/checkpoint reference or known/zero/unknown cost. This is queue operational
failure, NOT proof a provider rejected work or no effects occurred. No automatic
Task summary or Notification projection is added by this bulk stop.

A separate conditional UPDATE retains the existing below-budget stale requeue
policy; both writes repeat status/age/budget/tenant predicates. The method's int
still counts only requeued rows, not budget stops. These are two acknowledged
queryset operations, NOT an all-or-nothing combined transaction; failure after
a committed stop may leave that stop persisted. An exception/ACK loss propagates
without another write, inferred rollback, success receipt or handler invocation.
A refreshed heartbeat/completion that wins the row lock is not overwritten.

Automatic handle_prune excludes the conservative stop prefix, while ordinary
NULL-error history keeps its prior retention behavior. cleanup_done already
excludes failed rows. Explicit authorized operator deletion remains separate.
The marker is not a security token, replay approval, authenticated provenance or
billing ledger; false-positive matches conservatively retain evidence only.
This is temporary evidence preservation, not global retention/legal acceptance.

This slice caps attempts; it does NOT certify below-budget replay safe for every
operation, add ordinary heartbeats, reserve all paid attempts, abort a provider,
freeze a new scheduled Job, or alter checkpoint digest transport/part semantics.
A normal last active attempt may complete while its heartbeat is still fresh.
Stopped checkpoint Jobs retain references; exhaustion does not grant a new send.
Typed transient/permanent/uncertain policies, per-input progress reuse, honest
coverage/failure digest reporting and controlled manual retry remain follow-ups.
Manual retry must preserve old history/cost and represent a new explicit intent;
no ID-only re-arm/reset or silent retry of uncertain external effects is added.

Five existing stale-source checks are strengthened for both conditional writes,
not removed or made optional. Five new SQL/source-wiring cases and fourteen
existing-schema PostgreSQL cases are prepared for admission, stop, scope,
retention/NULL errors, competing reapers/heartbeats/completion, late claim loss
pre-commit rollback and committed ACK loss. None have been run by the author; earlier reaper green
results do not certify the new budget-stop semantics. Recovery/order/spend and
broader release gates remain OPEN. Old workers can still bypass the acquisition
cap, and old prune code can erase stop evidence; coherent worker/pruner rollout
is a separate deployment gate, not a permission to restart services here.

## Prepared acceptance specification — not executed tests

The cases below are specified, NOT implemented/run by this design package.
Future test files are proposed, not existing commands to execute now.

| Case | Required evidence |
| --- | --- |
| Valid success / skipped | Matching Job outcome committed; skipped has no success notification |
| Declared failure / known zero / unknown cost | Existing terminal policy, bounded audit and cost distinction preserved |
| Old worker finishes after reap, before new claim | Pending row is not changed; no task/cost/notification projection |
| Old worker finishes after a new claim | New generation's status/result/error/cost/attempts untouched |
| Old failure after new claim | No retry/terminal overwrite and no use of new attempt's retry count |
| Competing finalizers for one claim | Only one commits; other returns CLAIM_LOST |
| Changed task/type/tenant or missing row | No write or cross-tenant result disclosure |
| Heartbeat changes locked_at | Matching attempts/started_at claim still valid |
| Committed operator cancellation | Old worker cannot turn failed back into done or pending |
| Direct acquisition false / future-dated row | No handler; explicit NOT_ACQUIRED, not success |
| Direct acquisition then lease loss | No borrowed claim through unqualified reload |
| Outcome commit error/unknown acknowledgement | No success notification or immediate new retry/finalizer write |
| Task write/flush fails before shared commit | Job and Task database writes roll back; no success receipt or handler replay |
| Job committed, notification fails | Committed receipt preserved; no outcome retry |
| Two Jobs of one AgentTask finish out of order | Document limitation; no invented latest-run guarantee |
| Checkpoint digest / persisted reference after flag rollback | Existing digest execution/finalizer remains selected |
| Inline/CLI/API/web/agent lost claim | Cannot report a newer attempt as this caller's success |

Proposed first-slice tests: a new module-local source-isolated finalization unit
file plus a tenant-marked PostgreSQL claim-outcome file. Use
`tests/source_import_isolation.py`; do not globally replace production modules
or weaken existing outcome/privacy tests to satisfy mocks. Existing suites such
as returned-job-failures, job notifications, CLI run and digest integration are
regression candidates chosen according to the actual changed call sites, not an
automatic request to rerun all of them or the full suite.

PostgreSQL cases use distinct sessions inside one sequential pytest process,
fixture-owned tenant/job IDs and bounded synchronization; no sleep-only race
proof, global stale reap or deletion of historical rows. Existing shared
`test_schema` and private environment remain owner-managed. No schema
create/reset/drop/stamp/migration or provider/messenger effect is permitted.
The owner/local agent executes future prepared checks and returns exact SHA,
commands, result, duration and saved exit. Static review is separate evidence.

## Refactoring sequence and coordination

Only the current contract/audit is assigned here. Future implementation slices
must be individually selected from fresh dev, not automatically executed:

1. After JobManager ownership/integration is coordinated, introduce the immutable
   claim and ordinary outcome acknowledgement. Route all ordinary finalizers
   through it and separate post-commit errors. Preserve checkpoint dispatch.
2. Adopt acquired-claim returns in direct entry points and honest lost/not-acquired
   adapter responses. Check each touched surface and permissions explicitly.
3. Separately design ordinary heartbeats/lease ownership, safe stale recovery,
   cancellation fencing, task/job transactions and task-summary ordering.

No heartbeat interval alone makes replay safe. A healthy delayed worker can
continue external effects after lease loss; fencing DB outcomes does not fence
providers, collector writes or handlers' intermediate storage. Do not invoke a
live provider to test a concurrency design. These remaining limits keep PRD-05
open; accounting/reservation design stays in its assigned subsequent lane.

The earlier PR34/PR35/PR37 coordination was resolved through merged PR45.
Parallel model-layout lanes and deferred PR29 remain separate; this slice does
not modify their model or AgentTask manager files. Shared board/ledger edits are
reserved for the docs reconciliation owner; the implementation PR is the
allocation record until that reconciliation is coordinated. No new handoff or
backlog document is introduced. Historical owner verification of PR34 applies
to exact 81138d9, not to this implementation.

## Source references

- [Queue acquisition/outcome manager](../../app/models/managers/job_manager.py)
- [Dispatcher and direct entry points](../../app/jobs/dispatcher.py)
- [Job generation/cost columns](../../app/models/job.py)
- [Task trigger/result manager](../../app/models/managers/agent_task_manager.py)
- [Task summary fields](../../app/models/agent_task.py)
- [QuerySet/session ownership](../../app/models/managers/base_manager.py)
- [Session decorator](../../app/core/database.py)
- [Checkpoint claim, heartbeat and finalizer](../../app/services/digest/job_delivery.py)
- [Web Job retry/cancel adapters](../../app/web/jobs.py)
- [Release gates](../BUSINESS_PRODUCTION_READINESS.md#prd-05--honest-outcomes-and-bounded-task-execution-blocker)
- [Existing checkpoint integration contract](digest_job_delivery_handoff.md)

## Bounded heartbeat renewal foundation

Source audit on `e7fb385` confirms ordinary dispatcher execution does not refresh locked_at; the 30-minute reaper can requeue a still-working ordinary handler. Checkpoint digest has its own heartbeat protocol, not a general queue guarantee. This package introduces only JobManager.renew_claim; it does not wire a periodic timer or change replay/retention policy.

Contract: accept a validated immutable JobClaim and an aware timestamp (default UTC now). In normal scope the concrete integer tenant must match; explicit platform bypass still binds the claim tenant. One atomic UPDATE matches id, tenant, job_type, NULL-safe task, running status, attempts and started_at; it advances locked_at with GREATEST(existing, now) so delayed callbacks cannot shorten the lease, explicitly preserving updated_at and all result/cost/attempt/task evidence. RETURNING identifies a matching row; True is exposed only after the method's commit acknowledgement. False is claim mismatch, not proof of no handler effect; database/commit errors and BaseException cancellation propagate.

Author 15 new actual-source/module-local-double checks on `e7fb385` + patch are OK. Owner reports all nine PostgreSQL predicate/storage/rollback checks in `tests/test_job_claim_heartbeat_db.py` passed/exit0 on #87 original submitted head `f478544e6293279db4076e356a01bc93fea81a4f`; none were executed by the author. Runner SHA256 `f92dec4f12c44e42c42b589a440092ebce8a49a70999533fb1fb9910d5cb2067` independently matches the published file. Reported output has COMMIT=0, outer rollback and no synthetic leftovers; sequence advancement 705→707 is permitted. This is owner execution evidence plus independently checked artifact identity, not an independently run or durable-commit/inter-connection race proof. The evidence-only follow-up edits docs, leaving tested source and runner unchanged. It validates localhost:5432/social_manager/test_schema before app import, uses one outer transaction/savepoints, rejects actual engine COMMIT, rolls back all synthetic rows, and runs no bootstrap/global sweep/DELETE/DDL/provider/job. Sequence values may advance. Existing migration/table mismatch stops; no automatic setup/reset. This is NOT inter-connection concurrency or the full release suite.

Next dispatcher slice must separately define: heartbeat interval/clock behavior, ownership preflight, what a false/failed renewal does to new dispatch, cancellation/handler draining, and recovery of unknown external effects. A timer or DB generation predicate alone does not stop a paused worker's provider/intermediate writes; a lost/uncertain claim is not refund authority or safe automatic replay. Scheduler enqueue/next-run atomicity and the PRD-03 independent paid-attempt ledger remain separate open contracts. No status/schema/live action is authorized here.


## Ordinary pre-dispatch ownership gate

On baseline `9f58f2f6d5f8ce5c1299583a4fc2ca12b882f5af`, ordinary execution captured a claim but invoked its handler before checking whether that generation still owned the row. A paused acquired caller could therefore resume after a reaper/new generation and begin effects even though finalization would later be rejected. This slice closes only that initial dispatch window; ownership can still be lost after admission.

`_execute_ordinary` now awaits the merged #87 `renew_claim` using the immutable captured claim under the existing tenant scope, before entering the handler exception/retry region. Only the exact boolean True admits dispatch. False raises the existing JobClaimLostError; malformed acknowledgements or database/commit exceptions raise static JobClaimRenewalError (`claim_renewal_unconfirmed`), without exposing original error text. BaseException cancellation propagates. These exits invoke no handler, ordinary outcome write, retry scheduling or completion notification. Suppressed display chaining does not erase Python exception context; no claim of safe replay or prior-effect absence is made.

Successful admission preserves handler result/failure/returned-failure policy and claim-fenced finalization. Legacy digest receives this gate; enabled or persisted-reference checkpoint digest retains its own protocol without duplicate renewal. Unknown-handler finalization and all entry-point acquisition behavior remain unchanged.

Author evidence: `python tests/test_job_dispatch_claim_preflight.py` — 16 new actual-dispatcher/claim/result source checks with module-local manager/digest doubles and real asyncio task cancellation, exit 0, on `9f58f2f` + source patch SHA256 `d5e726afb2b5854693c55bf4104f764a06d070ed508ee5e2c2fa944dfad63bda`. Against the unchanged baseline, the same new checks yield 2 failures / 12 errors (including bounded admission waits); this is regression sensitivity, not a run of an old suite. Source AST comparison confirms every previous class/import/function except the new preflight prefix remains unchanged. No application/DB/provider/network/bootstrap or full-suite run occurred. #87 owner nine PostgreSQL checks remain evidence for its pinned renewal primitive, not execution evidence for this dispatcher revision.

This package adds no periodic heartbeat/timer, lease interval, stale-policy or status/schema change. A later supervised heartbeat slice must define mid-handler claim loss, uncertain renewal, simultaneous completion, cancellation/draining and unknown external effects; actual inter-connection reaper races remain unaccepted. Do not duplicate the completed #87/#74 checks or infer that this preflight alone protects a healthy long-running handler.


## Cooperative periodic ordinary heartbeat supervision

This bounded slice builds on delivered #87 renewal and #89 pre-dispatch admission. After strict initial admission, ordinary execution runs exactly one handler task plus one heartbeat task in the existing tenant context. Every 20 seconds of monotonic asyncio sleep, the heartbeat awaits full immutable-claim renewal before scheduling its next sleep; no overlapping renewal calls are launched. Legacy digest is supervised; checkpoint mode/persisted checkpoint reference retain their independent protocol.

Lost ownership, malformed acknowledgement, database/commit uncertainty or unexpected heartbeat termination wins over handler completion in the same scheduling turn. The supervisor requests handler cancellation, drains both local tasks, then propagates explicit claim loss or static renewal uncertainty outside ordinary handler-failure/retry finalization. No ordinary outcome, retry or completion notification is emitted by this caller. A handler result returned while suppressing cancellation is ignored. Handler-raised exceptions, even one whose type resembles ownership uncertainty, retain existing handler policy through a distinct internal stop signal.

Normal handler completion waits for any already-in-flight renewal acknowledgement rather than cancelling an unknown database write. Confirmed completion cancels the sleeping monitor and drains before finalization; renewal failure dominates even an already-completed result. Real caller cancellation stops both tasks and propagates only after drainage; repeated cancellation shields the drain, not handler/provider work. The immutable claim and tenant context survive observed-row mutation.

This is cooperative asyncio supervision, NOT a hard deadline or external-effect fence. Noncooperative handlers, a blocked event loop or hanging driver can delay drainage/renewal indefinitely; no receipt is returned while local children remain active. Claim loss does not erase in-flight provider/intermediate effects or grant safe replay/refund. Existing reaper/below-budget replay/status/attempt policy is untouched; independent-connection reaper races, worker crashes/paused clocks, provider idempotency and recovery/quarantine remain OPEN. A timer alone does not close PRD-05.

Author: `python tests/test_job_ordinary_heartbeat.py` — 22 NEW actual-source/stdlib asynchronous task checks OK/exit0 on `cdc82b9f250c4d7ac1ef3be81c2fc078e286c75c` + source patch SHA256 `80278860fa15aa0f3800b1b78524f9a3299a383bad6d45ad8f5c261be001415c`; four selected new regression checks fail on baseline (1 failure/3 errors). No old suite, app/DB/provider/network or submitted/merged-head execution. Owner reports `tests/test_job_ordinary_heartbeat_db.py` passed all 8 cases/exit0 on original #91 submitted head `4a3eacd37b759d23cba6fc2747cad56a12363d73`; SHA256 `ed0dd39034f4ca729b072dc32fff6c6df314cc247b0538ee6bfae5a0c20e7dbb` independently matches published bytes. No execution by this coordinator. Owner-reported output confirms COMMIT=0, outer rollback/no synthetic leftovers and unchanged row counts; tenant sequence 707→709 only. This chat excerpt/environment report is not an independently inspected saved runtime log. The evidence-only follow-up changes docs, not dispatcher/tests, so no rerun is requested. It reuses only the earlier host/schema environment guard, never the earlier nine tests; standalone command `DB_TEST_SCHEMA=test_schema /Users/admin/Projects/social-media-ai/.venv/bin/python tests/test_job_ordinary_heartbeat_db.py`. One synthetic own/foreign tenant pair, actual renewal/finalizer and normal tenant-scoped reaper with pre-execution exact synthetic tenant predicate assertion, controlled coroutine/timer interleavings, one existing caller connection/outer rollback/savepoints and engine-COMMIT rejection. No bootstrap/seed helper/global bypass/DELETE/DDL/real handler/provider action; sequences may advance. This tests actual driver behavior with savepoint/task interleavings, NOT independent-connection races, durable commit proof or production 20-second timing. Pinned owner acceptance permits integration of this bounded supervision slice, not complete PRD-05 closure. Do not repeat #74/#87 checks or extend single-connection evidence to independent-connection races/production timing.
