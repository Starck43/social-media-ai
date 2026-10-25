# Social analysis web journey

Audit base: local `dev` `c98dc9c`; origin/dev `1e0e3a0` verified on
2026-10-18. The inline source-picker commit `a11f1f9` is part of the current
base and its behavior is retained. Delivery is tracked by
[P8-T22](../tasks/p8-first-value-ui.md).

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
  Task runs already enqueue work and show progress. The initial implementation
  retained a focused source-page retry; the 2026-10-19 layout follow-up removes
  execution controls and run history from the source page altogether.
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
  high-priority follow-up, [P6-T04](../tasks/p6-lifecycle-privacy.md); this web
  slice corrects the warning but does not certify a backend deletion fix.

## Operator layout

| Page | Primary action | Useful information | Secondary actions |
| --- | --- | --- | --- |
| Sources | Add source | Platform, access readiness, active state | Open source details |
| Source detail | Save collection settings in the inline form | Collection/access, source-wide waiting/suspended counts, last check, saved-analysis totals, recent sentiment sample and estimated spend; permitted related tasks | One task-creation link at the bottom of related tasks, with this source selected; execution history, progress and diagnostics belong to task/run views |
| Scenarios | Create methodology | Analysis/content types, active/default state, permitted task usage | Open methodology |
| Scenario editor | Save methodology | Purpose, content coverage, model choices; separate permitted task usage block | Create task with this scenario selected; open a related task |
| Tasks | Create analysis task | Source/scenario selection, effective activity, schedule, latest outcome, queued/running state | Open task, pause/resume, enqueue a run |
| Task editor | Save or save and enqueue, returning to task detail | One form at `/app/tasks/{id}/edit`, reused in the task-detail dialog; sources → methodology → content period → schedule | Optional targets/conditions remain separate from the core setup |
| Task detail | Edit and run now from the navbar | Configuration, source/platform scope, latest task-associated run counters, per-source progress, raw coverage and run history | Retry analysis beside the run panel; authorized admin diagnostics in a disclosure |

Operators receive actionable status and next steps. Existing administrator
predicates protect technical diagnostics and raw previews. Task usage must be
gated before querying by `agenttask.view`; creating a task needs
`agenttask.create`. Unknown progress or missing telemetry must remain unknown.
Statistics keep their entity and time scope: a recent-analysis sentiment sample
is not a lifetime average, analytics rows are not raw content rows, and estimated
analytics cost is not a complete billing ledger.

Task names in the list open task detail. Its run panel refreshes while pending
or running, and polls less frequently while idle to discover scheduled runs.
Before a task has an associated Job, the panel shows its configured scope and
an unstarted or unavailable progress state. Historical source-only jobs cannot
be attributed to a task. Normal execution remains available even when there is
no staged input; retrying staged analysis is a separate action. The task editor
dialog loads the same canonical editor URL on demand and returns to task detail
after saving; the direct URL remains available without the dialog.

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
task-scoped changes. The P8-T23 picker commit changed only the editor and its
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

Source-detail follow-up, 2026-10-19, base `346cd65` plus reviewed working diff:
the page now has three source-level cards and an inline settings form. Source
run panels, polling, raw previews, history, header and destructive actions were
removed from this page. The related-tasks card has one source-prefilled create
link. Existing exposed mutation/progress routes retain their prior contracts.
The editor preserves an omitted token owner, clears an explicitly empty owner,
rejects nonmembers, and checks duplicates when the platform changes.

Primary OpenCode `build`, `QwenPlan/deepseek-v4-flash-0731`, isolated scope:
`tests/unit_web/test_social_analysis_journey.py`,
`tests/unit_web/test_analysis_retry.py`,
`tests/unit_web/test_template_autoescape.py`,
`tests/test_analysis_progress_endpoints.py`. Collection: 122, exit 0. Initial
execution: 118 passed, four failed because the AST test fixture omitted
`COLLECTION_MODES`. After correcting only that fixture, the four parameterized
editor cases passed (exit 0); the other 118 passing cases were not repeated.
Python/Jinja syntax and diff checks passed. Prepared PostgreSQL-backed source
render assertions were updated but not executed. No browser/visual check or
schema operation was performed for this follow-up.

Task-detail correction, 2026-10-19, synchronized base `ddc9b99` plus the
reviewed working diff: task names open detail, the process panel comes first,
and navbar edit opens the canonical `/edit` form in a native dialog. Saving
returns to task detail. The same form remains directly addressable. Run now
is always available under its original permission; retrying staged analysis
is a distinct panel action. All counters/progress stay within the HTMX root,
including the no-Job state; idle polling discovers newly scheduled runs.
The panel separates the current source backlog in the configured date window
from latest-Job statistics. Raw counts use one tenant-scoped aggregate and the
bound `CollectedItem.objects` manager. Malformed windows remain unknown and
cannot widen explicit retry. Model/provider and successful/failed call counts
come from journal rows matching both task and Job, under admin access.

Primary OpenCode, same pinned model and isolated flags: 131 cases collected
across `test_task_detail_progress.py`, `test_task_journal_scope.py`,
`test_task_source_picker.py`, `test_template_autoescape.py`,
`test_unified_social_analysis.py`, `test_queue_recovery_admission.py` and
`test_analysis_progress_endpoints.py`. The main execution passed 130 cases;
one explanatory-text assertion was updated to the final wording. After the
bound-manager fix and strengthened fixture, the two task-progress/journal
files passed all 21 cases (0 failed/skipped); the other 110 passing cases were
unchanged. Earlier fixture failures remain superseded by this successful scope.
Node syntax and diff checks passed. Read-only actual ORM access for task 1582
found one active source/platform, no staged records, a successful coarse run
receipt and no associated Job. Historical percentages were not inferred from
source-only runs. No data was changed and no collection/model run was launched.
The local task URI still returned nginx 502; no live browser acceptance is
claimed. Ordinary runtime handling of malformed historical `cli_dates` remains
outside this UI correction; explicit retry and backlog now reject that widening.

Local implementation commit: `ca4b9b04cb42396765b0d5816abce0f67d8e21df`.
Closeout catalog recheck on 2026-10-19 confirmed that
`test_schema.analyzed_content_receipts` is absent, using the existing session
factory in a read-only transaction. Persisted acceptance remains blocked;
no schema repair is implied by the UI commit. Later owner instructions exclude
visual testing of the final editor and analytics layout.
