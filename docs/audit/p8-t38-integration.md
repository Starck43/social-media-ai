# P8-T38 — VK selection policy increment integrated; acceptance deferred

**Increment integration — accepted as dev content, task NOT accepted, 2026-10-27**
(owner-authorized interim merge of the delivered policy increment to keep it
from being lost). Integrated via PR
[#258](https://github.com/Starck43/social-media-ai/pull/258), merge commit
`5848c216`. PR
[#256](https://github.com/Starck43/social-media-ai/pull/256) (executor
notion-ai, draft) stays open as the task's working surface for the remaining
acceptance work. Parent:
[P8](../phases/p8-first-value-ui.md).

## What was integrated (and what was not)

Only the isolated pure-policy increment delivered on PR #256 (published head
`28f6953f`, task-card checkpoint `bac7551e`):

- `app/services/social/vk_access.py` — dependency-free fail-closed VK
  connection-selection policy: strict pinned api/user selection, exact
  personal-credential identity, pure fail-closed decision over a fresh
  workspace/member/credential snapshot.
- `tests/social/test_p8_t38_vk_access.py` — 20 stdlib unit tests that load the
  module by path only (no app/conftest/ORM/settings/DB initialization).
- `docs/tasks/p8-t38.md` — task-card checkpoint and increment record.
- `a0870c64` — project black/isort profile alignment on the two new files.

The module is intentionally unreferenced by any surface: no fresh-snapshot
adapter, no persistence, no preview/execution/client guards, no
web/API/CLI/admin/agent presentation. **Production behavior is unchanged.**
No item of the card acceptance list is satisfied by this merge, which is
deliberated in the ROADMAP status below.

## Audit evidence (independent audit, 2026-10-27)

- Policy review at `28f6953f`: stdlib dataclasses only; no vault, environment,
  network, ORM or token access. Fail-closed ordering verified: workspace →
  known methods (unknown/empty deny without echoing input in the error) →
  connection present → tenant/mode/row identity (no row or mode substitution,
  `auto` not accepted, boolean IDs rejected) → revoked → membership recheck →
  expiry boundary inclusive → mode matrix (service tokens denied on
  user-only search routes) → unknown grants are not permissions → scope
  subset. `live_unverified_methods` is honestly labeled; no live-availability
  claim.
- 20/20 unit pass at the published head
  (`python -m unittest discover -s tests/social -p test_p8_t38_vk_access.py`).
- The published head failed `black --line-length 120` /
  `isort --profile black -l 120` on both new files (executor sandbox lacked
  the tools — recorded in the PR #256 audit comment). Applied locally:
  behavior-neutral import/wrapping-only change (24+/8−); 20/20 re-verified.
- Current-base reconciliation: the branch was forked at `522bac9a` (behind
  dev after #255/#257/registry); cherry-picked onto dev `6ae13079` as
  `99345f80` + `a39db0f2`, clean, and `git diff --check origin/dev..HEAD`
  clean.
- Final dev `5848c216`: 20/20 re-run green; black/isort/compileall clean.

## What is missing for acceptance

From the card "Implementation increment 1" record and the PR #256 body — all
still open, executor-owned on PR #256:

1. Authorized fresh membership/vault adapter that supplies the policy a fresh
   authorized snapshot before every operation (a serialized preview is never
   an execution permit).
2. Safe scope/profile metadata taken from real grant metadata, never inferred
   from OAuth login or the product entitlement catalog.
3. Selected-plan persistence (mode per task, visible and revocable).
4. Preview/execution/client guards: deterministic resolution in all execution
   surfaces, fail-closed on missing capability, no silent user↔service
   fallback beyond the documented limited collector cases, and refresh/revoke/
   error handling that never leaks tokens.
5. web/API/CLI/admin/agent presentation of the selected mode,
   account/profile, granted scopes, health/expiry and reauthorization state.
6. Focused integration evidence: mocked missing-scope / expired / revoked /
   cross-workspace cases plus permission rechecks at execution time.

Q-5 remains the live method/profile evidence owner; this increment makes no
live availability claim.

## Status

- ROADMAP P8-T38: **IN_PROGRESS → DEFERRED**. Reason: policy increment is in
  dev; acceptance incomplete — remaining integration work in
  `docs/tasks/p8-t38.md`, continued on PR #256.
- Task card preserved at `docs/tasks/p8-t38.md`.
