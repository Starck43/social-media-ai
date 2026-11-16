# Local worktree takeover

The owner transferred unfinished local worktrees to the coordinating Codex on
2026-10-24. Verified integration base: local `dev` and `origin/dev` both
`0f07e179f21c6a6e18a618169e700b64d15c50b5`. Task acceptance remains in
the phase files; this inventory does not accept pending work.

Concurrent P9 development later occupied the primary checkout. Takeover work
was moved by an explicit owned-path patch and file hashes into
`/private/tmp/social-takeover-final` on `dev`; only those exact owned changes
were removed from the primary checkout. P9 files, branches and tests were
preserved. The P9 implementation is outside this takeover's acceptance.

## Recovery

Before retiring dirty copies, original HEADs, staged and unstaged binary
patches, and nonignored untracked files from 14 dirty worktrees were saved to
`/private/tmp/social-worktree-recovery-20261024/manifest.json`. The manifest
records the archive path and SHA-256 for each worktree. Private local artifacts
from the Platforms, collection-stage-D, jobs and signal-test worktrees were saved separately in
restricted local archives; environment and virtualenv links were preserved
without dereferencing them. These local recovery files must remain available
until takeover is complete. They are not repository deliverables or validation
evidence.
`local-branches.bundle` also preserves remaining local branch/tag history;
`git bundle verify` passed (SHA-256
`7f866cfd6d08cc1583bff090608049d3ab112e7bddfd913a6ba4fe31cad7c5d0`).

## Retired copies

| Former worktree | Disposition |
| --- | --- |
| `/private/tmp/social-p6-t03` | Clean; HEAD `6d5c5ac` already in published dev. |
| `/private/tmp/social-p6-t03-merge-dev` | Clean; HEAD `3fe25db` already in published dev. |
| `/private/tmp/social-pr229-postmerge` | Clean; HEAD `2cdc796` already in published dev. |
| `/private/tmp/social-p5-p8-final` | Clean; HEAD `a3f37e0` already in published dev. |
| `/private/tmp/social-ready-collection-limits` | Working patch reviewed: current dev includes the limits and additionally preserves task context, deduplication and parent counting. Patch archived. |
| `/private/tmp/social-p5-thread-context` | Six changed file blobs identical to dev; remaining receipt and selection-store differences superseded by current selection-conflict validation and parent counting. Patch archived. |
| `/private/tmp/social-p8-t27-acceptance` | Platforms implementation, fixtures and acceptance suites integrated; other differences are the newer personal model settings and tenancy contract in dev. Patch and local artifacts archived. |
| OpenCode `p5-t14-stage-d` | Clean; HEAD `b8e6565` already in published dev. Local artifacts archived. |
| `/private/tmp/social-ready-signals-tests` | Tests integrated or superseded by final source-deletion, receipt and operator-identity fixtures; patches and local artifacts archived. |
| `/private/tmp/social-jobs-p1-t02` | Manager and behavioral tests integrated; dev additionally validates scenario binding and source ownership under locks. Patches and local artifacts archived. |
| `/private/tmp/social-admin-current-snapshot` | Mixed source changes superseded by current tenant/owner/delete guards and counters; useful acceptance changes ported separately. Patches and local artifacts archived. |
| `/private/tmp/social-dev-dirty-preserved` | Mixed source/doc/status bundle superseded; operator-role test and corrected cost documentation preserved separately. Patches and local artifacts archived. |
| Old OpenCode `t12` | Original platform-role fix already integrated; unused hardcoded owner argument superseded by trusted catalog owner resolution. Patch and private artifacts archived. |
| Old OpenCode `audit` | Ad hoc replay archived without execution. Required task contracts are covered by the accepted task suite; optional historical replay cases do not add a runtime requirement. |
| `/private/tmp/social-admin-legacy-acceptance` | Useful catalog/billing fixtures adapted to current owner and UID rules; redundant CLI persistence suite superseded by six passing catalog checks. Original files archived. |
| `/private/tmp/social-p10-queue-test-repair` | Same-name task edit regression ported and passed in the isolated worktree. Original patch archived. |
| `/private/tmp/social-p10-t11-audit` | Current source audit and probes committed at `94e9fff`; historical audit preserved. Original files archived. |
| `/private/tmp/social-p10-t12-acceptance` | Task candidate adapted and accepted at `6c581bf`, 41 passing tests. Original files archived. |
| `/private/tmp/social-p10-t13-acceptance` | CLI candidate adapted and accepted at `0693c698`, 40 isolated and six PostgreSQL checks. Staged/unstaged original patches archived. |
| `/private/tmp/social-p10-catalog-final-acceptance` | Clean historical candidate; commit history preserved in recovery bundle. |
| `/private/tmp/social-p10-t12-t13-with-p3-ui` | Clean historical integration candidate; commit history preserved in recovery bundle. |

