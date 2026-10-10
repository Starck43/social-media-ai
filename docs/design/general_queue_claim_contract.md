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
The cancellation follow-up below is PREPARED from that dev; no author-run tests.

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
Cancellation authority is distinct from worker ownership. The prepared operator
writer below conditions its mutation on the observed generation; it does not
acquire the worker's claim or establish authority merely by constructing a value.
Web guards and manager tenant-context checks remain required.

### Bounded operator cancellation follow-up

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
the author; owner execution uses only the existing reviewed test environment.

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
