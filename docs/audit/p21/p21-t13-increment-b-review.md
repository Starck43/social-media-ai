# P21-T13 A+B candidate review — NOT ACCEPTED

Date: 2026-11-14. Owner-appointed executor/auditor: `opencode-p21-t13`.
Source candidate: `10d9b0a28ba40471b3e7bf24701225f05539993b`.
Branch: `feat/p21-t13-catalog-storage`; [PR #488](https://github.com/Starck43/social-media-ai/pull/488)
targets `dev`, remains draft. [Task card](../../tasks/p21-t13.md) owns detailed
mapping, documents actually read, commands, revision hashes and deviations.

## Verdict

**NOT ACCEPTED / BLOCKED.** The native configured tester cannot execute the new
B checks: both build wrapper and direct tester returned HTTP 402
`Billing: insufficient_funds` before execution. Five new offline modules and
new real-B PostgreSQL service/mapper/concurrency checks remain **NOT RUN**.
Shared guard/parser reconciliation with [P11-T37 / PR #490](https://github.com/Starck43/social-media-ai/pull/490)
is required before integration. No green result is invented or repeated.

## Reviewed corrections

- Typed fresh holder/custody/grant resolution, no actor-plan substitution,
  complete per-capability eligibility and deduplicated metadata choices.
- Explicit dormant read scope, secret-free SQL projections, deterministic grant
  defaults and personal priority independent of grant-table columns.
- Second fresh grant read after reservation; refusal settles `not_dispatched`
  and releases the lease. No activation/credential or tenancy edit.
- Generation defaults separated from model output hard ceilings and approved
  limits; context size never becomes an output budget.
- Bootstrap mirrors immutable `0154` objects. Static tuple/correlation and
  named-body-format parsing preserves complete SQL and fail-closed restrictions;
  migration bodies/assertions remain unchanged.

## Evidence and limits

- Original A offline evidence is retained with its tested source manifest;
  it does not certify changed B/parser/bootstrap files.
- [Local A PostgreSQL evidence for `7fda23a2`](https://github.com/Starck43/social-media-ai/pull/488#issuecomment-6881138855)
  covers storage/backfill/guard/CRUD/first-save race/downgrade/re-apply. Current
  migration, models, admin, catalog-admin and existing DB acceptance test are
  byte-identical (`git diff 7fda23a2 -- <those paths>` exit 0). Do not repeat
  that green cycle. Original exact commands and full inventory/residue evidence
  require a portable supplement if retained; B services are explicitly excluded.
- Authorized parser focus: 3 PASS / 1 FAIL; only that failed body-parity node
  and new named-format regression ran next: 2 PASS, no prior green repeated.
  Exact commands/source SHA256 are in the task card.
- Black/isort PASS on changed Python paths; `git diff --check` PASS.
- Native implementation verification outside the assigned no-test scope is
  recorded as a deviation, not approved tester evidence.

## Required next actions

1. Restore the configured tester route or obtain an explicit supported alternate
   route assignment; run only the five new B modules at the candidate source.
2. `opencode-local`: new real B grant/metadata/ORM service and revoke/downgrade/
   pool workload checks on a task-owned isolated schema; exact commands, complete
   before/after schema-inventory diff and zero residue. No public apply.
3. Reconcile shared files with PR #490: one copy of identical `0154` objects,
   preserve both lanes' tests and T13 named-body/nested-loop parsing.
4. Review the exact resulting PR head with required PASS evidence before READY,
   acceptance, merge and verified-integration closeout. No merge/row deletion now.

Owned finished parser/implementation/tester sessions were deleted via V2 API;
older `SessionNotFoundError` cases remain historical housekeeping, not blockers.
The unmerged task branch/worktree are retained as the resume point.
