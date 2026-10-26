# Test conventions

Use the assigned task's test-runner skill and read only its card plus the
relevant [test](../docs/COLLABORATION.md#tests) or
[database](../docs/COLLABORATION.md#database) procedure.

- Group tests by domain under `tests/<domain>/` (for example `billing/`,
  `security/`, `jobs/`, `web/`, `collection/`). Keep root for pytest bootstrap,
  `conftest.py` and package metadata.
- Name new task tests `test_<task-id>_<topic>.py`, e.g.
  `tests/billing/test_p2_t15_cap_replay.py`. Put reusable fixtures, presets,
  factories and doubles in `tests/shared/`; use descriptive shared names without
  a consumer task ID. Group other assets by purpose and include the task ID.
- Move existing files only when assigned and after updating every importer and
  path-sensitive loader. Avoid unrelated tree reshuffles. Do not bare-import a
  suite across domains; use stable shared helpers.
- Classify DB use by fixtures/imports/resources, not test filename. DB tests use
  root conftest, validated `test_schema` and serialized exclusive ownership.
  Never create test records in public schema without explicit Superuser approval.
  Remove and verify all synthetic test-schema records after tests, including
  committed fixtures. Do not bypass `conftest` or parallelize without proving
  isolation.
- Strengthen observable contracts; preserve authorization and tenant checks.
  Mock paid APIs/external writes. Never weaken assertions to obtain PASS. A
  run-only tester changes no source and authors no tests. No full suite without
  owner request. Do not commit logs, caches or generated test data.

Report exact test paths, command, tested revision, exit/counts, failures and
cause, unrun checks, DB cleanup, and documentation actually read in the task
card or PR body. Unknown counts remain unknown.
