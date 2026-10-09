# Next-session tasks after dispatcher privacy integration

Checkpoint: dev `57b5612f28bc8d9621cf21670babe8a4195d536e`, PR #20 merged
2026-10-09 with explicit owner approval. Fetch fresh dev before any new work;
owner actively edits the project. Chat/notification UI and chat asset fix
`9d83c9a` are preserved. This list is a plan, not new implementation authority
for every package, live activation or future merge.

## Current owner assignment — draft identity PR #22

PR #22 branch `ai/identity-permissions-boundary` synced with dev `eb49d1d`
(including merged PR #23/#24/#25), conflict resolution preserved both work lanes.
Owner fix `963bf4f` is retained in history; `4735859` replaces its arbitrary-user
fixture with a dedicated active actor (no role/superuser), cleanup and owner
scope only around task arrangement. Handler asserts no inherited actor/grant.
New setup/assertions are PREPARED, NOT RUN; PR stays draft, NOT merged into dev.

Owner reported 24/24 bootstrap unit, 33/33 focused readiness and prior privacy
2/2 passes; same DB public/test_schema isolation. Full suite timed out at 120s.
These are prior owner results, not acceptance of the new fixture; tested SHA/logs
not supplied. Read [the current handoff](identity_permissions_handoff.md).

Next: owner/local retests NEW fixture and permission regressions, records tested
SHA/commands/output, completes remaining identity/legacy arrange compatibility.
Priority: identity/rights first, general queue second, attempt-accounting and
budget-reservation DESIGN third, with separate schema approval. Do not restore
anonymous allowances, weaken security tests, redo digests or add personal routing.
Parallel privacy inventory below remains separate; no final merge authority.

## Latest owner result — focused fixture rerun passed

Owner confirmed the requested `test_dispatcher_log_privacy_db.py` rerun passed
with the narrowed fixture (two parameterized cases). OWNER-REPORTED; exact tested
SHA/output not supplied. No agent test execution. Retesting that focused fixture
is no longer an outstanding reported failure; remaining permission/identity
regressions and completion of the timed-out full suite still need evidence.
PR #22 remains draft/open, not merged. Continue compatibility review, not queue
implementation or automatic merge.

## Next linked identity tasks — dispatch unit prepared

The next small unit in draft PR #22 now gates declared permissions before
call_tool/execute handlers. Ten actual-source tests prepared, NOT RUN; undeclared
rights/confirmation/DB freshness remain open. No runtime guard change or live send.

Continuation order:
1. Owner verifies dispatch + earlier policy/regressions and completes full suite.
2. Resolved-tenant runtime identity and eager active User rights, no personal router.
3. Actor/tenant/current-right confirmation binding and membership/role revocation.
4. action_send helper/handler registration bug and explicit action tool rights,
   without incidental live posting; preview/approve/send contract separate.
5. Raw manager/legacy setup coverage; NULL-role ownership reconciliation policy
   requires separate approval before any migration or automatic identity linking.

The narrowed privacy fixture rerun is owner-confirmed passed. Do not repeat
bootstrap/digest integration. Read the [detailed handoff](identity_permissions_handoff.md).
Current next CODE unit is runtime identity, after coordinating parallel ownership.
Queue and budget schema/design remain later packages. No final merge permission.

## Already implemented — do not rebuild

PR #12 bounded default-off digest checkpoint integration; PR #16 fixture fix;
PR #17 typed digest/learn/reflect boundaries; PR #18 explicit returned failures;
PR #19 test-schema guard, redacted diagnostics, API liveness/readiness;
PR #20 dispatcher-owned log/failure-notification privacy. Merged does not mean
accepted/deployed. API /health returns 503 on DB failure; /livez is process-only.
Personal multi-workspace chat routing remains a PLAN, not a shipped feature.

## Parallel bounded privacy continuation — merged, acceptance open

PR #25 merged as `eb49d1d`; original baseline `3373a1e` after PR #24,
branch `fix/staged-attempt-log-privacy`, tests/handoff `78a86be`.
Merge verified; deployment/application-test acceptance still open.
Code `9bb566a`: only `_count_failed_staged` warning, safe event/category/source ID.
Nine helper/mocked-storage cases prepared, NOT RUN; static AST and new-file
whitespace/line-length checks only. Existing attempt-count/hash/fallback/cleanup
behavior, dispatcher PR #20, retirement PR #23, UI and runtime guard unchanged.
See [attempt-warning handoff](staged_attempt_log_privacy_handoff.md).

Next: owner/local acceptance results for the merged warning. Later consider
one `handle_prune` stale-items warning or one
coordinated collect path, separately. No live calls or deployment permission.

## Previous bounded continuation — merged, acceptance open

Fresh dev checked: `98e3aad` after PR #23, merged on explicit owner instruction.
The selected first handler surface is
`_retire_staged` failure warning only; code `be3f67c` on
`fix/staged-retirement-log-privacy`. Seven helper/mocked-storage cases are written,
NOT run; tests/handoff commit `a332a98`. This is integrated, not deployment or
acceptance. Dispatcher, UI and runtime guard untouched. No new tests executed.
Follow-up inspection: GitGuardian/Kilo succeeded for PR #23/#24 heads. This is
not application-test evidence; the seven prepared helper cases remain unrun.
See [selected-surface handoff](staged_retirement_log_privacy_handoff.md).
Do not rebuild this merged warning. The `_count_failed_staged` warning is now
merged separately via PR #25; acceptance remains open. Other handler/provider/ORM logs and stored
errors remain open; preserve partial collection outcomes.

Parallel open PR #22 is the identity/permissions branch; this task does not merge
or supersede it. Both branches edit the implementation tracker and this list.
Preserve both records when synchronizing; refresh dev before any further work.

## Next small sequential packages

1. **Owner/local acceptance:** focused PostgreSQL and notification regressions,
   then full suite on isolated DB_TEST_SCHEMA. Current package has historical
   authoring evidence of 13 privacy + 25 mocked outcome passes, but 2 new PG cases
   are unrun here. PR #19 has 8 prepared ASGI cases unrun in the sandbox. No new
   test run occurred at merge. Collect exact tested head/commands/results; do not
   infer new-package acceptance from the owner's earlier all-tests-pass report.
2. **One remaining handler-log path:** start with inventory in
   app/jobs/handlers.py (collect/analyze/staging/retention); `_retire_staged` warning
   is now merged via PR #23 and must not be rebuilt. Remove raw exceptions
   and tracebacks from one selected surface with safe correlation/category tests.
   Preserve collector partial outcomes and runtime guard; coordinate dispatcher
   overlap with queue work. This does not sanitize stored errors automatically.
3. **Task-outcome presentation audit:** compare actual persisted results with
   web/notification language, static failure codes, checkpoint uncertainty and
   unknown versus confirmed-zero cost. Fix only evidenced mismatches. Reserve
   owner UI files first; do not invent new partial statuses or unsafe resend UI.
4. **Operator diagnostic/recovery runbook:** dependency failure, stuck jobs and
   uncertain/blocked deliveries. Observation and escalation first; no clearing
   receipts, reset/drop or automatic replay of ambiguous external effects.
5. **Targeted documentation consistency:** stale entrypoint/LLM paths and
   initialization claims in AGENTS.md; remaining baseline-versus-current wording
   in readiness documents, including their earlier PREPARED privacy snapshot.
   Current integration authority is the implementation tracker and PR #20.

For the parallel privacy lane, item 2 follows item 1 evidence. This identity
branch instead follows the owner assignment above.
Do not bundle all packages into one broad refactor.

## Important complex packages — separate chats/branches

Priority A: **Fail-closed identity and rights.** Explicit trusted worker/operator
context versus unresolved interactive identity; tenant-owner versus platform
rights; known/missing/legacy roles, cross-tenant access and revocation. First map
chat/web/API/admin/tool surfaces, then prepare a negative rights matrix. Keep the
owner guard and existing legitimate worker paths; legacy role migration needs
explicit policy/approval. Safety priority before opening a public pilot.

Priority B: **General queue/scheduler correctness.** Claim CAS/leases/heartbeat,
Job+AgentTask outcome atomicity and concurrent enqueue+schedule advancement;
crash/concurrency cases. Checkpoint-specific fencing is not queue-wide fencing.
Coordinate dispatcher/managers/models to avoid overlapping log/UI edits.

Priority B: **Atomic memory batches/watermarks.** Transactional learn/reflect
updates and concurrent watermark protection; strict shape/evidence validation
is already merged and does not solve atomicity or races.

Priority C: **Per-attempt spend ledger and reservations.** All callers/retries/
fallbacks, explicit unknown pricing and concurrent admission; consistent USD units.
Design/schema approval BEFORE implementing a migration or billing claims.

Also open: production profile/one migrator/private DB/restore evidence,
retention and offboarding/revocation, actor-bound external-action approval,
full privacy audit and real transport/recovery acceptance before sender activation.
No connectors, embeddings or external tracing prerequisite for these safety tasks.

## Working agreement

- Use fresh dev; separate small thematic branches, no direct push to dev.
- Prepare tests ONLY; owner/local agent runs them. Never delete or weaken tests.
- English code/docs/commit messages; Russian chat.
- Every commit body contains Owner handoff: exact local commands/environment,
  expected behavior, written/unrun versus actually observed evidence, limits.
- Update implementation tracker and concrete resume point after every package.
- Common POSTGRES_URL + separate DB_TEST_SCHEMA. No second DB requirement;
  no reset/drop and no parallel pytest processes sharing one test schema.
- Preserve owner runtime guard, UI and active parallel work. Coordinate overlapping
  files before identity/queue/memory work. New merges need explicit instruction;
  no live migration/provider/messenger/sender activation without separate approval.
