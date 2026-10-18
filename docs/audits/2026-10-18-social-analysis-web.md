# Social analysis web journey

Audit base: local `dev` `c98dc9c`; origin/dev `1e0e3a0` verified on
2026-10-18. The inline source-picker commit `a11f1f9` is part of the current
base and its behavior is retained. Delivery is tracked by
[P8-T22](../phases/p8-first-value-ui.md).

## Confirmed behavior and gaps

- `handle_collect` stages raw input and optionally analyzes its fresh batch.
  `analyze_inline` alone does not drain older staged input or apply the task's
  normal trigger/action continuation. It cannot implement the requested journey
  by itself (`app/jobs/handlers.py`, `handle_collect`, `handle_analyze`).
- `handle_analyze` already retires only input covered by saved analytics or
  verified completion receipts. Failed and partial input survives; the retry
  ceiling can suspend automatic attempts without deleting raw content. Keep
  this contract and the bounded batch size.
- The web forms offer separate collect/analyze task types, but do not expose
  the inline collection flag. Operators must understand internal job primitives
  to set up a complete run (`app/web/tasks.py`, task form templates).
- The source header duplicates queue analysis already available next to the
  backlog and offers a collection action that runs inside the HTTP request.
  Task runs already enqueue work and show progress. Use the task journey for
  normal launches, retaining the focused queue retry near the actual backlog.
- Source schedules show only explicit source links and link every task to the
  general task list. Empty task source sets mean all active sources in the
  worker. Schedule projection must represent that rule and use direct links.
- The active scenario detail route renders `scenario_wizard.html`, not the
  older `scenario_detail.html`. Its source-assignment wording conflicts with
  the actual task-owned scenario relationship.
- The task editor reconstructs cron expressions through a limited visual
  builder. It drops month restrictions (`0 9 1 3 *`), treats Sunday `0` as
  Monday, and cannot represent minute intervals (`*/15 * * * *`). Preserving
  the original expression until an explicit schedule change is necessary.
  Source-picker HTML fallback does not make the rest of the Alpine form
  functional without JavaScript; browser checks must cover actual submission.
- Source deletion drops the source/task links without pausing tasks. Removing
  a task's last explicit source turns it into an all-active-sources task and
  can widen collection unexpectedly. Its old confirmation incorrectly promised
  that collection would stop. Transactional deletion semantics are a separate
  high-priority follow-up, [P6-T04](../phases/p6-lifecycle-privacy.md); this web
  slice corrects the warning but does not certify a backend deletion fix.

## Operator layout

| Page | Primary action | Useful information | Secondary actions |
| --- | --- | --- | --- |
| Sources | Add source | Platform, access readiness, active state | Open source details |
| Source detail | Create analysis task with this source selected | Access/readiness, last check, raw waiting/suspended counts, saved analyses; explicitly scoped sentiment and estimated spend | Retry stored raw input in its queue block; edit/toggle source; destructive actions in a separate administration block |
| Scenarios | Create methodology | Analysis/content types, active/default state, permitted task usage | Open methodology |
| Scenario editor | Save methodology | Purpose, content coverage, model choices; separate permitted task usage block | Create task with this scenario selected; open a related task |
| Tasks | Create analysis task | Source/scenario selection, effective activity, schedule, latest outcome, queued/running state | Open task, pause/resume, enqueue a run |
| Task editor | Save or save and enqueue | Sources → methodology → content period → schedule; explain saving versus an immediate queue request | Optional targets/conditions remain separate from the core setup |
| Task detail | Enqueue a run | Configuration, next occurrence, actual run outcome, collection and analysis counters, raw coverage and history | Edit task; authorized diagnostics in a separate disclosure |

Operators receive actionable status and next steps. Existing administrator
predicates protect technical diagnostics and raw previews. Task usage must be
gated before querying by `agenttask.view`; creating a task needs
`agenttask.create`. Unknown progress or missing telemetry must remain unknown.
Statistics keep their entity and time scope: a recent-analysis sentiment sample
is not a lifetime average, analytics rows are not raw content rows, and estimated
analytics cost is not a complete billing ledger.

## Execution contract

The web presents **Анализ соцсетей**, stored as the existing `analyze` job type
with `payload.collect_before_analyze = true`. One admitted job first collects
with inline analysis disabled, then runs the existing staged analyzer with the
same task, workspace, initiating actor and date window. This processes fresh
and eligible older raw input once and preserves trigger/action guards.

