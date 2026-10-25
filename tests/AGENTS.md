# Test directory contract

Use [test-runner](../.agents/skills/test-runner/SKILL.md) for assigned test work.
Read only the task card and relevant [Tests](../docs/COLLABORATION.md#tests) /
[Database](../docs/COLLABORATION.md#database) procedures, not the whole docs tree.

## Layout and authoring

- Group suites/helpers/browser assets by meaning in `tests/<domain>/` (billing,
  security, jobs, web, collection, etc.); one concern per suite.
- New/meaningfully edited suites: `test_<task_id>_<topic>.py`, e.g.
  `billing/test_p2_t15_cap_replay.py`; other assets use the same ID/topic stem.
  Existing task-named suites retain their original ID; record later use in the
  current task card's explicit test paths.
- Shared presets/factories/doubles/import helpers: `tests/shared/`, named by
  function (`<topic>_fixtures.py`, `<topic>_support.py`), without a consumer's ID.
- Root keeps AGENTS, conftest/bootstrap and package metadata. Move legacy root
  helpers only with all consumers; preserve path-sensitive loaders. No unrelated
  bulk moves. Prefer stable shared imports; never bare-import a cross-domain suite.
- Only assigned files; no source edits/new tests for run-only assignments.
  Strengthen observable contracts, reuse fixtures/managers and mock paid/external
  writes. Never weaken assertions to get PASS; ask the parent about ambiguity.
- English, Black/isort line 120, changed files only. No committed logs/caches.

## Validation and evidence

Classify by actual imports/fixtures/resources, not filename. DB suites require
root conftest, explicit validated test schema and sequential exclusive ownership;
`--noconftest` and parallelism require proven no-DB isolation. Preserve tenant/
permission and root event-loop guards. Collect affected paths after import/enum/
layout edits. Apply database cleanup and public-write limits from COLLABORATION.

Return exact SHA, command/scope, exit/counts, failure names/cause and cleanup.
The parent records this and **documentation actually consulted** (path/section,
purpose) in the task card/PR; long diagnostics are linked, not copied into chat.
Unknown is not zero. No executor self-acceptance. Session deletion failure is
housekeeping, not a failed test or a reason to reopen an accepted task.
