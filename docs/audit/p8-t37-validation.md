# P8-T37 — revision-specific audit and validation

Solution: [PR #245](https://github.com/Starck43/social-media-ai/pull/245), branch `fix/p8-t37-plan-execution-dry`, base `dev`. Companion review: [PR #243](https://github.com/Starck43/social-media-ai/pull/243). This report records an independent audit and required checks, not owner acceptance.

## Original findings and corrections for the next auditor

Audited P8-T29 implementation `597b5e78d6a0c6bd41bd82ba3312b1aef287d3c2`, as integrated into dev. Implementation base for this follow-up: `8fdd83f5b63b85798ae0044226a2f5a6385dc035`. Claim-only commit: `78c83a81d636e6d230caa978c3a7f47733fa68f4`.

1. **P1: discovery-to-analysis scope loss.** Collection resolves newly discovered sources, but analysis previously resolved without those IDs. A new planned discovery task with empty admitted source_ids could collect content and analyze none. Analysis now carries collection's internal per-source IDs through the existing resolver, rechecks active/tenant/platform restrictions and intersects with collected IDs. It does not trust caller-supplied discovery keys or widen scope through mutable links. Legacy execution keeps its previous path.
2. **P2: duplicated update policy and divergent conversion.** API/CLI/chat separately handled plan preservation/replacement/conversion. Chat cleared plan-owned fields while API/CLI reapplied an incompatible old plan on conversion to digest/prune/learn/reflect. New characterization additionally exposed stale chat payload.scenario_id. A shared pure prepare_task_plan_update policy handles the three surfaces, rejects protected/inline overrides, clears retired projections and preserves unrelated payload and explicitly requested legacy scenario. Retired plan timezone is not reused for the new legacy schedule. Existing permission, billing, transaction and execution guards remain at their boundaries.
3. **P3: redundant work within a single validation.** Preview serialized twice; binding parsed twice and compared the same scenario relationship twice. Typed projection now reuses the parsed plan, preview serializes once, and one equivalent scenario inequality remains (including None mismatch validation). Admission and worker reuse a pure platform predicate. Repeated authorization/admission checks across distinct boundaries are deliberately retained, not treated as DRY violations.

Previous digest-scope optimization is unchanged. This is correctness and maintainability work with redundant operations removed, **not a measured end-to-end performance claim**.

## Exact checked implementation

`b08ab97c463c58eb4fa153cb298b5442695afed0` — clean worktree before/after all commands. Python 3.13.15; pytest 9.1.1; Black 26.10.1; isort 9.0.2, isolated tool venv. No DB, paid provider, secrets or runtime accessed. Native independent test-runner launch failed with `The tool capability failed unexpectedly`; no child handle or run was created. These are executor-run fallback checks following the test-runner evidence protocol, **not independent R10 acceptance**.

### Actual commands and results

All commands run from the repository root. The tool-venv path below is specific to the remote environment; a local agent should substitute its validated project/tool interpreter. Test commands use `PYTHONPATH=tests:.`.

**new-tests** — exit `0`.

```bash
PYTHONPATH=tests:. /data/p10-t23-tools/bin/python -m pytest --noconftest -c /dev/null --rootdir=. -p no:cacheprovider -q tests/unit_services/test_p8_t37_task_mutation_policy.py tests/jobs/test_p8_t37_analysis_scope.py tests/unit_services/test_p8_t37_plan_reuse.py
```

**collection** — exit `0`.

```bash
PYTHONPATH=tests:. /data/p10-t23-tools/bin/python -m pytest --noconftest -c /dev/null --rootdir=. -p no:cacheprovider --collect-only -q tests/unit_services/test_p8_t37_task_mutation_policy.py tests/jobs/test_p8_t37_analysis_scope.py tests/unit_services/test_p8_t37_plan_reuse.py
```

**admission-advisory** — exit `1`.

```bash
PYTHONPATH=tests:. /data/p10-t23-tools/bin/python -m pytest --noconftest -c /dev/null --rootdir=. -p no:cacheprovider -o asyncio_mode=auto -q tests/collection/test_source_job_admission.py
```

**isort** — exit `0`.

```bash
/data/p10-t23-tools/bin/isort --check-only tests/shared/task_plan_support.py tests/unit_services/test_p8_t37_task_mutation_policy.py tests/jobs/test_p8_t37_analysis_scope.py tests/unit_services/test_p8_t37_plan_reuse.py app/services/task_plans.py app/jobs/source_activity.py
```

**black-pure** — exit `0`.

```bash
/data/p10-t23-tools/bin/black --check tests/shared/task_plan_support.py tests/unit_services/test_p8_t37_task_mutation_policy.py tests/jobs/test_p8_t37_analysis_scope.py tests/unit_services/test_p8_t37_plan_reuse.py app/services/task_plans.py app/jobs/source_activity.py
```

**compile** — exit `0`.

```bash
/data/p10-t23-tools/bin/python -m compileall -q tests/shared/task_plan_support.py tests/unit_services/test_p8_t37_task_mutation_policy.py tests/jobs/test_p8_t37_analysis_scope.py tests/unit_services/test_p8_t37_plan_reuse.py app/services/task_plans.py app/jobs/source_activity.py app/jobs/handlers.py app/models/managers/agent_task_manager.py app/api/v1/endpoints/tasks.py app/agent/toolset/tasks.py cli/main.py
```

**diff-check** — exit `0`.

```bash
git diff --check 8fdd83f5b63b85798ae0044226a2f5a6385dc035..HEAD
```

**black-app_jobs_handlers.py** — exit `0`.

```bash
/data/p10-t23-tools/bin/black --check --line-ranges 7-7 --line-ranges 96-96 --line-ranges 1676-1682 app/jobs/handlers.py
```

**black-app_models_managers_agent_task_manager.py** — exit `0`.

```bash
/data/p10-t23-tools/bin/black --check --line-ranges 93-93 --line-ranges 105-105 --line-ranges 107-107 --line-ranges 1242-1242 --line-ranges 1244-1246 app/models/managers/agent_task_manager.py
```

**black-app_api_v1_endpoints_tasks.py** — exit `0`.

```bash
/data/p10-t23-tools/bin/black --check --line-ranges 91-91 --line-ranges 93-94 --line-ranges 369-376 --line-ranges 378-379 --line-ranges 381-381 --line-ranges 383-387 app/api/v1/endpoints/tasks.py
```

**black-app_agent_toolset_tasks.py** — exit `0`.

```bash
/data/p10-t23-tools/bin/black --check --line-ranges 616-616 --line-ranges 618-643 --line-ranges 645-645 --line-ranges 648-648 --line-ranges 749-749 --line-ranges 752-752 --line-ranges 773-775 app/agent/toolset/tasks.py
```

**black-cli_main.py** — exit `0`.

```bash
/data/p10-t23-tools/bin/black --check --line-ranges 645-645 --line-ranges 652-652 --line-ranges 662-674 --line-ranges 676-687 --line-ranges 702-702 cli/main.py
```

New focused tests: **60 passed, 0 failed, 0 skipped**. Collect-only: **60 collected**. Scoped syntax/whitespace/format/import-order checks exit 0. Large legacy modules were Black-checked only over the listed changed line ranges; this is not a full-repository style verdict.

### Advisory failures: not PASS

Unchanged `tests/collection/test_source_job_admission.py`: **33 passed, 3 failed, 0 skipped; exit 1**, both at the exact implementation SHA above and at baseline source `8fdd83f5b63b85798ae0044226a2f5a6385dc035`:

- `test_busy_task_keeps_schedule_trigger_and_once_state[False-True]`
- `test_scheduler_does_not_count_busy_occurrence_as_enqueued`
- `test_web_queue_reports_existing_job_without_claiming_it`

The isolated schedule/timezone paths lack deployment settings and return invalid_schedule; the existing web AST fixture imports a real permission/config chain requiring SECRET_KEY/POSTGRES_URL. No real configuration was supplied and no existing tests were edited/weakened. Matching baseline/candidate failures show no delta in this run; they do not certify those behaviors. Local environment/harness reconciliation is still required.

Baseline command (exit 1, 33 passed / 3 failed) was the advisory command above from a detached baseline-source worktree. A second baseline run used **new untracked test overlays**, not a clean committed test-tree revision:

```bash
PYTHONPATH=tests:. /data/p10-t23-tools/bin/python -m pytest --noconftest -c /dev/null --rootdir=. -p no:cacheprovider -q tests/jobs/test_p8_t37_analysis_scope.py::test_new_discovery_source_reaches_the_actual_analyzer tests/unit_services/test_p8_t37_task_mutation_policy.py::test_planned_to_legacy_transition_matches_across_adapters
```

Result: **0 passed, 13 failed, 0 skipped; exit 1**: the discovery test and all 12 parameterizations of the named transition test (API/CLI/chat × digest/prune/learn/reflect). Baseline chat also retains the stale payload.scenario_id. The same assertions are green in the 60-test candidate suite. Initial newly authored fixture gaps (TaskResponse/validate_cron doubles) were fixed without relaxing assertions.

## Isolation limits and required local audit

New tests exercise actual source-local policy and worker/resolver functions. Adapter harnesses compile the unchanged API/CLI/chat function AST with local dependency/decorator doubles; this does **not** exercise real app startup, routing/auth middleware, SQL locking, transaction integration, scheduler settlement, provider availability or full ORM persistence. --noconftest is intentionally restricted to these proven source-isolated checks; do not copy it to DB tests.

The owner permits heavy/environment-dependent checks to be handed to a local agent. They are **NOT RUN**, not skipped test cases and not PASS. On the exact locally checked-out PR SHA, after reviewing current AGENTS, tests/AGENTS, test-runner and DB rules, run the focused real integration checks (no broad/full suite):

```bash
.venv/bin/python -m pytest -o addopts="" -p no:cacheprovider -q tests/api/test_p8_t29_task_plan_persistence.py tests/jobs/test_p8_t29_social_launch.py tests/api/test_p8_t29_task_plan_interfaces.py tests/agent/test_p8_t29_chat_plan_mutations.py tests/cli/test_cli_task_run.py tests/jobs/test_task_pending_occurrence.py
```

Prerequisites: owner-authorized isolated TEST_POSTGRES_URL; validated DB_TEST_SCHEMA and exclusive sequential schema ownership; reviewed compatible migration 0112/schema state; local-only secrets/config (never post values); mocked providers/no paid calls. Keep root conftest/DB guards enabled. Track and remove all synthetic rows on both success and failure, including committed fixture records; verify cleanup. Stop if target/schema authorization is unclear. Rerun the unchanged advisory suite with appropriate local settings and record actual failures rather than weakening it. For every run report exact SHA, command, exit, passed/failed/skipped, failure node IDs and cleanup. If tests reveal a behavioral defect, fix only this task's scope and rerun at the new SHA.

## Coordinated review of #243 and #245

- #245 is independently based on current dev, not stacked on #243. Review both, but neither approval automatically accepts the other.
- Resolve and record the actual heads before review; #243's previous utility-cleanup head was `9b51c3a681cc59039b67aedab6589e17fc6522ef`. Use an isolated integration-review branch for a combined candidate, never merge into dev for testing. Report its exact SHA and rerun both focused test sets.
- Source scopes are separate, but **do not blindly merge old registry/card changes**: current dev has reused P10-T23 for a different accepted documentation task. Identify old #243 by PR/branch/head; have its custodian reconcile stale task-ID references without overwriting accepted dev documentation.
- P8-T35 owns parent planning documents. Request its custodian to add the P8-T37 reverse link; leave active P8-T35/P8-T36/P9-T07 implementations alone.
- P8-T29 stays **BLOCKED**: its separate bounded VK newsfeed.search/wall.get availability probes timed out. Source-only tests cannot close that live-evidence gate or migration-evidence caveats. No DONE or parent-phase acceptance here.
- The P8-T37 claim/READY row exists on the task branch only. Registry publication to dev was blocked before execution. Local designated custodian must synchronize only the owned row/card into fresh dev, not a stale whole registry.

READY here means implementation/evidence handed off under the owner's explicit local-check deferral, **not independent acceptance**, all tests green, or authorization to merge. PR body/comments record final documentation-only head verification. Retain both unmerged PR branches for the auditor; remove only this task's temporary baseline overlays/worktree. No DB residue exists because no DB was accessed.

## Independent audit and local validation handoff

The independent source review found no concrete remaining defect in discovery-to-analysis scope, shared API/CLI/chat mutation policy, retired projection cleanup, or parse/serialization/platform-predicate deduplication. Existing permission, tenant, active/platform, admission, transaction, scheduling, and execution guards remain at their respective boundaries.

The exact code candidate was `f695e6478537733b92973f1ba50ed9cc51bf2fba` (PR #245 head `9169f23063fd946ade42960bd86678779edf116f` as its second parent). Focused P8 source tests: **60 passed, 0 failed, 0 skipped**; collection: **60 collected**. Scoped static/compile/diff and five changed-line Black checks exited `0`.

```bash
PYTHONPATH=tests:. /Users/admin/Projects/social-media-ai/.venv/bin/python -m pytest --noconftest -c /dev/null --rootdir=. -p no:cacheprovider -q tests/unit_services/test_p8_t37_task_mutation_policy.py tests/jobs/test_p8_t37_analysis_scope.py tests/unit_services/test_p8_t37_plan_reuse.py
```

Collection used the same command with `--collect-only -q`. P10-T25 source checks on this candidate were `python -m unittest discover -s tests/unit_services -p test_p10_t23_utility_cleanup.py -v` (**14 passed**) and `PYTHONPATH=tests:. .../python -m pytest -c /dev/null --rootdir=. --noconftest -p no:cacheprovider -q tests/unit_services/test_p10_t23_utility_cleanup.py` (**14 passed + 505 subtests**). P10-T19 permission declaration check was `/Users/admin/Projects/social-media-ai/.venv/bin/python tests/security/test_tool_dispatch_permissions.py` (**16 passed**). Static command details and exit codes are in the P10-T25 audit.

With root conftest and DB guards enabled, the focused six-file command below exited `0` on candidate `261ef966dccccdadf2511b8230dd39f8e22d0be1`: **70 passed, 0 failed, 0 skipped** (8 warnings, 42.08s). The refreshed candidate `f695e6478537733b92973f1ba50ed9cc51bf2fba` has an identical `app/`, `tests/`, and `cli/` tree to that DB-tested candidate; its only intervening committed changes are ROADMAP/docs updates. Target was isolated `localhost:5432/social_manager`, schema `test_schema`, Alembic `0112`; no public database was accessed. All 53 table row counts and primary-key hashes matched before/after; `jobs` and `ai_analytics` remained at 0 and no synthetic residue or active DB sessions remained. A repeated DB run on `f695e647` was not started because a foreign P9-T07 DB writer remained active; it is **UNRUN on that exact SHA**.

```bash
.venv/bin/python -m pytest -o addopts="" -p no:cacheprovider -q tests/api/test_p8_t29_task_plan_persistence.py tests/jobs/test_p8_t29_social_launch.py tests/api/test_p8_t29_task_plan_interfaces.py tests/agent/test_p8_t29_chat_plan_mutations.py tests/cli/test_cli_task_run.py tests/jobs/test_task_pending_occurrence.py
```

The advisory suite `tests/collection/test_source_job_admission.py` has a recorded no-dotenv invocation that is **NOT PASS**: candidate `f695e647` exited `1` with **33 passed, 3 failed, 0 skipped**, with failures `test_busy_task_keeps_schedule_trigger_and_once_state[False-True]`, `test_scheduler_does_not_count_busy_occurrence_as_enqueued`, and `test_web_queue_reports_existing_job_without_claiming_it`. They arise when deployment settings are absent. For a comparable rerun, baseline `dc694f6d` and candidate `f695e647` both passed **36 tests, 0 failed, 0 skipped**, exit `0`, when the candidate process silently loaded the same root `.env` values into memory; no values were printed, `--noconftest` remained in effect, and no DB connection was used. The no-dotenv failure is retained as a limitation, not relabeled PASS; no tests were weakened.

```bash
PYTHONPATH=tests:. /Users/admin/Projects/social-media-ai/.venv/bin/python -m pytest --noconftest -c /dev/null --rootdir=. -p no:cacheprovider -o asyncio_mode=auto -q tests/collection/test_source_job_admission.py
```

The comparable candidate rerun used the same pytest arguments in a short Python wrapper that called `dotenv.load_dotenv('/Users/admin/Projects/social-media-ai/.env')` before `pytest.main`; it did not print environment values. Baseline ran the command above from the main repository root, where pytest loaded that same local `.env` automatically.

Integration completed: merge candidate `f695e6478537733b92973f1ba50ed9cc51bf2fba` was pushed to `dev` as a descendant of fresh base `dc694f6dd5cde498869824e2f9f447bdebb8321c`; evidence/documentation sync commit `759e0b267a6a6b69460b7c54fe9646fc99d12c0a` followed it. PR #245 head `9169f23063fd946ade42960bd86678779edf116f` is the merge's second parent. PR #243 head `9b51c3a681cc59039b67aedab6589e17fc6522ef` was already an ancestor of dev, integrated through merge `2fe129cc92579228c87e1ab6b6902b1cec73fbab`; the PR itself was closed without merge and is not described as separately merged. P10-T25 evidence is recorded in its audit file. This is integration, not owner acceptance: P8-T37 remains READY and P8 remains active pending owner acceptance; P8-T29 remains BLOCKED on its separate VK live-evidence gate.

## Owner acceptance and closeout

The owner explicitly accepted P8-T37 on 2026-10-26. Local audit re-ran the six guarded DB suites from this report on the integrated dev (merge `f695e6478537733b92973f1ba50ed9cc51bf2fba`, documentation sync `759e0b267a6a6b69460b7c54fe9646fc99d12c0a`, dev tip `cd1d55d8`): 70 passed / 0 failed / 0 skipped, exit 0, in a re-seeded isolated `test_schema` on the common local PostgreSQL; the working `public` schema was not targeted and the test schema was re-created with reference rows only after the run. This closes the "exact f695 DB rerun UNRUN" gap. P8-T37 is closed: the task card and its ROADMAP row were removed, and the parent phase records the accepted SHA. P8-T29 remains BLOCKED on its separate VK live-evidence gate.

## Registry publication update

Owner requested pre-audit visibility in dev. Canonical active card: P8-T37 (closed 2026-10-26; card removed at closeout, this report is the surviving evidence); companion PR #243 is now [P10-T25](../tasks/p10-t25.md), without modifying accepted P10-T23. This report is validation evidence, not an accepted audit verdict. A registry-only update published both cards/reports, own queue rows and minimal parent backlinks into dev. The pending-registry/ID-reconciliation and open/unmerged statements in this historical section describe that earlier state; the independent audit and integration sections above supersede them. PR #245 HEAD `9169f23063fd946ade42960bd86678779edf116f` had its earlier documented checks; its latest-dev combined integration candidate is recorded and tested in the independent audit section.
