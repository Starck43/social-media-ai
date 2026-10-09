# Next-session compatibility pointer

Task allocation lives in the [current-stage board](README.md), reached through
[PRODUCT_PLAN](../PRODUCT_PLAN.md) and [ROADMAP_INTEGRATED](../ROADMAP_INTEGRATED.md).
This path is retained for existing links; it is not a competing backlog.

## Current occupied lane: draft PR #22

Branch `ai/identity-permissions-boundary`, synchronized with dev `274cb2c` by
`8f09a83` (earlier `0234c21` by `f28b70f`). Identity (`b1e5d5`), fresh actor-bound confirmation (`f4f3fd`) and safe
PENDING action preview (`ccb8905`) are prepared/pushed, NOT merged or accepted.
46 new standalone test methods plus earlier policy/dispatch and updated DB
regressions are written, NOT run by the agent. Parallel VIEWER fixture edits
are preserved; required deleted imports are restored in the follow-up.

Read [the existing detailed handoff](identity_permissions_handoff.md) for exact
contracts, compatibility risks, owner commands and evidence. Next: local
verification of this new block and remaining coverage/compatibility review,
not a replay of completed substeps. Recheck dev/open PRs before further edits.
Latest owner focused setup/privacy/identity/permission checks passed, but their
exact tested SHA is not supplied. Separate dev `274cb2c` LLM/output/learning
report: 61 passed. Neither certifies the new runtime block or collect_now gate
`ec69981` (six unrun regressions). Full-suite completion remains open. Use a
separate worktree; owner-local dirty UI files stay untouched. No automatic merge, deploy or live send.

Owner sequence remains rights first, general queue second, attempt-accounting
and budget-reservation DESIGN third (schema approval separate). No personal
router, digest reintegration, broad test bypass or unapproved migration.

## Integrated parallel work

PR #18 returned failures, #19 readiness/schema guards, #20/#23/#25 bounded
privacy, #26 documentation navigation and #27 operator runbook are merged.
Merged is not tested/deployed/accepted. Preserve their code and documentation;
use the [ledger](../IMPLEMENTATION_STATUS.md) and board for current evidence.
The operator runbook's eight prepared tabletop cases were not run by this agent.
