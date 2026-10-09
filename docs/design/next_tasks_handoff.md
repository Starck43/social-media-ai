# Next-session tasks after dispatcher privacy integration

Checkpoint: dev `57b5612f28bc8d9621cf21670babe8a4195d536e`, PR #20 merged
2026-10-09 with explicit owner approval. Fetch fresh dev before any new work;
owner actively edits the project. Chat/notification UI and chat asset fix
`9d83c9a` are preserved. This list is a plan, not new implementation authority
for every package, live activation or future merge.

## Already implemented — do not rebuild

PR #12 bounded default-off digest checkpoint integration; PR #16 fixture fix;
PR #17 typed digest/learn/reflect boundaries; PR #18 explicit returned failures;
PR #19 test-schema guard, redacted diagnostics, API liveness/readiness;
PR #20 dispatcher-owned log/failure-notification privacy. Merged does not mean
accepted/deployed. API /health returns 503 on DB failure; /livez is process-only.
Personal multi-workspace chat routing remains a PLAN, not a shipped feature.

## Next small sequential packages

1. **Owner/local acceptance:** focused PostgreSQL and notification regressions,
   then full suite on isolated DB_TEST_SCHEMA. Current package has historical
   authoring evidence of 13 privacy + 25 mocked outcome passes, but 2 new PG cases
   are unrun here. PR #19 has 8 prepared ASGI cases unrun in the sandbox. No new
   test run occurred at merge. Collect exact tested head/commands/results; do not
   infer new-package acceptance from the owner's earlier all-tests-pass report.
2. **One remaining handler-log path:** start with inventory in
   app/jobs/handlers.py (collect/analyze/staging/retention). Remove raw exceptions
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

Start a new implementation chat with item 2 after checking item 1 evidence.
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
