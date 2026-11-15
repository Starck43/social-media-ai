# P21-T13 — Increment A review, acceptance withheld

**2026-11-14 · NOT ACCEPTED · no merge or task closure.**

Task: [P21-T13](../../tasks/p21-t13.md), [PR #488](https://github.com/Starck43/social-media-ai/pull/488),
branch `feat/p21-t13-catalog-storage`, target `dev`.
Code candidate: `1beb72cbbd1faff4f27eb62e26adc2eb9a28407e`.
Registry owner remains `opencode-p21-t13`; required DB runner is `opencode-local`.

## Authorization and evidence reuse

The owner explicitly appointed the executor as auditor, requested autonomous
closeout under project rules, and prohibited repeating green tests. This is an
owner-authorized role exception, not independent review. It authorizes publication
of this scoped continuation and review; it does not waive named DB checks,
complete increment B or authorize public migration apply.

No pytest/unittest or previously green static check was rerun during this audit.
The committed candidate's source manifest matches the prior tested working-tree
manifest exactly:
`b42a96fb0ff15c1b6c408cf43b7ae02b2c26048779ddf79c0c497aa88f9fc1d9`.
The verification used committed file bytes, sorted changed paths under `app/`,
`migrations/`, `scripts/`, `tests/` since `80891d7c`; each manifest line is
`<sha256(file bytes)>  <path>\n`. Verification exited 0. This links the existing
173-pass focused pytest and 17-pass isolated unittest evidence to this code
revision; documentation-only follow-ups do not alter that code.

Fresh `git fetch origin` returned exit 0. `origin/dev` still resolves to
`179db887a9a0d7ea580807d9e96a88999e950b3f`, identical to the recorded DB-free
single-head proof (`0155` → candidate `p21t13_catalog_storage`). The graph proof
was reused, not rerun. No schema head, DDL or migration cycle was queried.

## Review scope

Reviewed the published storage diff and continuation, grant service/admin write
paths, capability/custody mappings, migration/guard changes, and existing check
evidence. The service binds actor identity, rereads active superuser authority,
locks the platform catalog, enforces immutable grant membership and clears group
defaults before flush. Admin choices are scalar metadata; CSRF and service-only
writes remain mandatory. Personal INSERT compatibility derives custody only
when omitted, with no platform inference or explicit-ID rewriting; retirement
does not delete/rebind managed selections.

This source review is not PostgreSQL proof of trigger compilation, partial-index
conflict behavior, ORM RETURNING, constraint order or races. No passing DB result
is inferred from source/mocks.

Documents actually consulted: P21-T13 row/card (increment split, acceptance,
continuation evidence); PR #488 body/comments/reviews (published revision and
missing DB evidence); `docs/process/COLLABORATION.md#tasks`, `#state-machine`,
`#evidence`, `#acceptance`, `#tests`, `#database`,
`#migration-numbering-and-parallel-tasks`; `docs/audit/README.md` (report placement).
The existing session's README/DESIGN/test-convention reads were reused.

## Findings preventing acceptance

1. **Required PostgreSQL evidence is absent.** PR #488 has no local-runner result
   or review covering the continuation. Duplicate inventory, retained-row backfill,
   Alembic cycle/refusal cases, real custody/grant/credential guards, default races,
   legacy personal CRUD/RETURNING and before/after inventory plus cleanup remain
   NOT RUN. No DB target/schema was selected by this executor. Required action:
   `opencode-local` runs the card's exact-revision handoff under exclusive ownership
   and records target, commands, counts/exits and zero synthetic residue.
2. **The whole-task acceptance gate includes undelivered increment B.** The card
   explicitly forbids READY before consumer migration. Fresh
   `gh pr view 486 --json state,mergedAt,headRefOid,url` confirms P21-T12 is now
   MERGED (`2026-11-14T00:18:36Z`, head
   `95d3eac0f457de5b60c2a15a3140a32856d7f229`). This removes the prerequisite gate;
   it does not deliver funding-aware consumers, plan intersections or revision
   invalidation in this PR. Implementing B is feature execution, not an audit
   correction. Do not silently descope B, remove the row or call A whole-task DONE.
3. **The historical guard suite is not green.** Its recorded result remains exit
   1, 13 passed/2 failed in the unchanged `0154` parser. The passing P21-T13-only
   parity test does not waive the two historical checks. Resolve the parser/check
   classification or obtain an explicit named waiver; never relabel it PASS.

The existing greens cover the candidate's code, not the future combination with
new increment-B/accepted-T12 consumer work. New behavior needs its assigned
focused acceptance evidence; routine repetition of unchanged greens is prohibited
by the owner.

## Disposition and handoff

Keep IN_PROGRESS: the DB handoff and consumer implementation are actionable, not
owner-deferred. Keep the PR draft, task card and branch intact. No READY, merge,
DONE, archive move or ROADMAP-row deletion is authorized by this verdict.

The auditor can finish closeout only after the required evidence/scope gate is
met, then verify exact-head integration into fresh dev and perform owned cleanup.
No public apply is authorized. This review is a diagnostic, not an accepted audit.

Worktree inventory shows this owned task worktree is unmerged and must be retained;
other listed worktrees/branches are foreign and untouched. Owned scratch remains
while the task is open; no child-session manual cleanup is claimed. Automatic
child cleanup is configured. Cleanup cannot manufacture acceptance.