All 21 original auxiliary worktrees were retired. All inactive local branch
refs were removed after ancestry/content review and exact bundle verification.
The remaining worktrees/branches belong to the current dev integration and
active P9 lanes. Remote branches and open PRs were preserved.

## Consolidated outcomes

| Worktree group | Integration action |
| --- | --- |
| P10 chat/CLI | Accepted at `6c581bf` and `0693c698`; phase DONE, ROADMAP rows removed. |
| P10 audit | Accepted at `94e9fff`; 17 passed and four strict expected failures document remaining gaps. |
| Admin catalog | Eight exclusive PostgreSQL/ASGI checks plus later exclusive mock POST regression; P8-T16 accepted at `7509f8f`. |
| Admin probes/billing | Trusted actor bug fixed at `7509f8f`; three exclusive regressions passed. Full 45-case run passed with foreign-schema overlap, so P8-T15/T17/T18/T20 acceptance remains open. |
| Restored regressions | Web operator-role tests: 10 passed / 13 subtests. Same-name task edit: one passed. |
| Credential route discrepancy | Actual doubled route documented; task-allocation question Q-10 remains open. |

## Validation boundary

No schema, migration, runtime, live-provider or deployment operation was performed
by this takeover. The owner authorized scoped Codex-subagent test and local
commit execution after the original OpenCode provider exhausted its quota.
Kilo subsequently completed the CLI isolated scope: 40 passed. Additional
isolated checks: interface audit 17 passed / 4 strict xfailed; web permission
contracts 10 passed. PostgreSQL CLI catalog acceptance: 6 passed.

The first chat run found a pre-flush scenario validation defect and two test
fixture defects; the corrected isolated run passed 41/41. Admin fixtures required
adaptation to personal catalog ownership. The old billing fixture retained 44
empty synthetic tenants after setup failures. Read-only reconciliation proved
the exact run-owned set (44 rows in the exclusive run's timestamp window, zero
dependent rows); canonical manager cleanup deleted those exact 44 identities.
Postflight found zero remaining candidates, zero active writers, and unchanged
schema revision `0109`. No prefix/range deletion or schema reset was used.

Four later nonfinancial fixture sets were also deleted by exact manifests
after dependency checks; no financial evidence was removed. The last admin
run produced complete local log/JUnit evidence at
`/private/tmp/social-takeover-final-evidence/`. It records three exclusive
regression passes, 45 passing cases with observed foreign-run overlap, and
one exclusive same-name task regression. The JUnit files retain 47 fixture
manifest records: 15 with account/policy/cutover markers and 32 without those
markers. Those labels are not a dependency/cleanup proof; all retained rows
require exact identity/dependency checks before any future data cleanup.
No such queries or cleanup were performed after the owner released the DB
lane to other chats.

P10-T11/T12/T13 and P8-T16 are closed with their scoped evidence. Remaining
admin/billing acceptance requires an exclusive rerun; its tasks stay open.
No broad green suite or P9 acceptance is claimed.
