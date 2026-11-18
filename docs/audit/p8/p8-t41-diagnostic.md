# P8-T41 — independent audit diagnostic (not accepted)

Audit date: 2026-11-18. Auditor: independent audit subagent. The executor of
P8-T41 was the main OpenCode session, so self-acceptance is not permitted.

Verdict: **not accepted — the ROADMAP row stays IN_PROGRESS**. No DONE, no
archive move and no row removal were published. Integration is verified; the
blocker is required-check evidence at the accepted revision.

## Candidate and integration

- Accepted source revision: `aa470623` (`fix(p8-t41): unblock the integrated
  VK journey from chat`); branch-head docs commit `82eb5a61`.
- Integration is an explicitly owner-authorized direct merge into `dev`, merge
  commit `e6767991` (`Merge branch 'test/p8-t41-integrated-journey' into dev`).
  There is no PR and no `mergedAt`; this is the recorded direct-integration
  exception, not an invented PR.
- `git merge-base --is-ancestor e6767991 origin/dev` → exit 0.
- `git ls-tree origin/dev -- app/admin/view_modules/llm_catalog.py app/agent/toolset/tasks.py app/services/billing/payer.py`
  → blobs `656b5261`, `386afd00`, `5bf8965e`.
  `git diff e6767991 origin/dev -- <those three files>` is empty, so P8-T41's
  delivered source is byte-identical on fresh dev. `operations.py` is the
  separately accepted P11-T41 blob `02e1df75`.

## Findings

1. **Required check failed at the accepted revision.** On exact revision
   `e6767991`, `python -m scripts.check_p8_t41` → 45 tests, **1 FAILED**:
   `tests/tooling/test_p8_t41_surface_contracts.py::SurfaceSeamTests::test_all_five_writers_retain_shared_plan_preparation[surface=app/admin/view_modules/operations.py]`,
   `AssertionError: {'resolve_task_plan_create', 'project_task_plan_create'} not
   less than or equal to {'_prepare_task_payload', ..., 'on_model_change', ...}`.
   The seam calls exist only inside the private helper `_prepare_task_payload`,
   which the AST-only contract does not accept. On current dev the same check is
   45/45 OK only because the separately accepted **P11-T41** inlined those calls;
   that fix is not part of P8-T41's accepted revision and cannot be credited to
   it.
2. **Required DB/P8-T38 paths fail.** On current dev the 11 required paths report
   9 failed / 94 passed. All nine are
   `tests/social/test_p8_t38_execution.py::VKSelectedRequestTests`, each
   `ImportError: cannot import name 'NarrowedAccess' from
   'app.services.social.credentials'` raised while loading `vk_client.py:14`
   through `load_isolated_source`. The test double `_module(...)` (line ~43)
   never declares `NarrowedAccess`, although `vk_client.py` imports it and the
   real frozen dataclass lives at `app/services/social/credentials.py:54`.
   `NarrowedAccess` predates the accepted revision (`cebd033e`, an ancestor of
   `e6767991`), and the test file is unchanged between `e6767991` and
   `origin/dev`, so this mismatch also affected the accepted revision. No
   exact-revision evidence exists for the 11 required paths passing.

## Rule basis

- State machine: the row is IN_PROGRESS. `IN_PROGRESS → READY` is the executor's
  gate and needs every required acceptance check passed with evidence in the
  card/PR; the auditor can only apply `READY → DONE`. No valid READY exists here.
- Evidence/Acceptance: "A required test not run is not PASS"; "Green unit tests
  cannot replace a failing or unrun DB scenario"; "Required DB scenarios need
  exact-revision local evidence"; "Confirm the current candidate revision and
  that evidence matches it". A later fix from another task (P11-T41) or an
  auditor correction does not make a required check pass at the accepted SHA.

## Checks run by this audit

All commands were run on Python 3.14.0. The two worktrees used were created at
`e6767991` and `origin/dev` and removed afterwards.

- Exact revision `e6767991` (detached worktree): `python -m scripts.check_p8_t41`
  → `Ran 45 tests`, `FAILED (failures=1)`, failure reproduced as in finding 1.
- Current dev: `python -m scripts.check_p8_t41` → `Ran 45 tests`, `OK`.
- Current dev, before correction:
  `PYTHONPATH=.:tests python -m pytest 'tests/social/test_p8_t38_execution.py::VKSelectedRequestTests' --noconftest -q -o addopts="" -p no:cacheprovider`
  → `9 failed in 1.64s`, cause reproduced as in finding 2.
- Current dev, after correction: same command → `9 passed, 2 subtests passed in
  0.75s`.
- Integration: ancestry check, `ls-tree` and content diff above.

## Authorized correction applied

A minimal test-double correction was authorized by the owner. Landed locally as
`00d8d186` `fix(p8-t38): declare NarrowedAccess in isolated credentials double`
(one line in `tests/social/test_p8_t38_execution.py`): add
`NarrowedAccess=lambda **kwargs: types.SimpleNamespace(**kwargs)` to the
isolated credentials double. This clears the nine source-only failures; the
executor's 94-pass baseline for the other paths stands. It was not pushed to
`origin/dev` by this audit.

## Required remediation before re-requesting acceptance

1. Re-baseline the accepted revision to a current `dev` SHA that contains
   `00d8d186` and the P11-T41 fix, then re-run and record **all** required checks
   at that exact SHA: `python -m scripts.check_p8_t41` (expect 45/45), the 11
   DB/P8-T38 paths with the root conftest against `test_schema`, and before/after
   `schema_inventory.py` diff. Record commands, exit codes, counts, DB target and
   cleanup in the card.
2. Publish READY only after those checks pass, then request a fresh independent
   audit. Alternatively, obtain an explicit owner waiver naming the failing
   check at `e6767991`; it stays recorded as unrun/waived, never PASS.

## Unrun / limits

- Full 11-path DB-backed suite after the correction, full pytest, browser/runtime
  journey re-run and live VK checks: not run by this audit.
- The executor's recorded live journey, synthetic-row cleanup and clean schema
  inventory are retained in the card; they do not cure a required check that
  failed at the accepted revision.
- No P8-T41-owned branch or worktree remains (the task branch was deleted after
  the merge). The two verification worktrees created by this audit were removed.
  Foreign worktrees and the owner's `氛围/` directory were not touched.

## Documents read

`ROADMAP.md` (P8-T41 row); `docs/tasks/p8-t41.md`; `docs/checks/p8-t41.md`;
`docs/process/COLLABORATION.md` (State machine, Evidence, Acceptance,
Git and cleanup); `docs/phases/p8-first-value-ui.md`; `docs/audit/p11/p11-t41.md`;
`tests/social/test_p8_t38_execution.py`; `app/services/social/vk_client.py`;
`app/services/social/credentials.py`; `.agents/skills/audit/SKILL.md`.
