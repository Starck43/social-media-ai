# Review of the retired remote lane's open work (2026-10-23)

Scope: the four open pull requests and the branch-only admin tasks left by the
retired remote lane. Verified against `dev` `8f2d8a2` on the owner's machine.
No database was touched: the shared `test_schema` was held by another runner and
is at `0108`. Everything below is source/offline evidence, not DB/ASGI acceptance.

## Open pull requests

| PR | Task | Head | Merge into `8f2d8a2` | Verified now | Verdict |
|---|---|---|---|---|---|
| #216 | P10-T11 audit | `b9faf8d` | conflicts | probes run at their head: 14 tests, 10 expected failures (by design) | **Refresh or drop.** Docs-only audit; several findings still hold (see below), but the probe file lives in the old root layout and the audit predates the personal-catalog changes. No runtime code to merge. |
| #217 | P10-T12 chat task contracts | `c4161b5` | conflicts (resolved in review branch) | rebased to `dev` `8f2d8a2` as **`review/p10-t12`** `0e03ecc`; source-loaded suite **27/27 passed** | **Ready for DB/ASGI acceptance.** See rebase notes. |
| #224 | P8-T27 platforms/admin/FK audit | `6fd66d1` | conflicts | at its head: **162** unit tests passed; offline browser **32 cases, 0 failures** (real Chromium) | **Rework/rebase.** The feature (Platforms tab) is absent from `dev`; branch is three commits behind and needs a rebase plus PostgreSQL/ASGI acceptance before review. |
| #220 | roadmap reconciliation | `30c8898` | conflicts | at its head: **30** tests, 7 expected failures (by design) | **Drop or refresh.** Baseline `ddc9b99` predates current `dev`; the audit is a useful historical cross-check but its registry statements are stale. No runtime code. |

### #217 rebase details (`review/p10-t12`, `0e03ecc`)

- Conflicts were only in `app/agent/toolset/tasks.py`. Resolution keeps dev's
  collection-intent and `deactivate_after_success` flows and adds the branch's
  tenant-presence check, pre-write source/scenario validation, `clear_scenario`,
  persisted `cron_expr`, payload-only-when-changed and the `@once` next-run rule.
- The branch's own test was adapted to dev's structure: the manager class is
  selected by name (dev now defines helper classes before `AgentTaskManager`),
  and doubles were added for dev's `monitoring.selection` and
  `collection.intent` imports. Result: **27/27 passed**, no DB, no app import.
- Still required before READY: sequential PostgreSQL/ASGI acceptance of the
  task-tool contracts in an approved `0109` window (the personal-catalog ORM
  metadata is required for the app to import) plus the coordinator's receipt/force
  policy check. Until then P10-T12 stays BLOCKED.

## Which #216 findings still hold at `8f2d8a2`

Directly re-checked in current source, independent of the stale probe file:

- **Task listing cannot resolve its model:** still true — `task_list` in
  `app/agent/toolset/tasks.py` does not import `AgentTask`.
- **Task edits silently change configuration:** still true — `cron_expr` is not
  persisted with `next_run_at`, an omitted scenario is not preserved by the
  update path, and `@once` has no local next-run rule (all three are what the
  rebased #217 adds).
- **Saved agent model choice ignored at 0108:** true by construction; the numeric
  `agent_model_id` only exists in the personal-catalog migration (#229).
- **P10-T13 CLI staleness:** superseded — its implementation is merged (#218) and
  the task is now `IN_PROGRESS` under its new owner.
- Remaining #216 items (HTTP/tool schema disagreement, credential-disable URL,
  global model tools) were not re-probed here; the audit's dates and baselines
  must be refreshed before acting on them.

## Branch-only admin tasks (no PR)

`P8-T15`, `P8-T17`, `P8-T18`, `P8-T20` have remote branches
(`fix/admin-model-real-request`, `-diagnostics`, `-owner-mode`, `-cost-feedback`)
but no open pull requests. They were not reviewed here. Recommended: keep them
BLOCKED, decide explicitly whether to reassign or drop; do not merge any of them
on the strength of their historical "implementation ready" notes.

## State left behind

- `dev` unchanged: `8f2d8a2`; no merge into `dev` from this review.
- `test_schema`: `0108`, no `owner_user_id`/`uid`, no 0109 guards.
- Review branch: `origin/review/p10-t12` at `0e03ecc` (offline-green, DB pending).
- No production target, deploy, live provider call or schema action.
