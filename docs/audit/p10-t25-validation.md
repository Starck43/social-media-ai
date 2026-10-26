# P10-T25 — bounded utility cleanup evidence

[Task card](../tasks/p10-t25.md) · [PR #243](https://github.com/Starck43/social-media-ai/pull/243) · [local-helper/status comment](https://github.com/Starck43/social-media-ai/pull/243#issuecomment-6410152108)

## Revision and route

- Integration baseline: `e8501686dba9fcc6f22f03d7c909851ecba581fe`.
- Claim: `f02b6f81f16dd5f196c0a4e51987d9a28afe1474`; one new ROADMAP row plus the new card Execution record only.
- Exact tested application/test revision: `f49255b62cfa447e572aabe6f50982471a2ddbbe`, task branch `refactor/p10-t23-unused-utils`, clean before and after all checks.
- Environment: Linux, CPython 3.13.15; isolated tools in `/data/p10-t23-tools`: pytest 9.1.1, Black 26.10.1, isort 9.0.2. Not a Python 3.12 or production-environment validation.
- Requested native test-runner session could not start: `The tool capability failed unexpectedly`; no child URL/result. The executor then followed `.agents/skills/test-runner/SKILL.md` / current `COLLABORATION#Tests` (R10 workflow). This is executor evidence, not independent acceptance. No source edits between frozen-SHA checks.
- A subsequent evidence/registry-only commit may have a different HEAD; application/test blobs remain pinned above. The PR body records the exact final-HEAD rerun.

## Audit and change boundaries

`app/utils/cache.py` (187 lines) and `app/utils/json_utils.py` (11 lines) have no consumers under app/CLI/scripts/tests. Whole-repository symbol/string discovery and lazy-import inspection found no utility-module loader. They are removed, without replacing or changing active LLM retries, provider calls or JSON processing. This repository-only audit cannot certify unknown out-of-tree consumers.

`translit_slug(text)` retains its public signature, Russian mapping, lowercase-before-filter ordering and ASCII-only output. A module-level translation table and compiled filter replace the temporary Python list/loop. No benchmark or application-throughput speedup claim is made; the verified result is compatibility and a simpler execution path. No general utility framework or speculative abstraction is introduced.

Only the three utility paths, the new focused suite, this evidence, the task card and one ROADMAP row are owned. Open PRs #216/#217/#220/#224 have no implementation-path overlap. Visible Codex/queued lanes, AI callers, differing stemming algorithms, collection/attachments, auth/billing, runtime, migrations and secrets are untouched. Unpublished local Codex edits are not visible remotely; the checkout is isolated and cannot overwrite them.

## Results

| Scope | Exit | Passed | Failed | Skipped | Notes |
| --- | --- | --- | --- | --- | --- |
| Standalone unittest | 0 | 14 | 0 | 0 | Same suite as pytest |
| Isolated pytest | 0 | 14 | 0 | 0 | Also 505 passed subtests |
| Collection | 0 | N/A | N/A | N/A | 14 tests collected |
| Black / isort / compileall / whitespace | 0 each | N/A | N/A | N/A | Scoped static checks |
| Consumer search | 1 raw / 0 wrapper | N/A | N/A | N/A | grep 1 means no references |
| DB consumer integration | NOT RUN | unknown | unknown | unknown | Local exclusive schema required |
| Full application suite / real APIs | NOT RUN | unknown | unknown | unknown | Not in remote authorization/scope |

Failure names: **none in the executed test scope**. The duplicate runner executions do not constitute 28 distinct test cases. The 14 cases include each Russian letter/case, expansions/signs, empty/punctuation/mixed text, all 1,114,112 Unicode codepoints in bounded chunks and 200 seeded mixed strings against the frozen legacy algorithm. Compatibility evidence is not a production certification.

Baseline characterization ran against source `e8501686dba9fcc6f22f03d7c909851ecba581fe` with a new untracked test overlay before the rewrite: unittest exit 0, 14 passed / 0 failed / 0 skipped. That baseline was not a clean committed full test-tree revision. No unrelated existing suite or assertion was edited or weakened.

## Exact executed commands

All commands were executed from `/data/social-media-ai` at the frozen SHA above. The isolated pytest command intentionally ignores project configuration and root conftest **only for this proven no-DB suite**: imports are inspect/random/unittest plus `app.utils.translit`, with empty app/utility package initializers and no config, models, engines, fixtures or APIs. Never reuse these bypass flags for DB-backed suites.

### 1. unittest

```sh
python -m unittest discover -s tests/unit_services -p test_p10_t23_utility_cleanup.py -v
```

Observed exit: `0`. Expected exit 0; completed successfully.

### 2. pytest

```sh
/data/p10-t23-tools/bin/python -m pytest -c /dev/null --rootdir=. --noconftest -p no:cacheprovider -q tests/unit_services/test_p10_t23_utility_cleanup.py
```

Observed exit: `0`. Expected exit 0; completed successfully.

### 3. collection

```sh
/data/p10-t23-tools/bin/python -m pytest -c /dev/null --rootdir=. --noconftest -p no:cacheprovider -q tests/unit_services/test_p10_t23_utility_cleanup.py --collect-only
```

Observed exit: `0`. Expected exit 0; completed successfully.

### 4. black

```sh
/data/p10-t23-tools/bin/black --check app/utils/translit.py tests/unit_services/test_p10_t23_utility_cleanup.py
```

Observed exit: `0`. Expected exit 0; completed successfully.

### 5. isort

```sh
/data/p10-t23-tools/bin/isort --check-only app/utils/translit.py tests/unit_services/test_p10_t23_utility_cleanup.py
```

Observed exit: `0`. Expected exit 0; completed successfully.

### 6. compileall

```sh
python -m compileall -q app/utils tests/unit_services/test_p10_t23_utility_cleanup.py
```

Observed exit: `0`. Expected exit 0; completed successfully.

### 7. whitespace

```sh
git diff --check e8501686dba9fcc6f22f03d7c909851ecba581fe..HEAD
```

Observed exit: `0`. Expected exit 0; completed successfully.

### 8. consumers

```sh
git grep -nE '(app\.utils\.(cache|json_utils)|from app\.utils import.*(cache|json_utils)|call_with_retry|RetryCache|decode_unicode_json|format_json_with_unicode_decode)' -- app cli scripts tests
```

Observed exit: `1`. Expected no matches; wrapper validates exit 1 and returns 0. No test failure.

## Local independent acceptance and DB integration — not run remotely

1. Resolve the current PR head and checkout that exact SHA in an isolated task worktree. Do not merge the PR or alter Codex lanes. Record the tested SHA, commands, exits, passed/failed/skipped and failure names in a PR comment.
2. Independent source-only rerun (stdlib; no environment or DB needed):

```sh
python -m unittest discover -s tests/unit_services -p test_p10_t23_utility_cleanup.py -v
```

3. For the existing consumer integration suite, use the project's installed venv/dependencies and locally configured encryption/settings secrets; never post their values. Resolve an authorized `TEST_POSTGRES_URL` and a validated isolated `DB_TEST_SCHEMA`, compatible reviewed Alembic revisions and **exclusive sequential ownership across all local worktrees/runners** before running. No public/production data.

```sh
: "${TEST_POSTGRES_URL:?set authorized test database URL}" "${DB_TEST_SCHEMA:?set validated isolated test schema}"
.venv/bin/python -m pytest tests/ai/test_chain_resolver.py -q -p no:cacheprovider -o addopts=""
```

Keep root conftest and tenant/event-loop/target guards. Do not use `--noconftest` or invent a DB target. The existing chain suite's cleanup calls `AIAnalytics.objects.delete()` broadly: never run it against an occupied/reference-data schema. Schema preparation/migrations require the documented test-target validation and sequential ownership; no runtime DDL or production apply. Mock any external provider/write route.

4. After success/failure/interruption, stop owned surviving processes, remove all synthetic records (including separately committed fixtures) and verify none remain. Do not remove another run's data; preserve migrations/reference data. Post revision-specific evidence back to this PR. Unknown/unrun is not PASS and is not a pytest skip count.

## Cleanup / acceptance boundary

No DB was connected and no synthetic/external data was created remotely; DB cleanup is N/A. One owned clean checkout is retained for review/resume at `/data/social-media-ai`; its task branch is unmerged and not deleted. No foreign files/worktrees/branches, runtime or production state were touched. Native session creation returned no handle; there is no owned child handle to stop/delete. READY means executor handoff only: owner acceptance, local validation and merge are pending, not DONE; no parent phase is closed.

## Canonical registry identity

This is pre-acceptance validation evidence for PR #243, now registered as P10-T25. References to P10-T23 in old branches/tests/commands are historical; accepted docs/audit/p10-t23.md is a different task and must not be overwritten. Related follow-up: [P8-T37](../tasks/p8-t37.md). Registry publication adds documentation only, not solution code, acceptance or new test results.