Standalone CLI/API/chat collection and staged analysis remain independent.
Existing persisted tasks keep their current runtime behavior until explicitly
saved through the unified web editor; no bulk rewrite or migration is needed.
Legacy collect forms normalize on web save. Historical collect runs retain
their actual collection labels. A collection failure must remain visible even
if an older staged batch can still be analyzed; no fresh analysis must not
replay historical analytics into new reactions.

Collection and analysis are distinct progress phases inside one job. The source
position counter resets at their boundary without resetting lost-claim state;
the source UI must not describe completed collection as completed analysis.

## Delivered behavior and validation

The implementation uses the execution contract above. Web creation and manual
launch require an active selected methodology or an active workspace default;
pausing remains possible when methodology is unavailable. An inactive selected
scenario can still be retained while saving a paused task. Inactive scenarios
do not offer a task-creation link that would silently lose their selection.

The full task editor now uses the shared `task-schedule.js` builder. Existing
cron expressions survive unrelated saves, including month restrictions, minute
intervals, Sunday `0`, day `31` and nonzero-minute hour intervals. Unsupported
visual patterns have an explicit raw cron field. Without JavaScript the form
is disabled with an explanation, preventing empty or partial parameter saves.
The source-picker fallback markup remains; a fully functional native editor
is not claimed. The incomplete duplicate editor on task detail was removed.

Reviewed source baseline: `a11f1f9e880925b01bf30478dbde62fa6ad0b48c` plus the
task-scoped changes. The P8-T21 picker commit changed only the editor and its
two test files; its handoff to this task was explicit. No push, schema
migration, runtime activation or paid request was performed here.

- Primary OpenCode `build`, model `QwenPlan/deepseek-v4-flash-0731`:
  **210 collected / 210 passed, 0 failed, 0 skipped**; collection and execution
  exit `0`, with nine passing subtests and one SQLAlchemy deprecation warning.
  The CLI lacks `--dir`; setting the subprocess working directory preserved
  the same model, agent, task attachment and permission boundaries.
- Exact isolated pytest scope: `tests/test_unified_analyze_handler.py`,
  `tests/test_analyzer_reported_errors.py`, `tests/test_job_progress.py`,
  `tests/test_analysis_progress_projection.py`,
  `tests/test_analysis_progress_endpoints.py`, and `tests/unit_web/` files
  `test_unified_social_analysis.py`, `test_unified_progress_phases.py`,
  `test_social_analysis_journey.py`, `test_unified_seen_hashes.py`,
  `test_task_source_picker.py`, `test_template_autoescape.py`,
  `test_analysis_retry.py`. Flags: `--noconftest -o addopts= -q --tb=short
  --no-header`; collection used `--collect-only` first. Storage/providers were
  mocked; this is not a PostgreSQL acceptance run.
- `node tests/test_task_schedule.cjs`: exit `0`, five original-expression
  preservation cases plus explicit builder/preset changes. Python compilation,
  Jinja parsing and `git diff --check` passed.
- Safari **27.0**, actual rendered editor and Alpine, synthetic loopback
  handler only: real Save POST omitted `run_now`; Save-and-queue POST contained
  `run_now=on`, the original custom cron, selected sources and start date.
  Source removal submitted no source IDs (all-active contract); adding another
  source submitted its numeric ID. No JavaScript errors were observed.
  Tested fixture SHA256:
  `d08cdaafeb0b4fd7f2004dd45bc65d697d57ddc87658619bc9f0a1eb3840c8e3`;
  schedule helper SHA256:
  `6ae095a73ce784236c07f1c5aeb266a5dfe44faccaf4d150d30e6edd650855a6`.
  Final methodology guidance is covered by the later isolated rendering tests;
  the Safari POST checks preceded that presentation update.

Remaining acceptance boundary: the local site returned **502** and the
separate task-editor PostgreSQL preflight reported missing
`analyzed_content_receipts` in `test_schema`. No schema repair was authorized
or attempted. Real persisted web/worker collection, receipt retirement,
concurrent admission and billing were not certified by these isolated checks.
Database-backed task tests are prepared for the unified contract but remain
unrun. No valid Safari screenshot or full responsive visual matrix was obtained;
no-JavaScript behavior was inspected in markup, not with JavaScript disabled.
The final compact selector and action-button styling follow-up received
template/JavaScript syntax and diff checks only; it was not visually tested.
P6-T04 remains a separately tracked source-deletion scope defect.
