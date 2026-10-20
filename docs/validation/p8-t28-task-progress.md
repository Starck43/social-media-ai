# P8-T28 — segmented task-run progress handoff

## Revision and scope

- PR: [#225](https://github.com/Starck43/social-media-ai/pull/225), Draft to `dev`.
- Synchronized base: `adf1d6fc79ea147863c4c2d7560349140ab2a6cf`.
- Claim commit: `138be556ebde4e7d5c06c65b066aa56b57feb3e6`.
- Implementation checked: `05949aff7377573e540b47470cc3b7eb763af24d`.
- Task is READY for coordinator/owner review, not DONE. Real application
  acceptance remains outstanding; Draft is intentional and no merge is authorized.
- Owner-approved narrow presentation handoff from P8-T22. Its foreign owner,
  BLOCKED status and historical receipt acceptance are unchanged. P8-T27/#224
  and the historical duplicate P8-T25 rows were preserved.
- Rechecked open heads before implementation publication: #224 `6fd66d16`,
  #220 `30c88985`, #217 `c4161b5e`, #216 `b9faf8dc`; no new overlap.

## Presentation contract

- `record_progress` is shared by the detail fragment and server-rendered compact
  modal HTML. No prototype toolbar or second progress implementation is shipped.
- Collection counts received records (`items`), not sources traversed. The record
  denominator stays unknown until discovery completes successfully for all
  sources. An unknown total never produces a fabricated global percentage.
- Analysis counts covered input records (`raw_processed`) against this run's
  selected batch (`raw_selected`), NOT analysis-result rows (`analyzed`) or the
  whole backlog (`raw_total`). The batch can include previously staged records;
  collection and analysis denominators may legitimately differ. Unvisited/unknown
  batches stay unknown, not zero. Task-filtered/unprocessed rows are not credited.
- Color identifies the source, not success. Gaps separate source segments;
  unprocessed error shares are hatched. The palette adapts to light/dark surfaces.
  Aggregates are computed before the 50-source display window; extra sources are
  grouped into one segment without dropping their counters. Legacy truncated
  snapshots do not get reconstructed into false all-source totals.
- Only the right configuration sidebar enumerates source names and links.
  It uses current tenant/task-bound inventory (or active sources when unbound),
  and marks the latest displayed run's failed/empty collection or failed analysis.
  It does not search historical runs for stale failures. Numeric platform codes
  and a fixed safe code allowlist are administrator-only; raw hints/exceptions,
  payloads, content hashes and publication content are not rendered there.
- Central raw-backlog tiles, per-source lists/legend, the old source-run block,
  and the redundant result-statistics grid are removed from the new view.
  Source-view permission still gates record counters and sidebar inventory.
- `dry_run` comes from run evidence/effective snapshot before mutable settings.
  Rehearsal collection is labelled as received-for-checking, never saved content;
  analysis counters/results are not credited and the retry-analysis action is
  hidden. Existing task-column dry-run configuration remains in the sidebar.
- Existing queue admission, pending handling, claim fencing, cancellation and
  handler algorithms remain unchanged. The progress writer adds bounded display
  aggregates; cancellation still propagates. A phase handoff retains the last
  appended collection row even without an extra handler step.
- Detail polling prefers any pending/running job, including an older busy job,
  before the newest completed run. The source sidebar refreshes through an HTMX
  out-of-band fragment with the same authorization. Mutation controls lock for
  pending/running and while submitting. Failed polls retain last counters and
  do not unlock a new run. The list keeps its existing admission route and gains
  a sending guard; queued indicators are not animated as running.
- Existing embedded editor `target="_top"` and `return_to_detail=1` save/run path
  are preserved, not replaced with another executor or navigation mechanism.

## Remote validation executed by the remote agent

No application/runtime/Compose startup, ORM connection, real DB, settings with
secrets, provider request, send, migration/reset/deploy or delegated test run.
The fresh migration `0106_search_source_and_task_field_cleanup.py` and collection
changes were inspected only; no schema or collection/selection logic was changed.

Environment: separate `/data/task-progress-implementation-venv`, Python 3.13.14,
pytest 9.1.1, pytest-asyncio 1.4.0, Jinja2 3.1.6, Playwright 1.63.0,
Chrome for Testing 153.0.8010.12. QA-only requirements are in
`tests/offline/requirements.txt`; application dependency versions are unchanged.

Commands from the repository root (all exit 0 in the final run):

```sh
python -m venv /data/task-progress-implementation-venv
/data/task-progress-implementation-venv/bin/pip install -r tests/offline/requirements.txt
/data/task-progress-implementation-venv/bin/python -m pytest --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header tests/test_job_progress.py tests/unit_web/test_run_progress_component.py tests/unit_web/test_unified_progress_phases.py tests/unit_web/test_segmented_task_progress.py
/data/task-progress-implementation-venv/bin/python tests/offline/render_task_progress.py /data/task-progress-fixtures
TASK_PROGRESS_FIXTURES=/data/task-progress-fixtures /data/task-progress-implementation-venv/bin/python -m pytest --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header tests/offline/test_task_progress_browser.py
python -m py_compile app/jobs/progress.py app/jobs/progress_metrics.py app/web/task_progress.py app/web/tasks.py tests/offline/render_task_progress.py
node --check app/static/js/task-run-progress.js
git diff --check
```

Installation was initially performed in two equivalent subsets (pytest/Jinja2,
then Playwright); final installed versions match the checked-in QA manifest.
Root conftest and pytest cache were disabled. Import/fixture graphs were inspected
first. The web router/package is not imported: pure projection is file-loaded;
selected actual endpoint/scope functions are AST-extracted with mocked boundaries.
Three updated existing template contracts are also executed this way; the rest
of `test_task_detail_progress.py` and the full application suite were NOT run.

Final results on the published implementation (all 16 implementation/test files
byte-compared with GitHub commit contents):

- **58 passed, 0 failed, 0 skipped** unit checks, 0.80 s.
- **12 passed, 0 failed, 0 skipped; 18 subtests passed** offline Chromium checks,
  14.18 s. External HTTP/WS requests aborted; fixtures use local assets only.
- Includes 150-source aggregation, unknown/zero/invalid counters, stale attempts,
  legacy windowing, success/partial/error/cancelled/pending/dry-run, safe sentinel
  exclusion, permission projections, mocked tenant filters, sidebar-only source
  enumeration, duplicate submit guard, failed poll retention, HTMX-event unlock,
  shared compact markup, reduced motion and a dark identity-palette check.
- An initial browser run found same-event-loop duplicate submission; the guard
  now checks its synchronous submitting flag. The final repeated runs passed.

### Visual evidence and limitations

`tests/offline/render_task_progress.py` renders the real detail-content templates,
component CSS, mutation controls/JS and compact macro inside a minimal offline
shell. It does NOT start ASGI or reproduce the entire deployed shell. The CDN
asset fetch returned HTTP 403; no retry/bypass was used. Therefore the fixture
uses an explicit layout-only utility subset and simulates teleport/HTMX events;
real Alpine/HTMX/CDN integration remains an owner check, not a remote PASS.
The browser test executes the actual modal polling state machine with mocked
fetch, not a live endpoint. Its compact screen is a macro fixture, not proof of
an integrated modal. No mock check is real persistence/permission acceptance.

Desktop analysis/dry-run, mobile partial and compact PNGs were opened and visually
inspected individually; no clipping/overlap/horizontal overflow was found. The
three final handoff screenshots use 1440px / 390px widths and carry the offline
fixture disclaimer. Image SHA-256:

- analysis: `dafb655219c61191d4305017c26b478b94848c40e792b6117a0e339d953e0168`
- dry-run: `d33687b6484010118f3cfb4272f7f2f4ed713db098e5a028c0d9006f8b902f9e`
- mobile partial: `e501260b2f66d90e60030b17800b6f611c97d9c38be6a703d74b710ab63de713`

## Required owner/coordinator acceptance (not executed remotely)

Use the normal authorized local validation route under RULES R8/R10; do not
create/reset/migrate a database merely to make this PR pass.

1. At the exact implementation SHA, run the full relevant existing web/task,
   dispatcher/progress, tenant/permission, admission and cancellation regressions
   against the approved local test environment, with existing fixtures reviewed.
2. In the real task detail/list UI verify pending, collection with unknown total,
   analysis, success, partial, source error/empty, dry-run and cancellation. Source
   names/links must occur only in the configuration sidebar; admin codes must not
   appear for an ordinary operator. A user lacking source-view permission must
   see neither inventory nor source-derived record counts.
3. Verify actual HTMX sidebar OOB refresh, Alpine compact modal, light/dark styles,
   navigation and focus on desktop/mobile. Disconnect/reconnect polling: preserve
   counters, no false completion or unlocked duplicate launch. Confirm mutation
   controls recover after an actual terminal poll.
4. Verify manual/no-runtime and runtime/job task admission, including two tabs,
   double clicks, a pending job and an older busy job outside the recent-10 list.
   There must be no second admitted run. New UI guards are not a substitute for
   server admission; no new locking/executor was added.
5. Save and execute an edited one-off task in the real iframe: modal closes via
   existing top-level redirect and returns to `/app/tasks/:id` with polling.
   Confirm dry-run performs no staging/model call and is never shown as saved or
   successfully analyzed. Runtime/provider/DB acceptance needs separate explicit
   local authorization; the remote branch made none of those calls.
6. Preserve P8-T22 persisted-receipt acceptance and other active lanes. Coordinator
   decides DONE and merge only after required evidence; this PR stays Draft.
