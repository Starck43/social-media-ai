# tests/AGENTS.md — directory contract for test subagents

Audience: any subagent asked to create, edit, or validate test files under
`tests/`. Parent rules stay authoritative and are read lazily, per section:
`AGENTS.md` (roles), `docs/RULES.md` (R2 collect-after-enums, R8 shared test
schema is sequential, R10 primary route), `docs/COLLABORATION.md` (handoff),
`.agents/skills/test-runner/SKILL.md` (execution contract). Never duplicate
them here.

## Layout

Domain directories mirror `app/` and the 2026-10 regroup:

- `admin/ agent/ ai/ api/ billing/ channels/ chat/ cli/ collection/ infra/
  jobs/ models/ notifications/ privacy/ security/ web/`
- Established slots keep their roles: `acceptance/` (PG/ASGI acceptance),
  `offline/` (browser/node gates), `unit_services/`, `unit_web/` (focused
  component units), `logs/` (run artifacts, never committed).
- `tests/` root holds only: `conftest.py`, shared helper scripts
  (`*_fixtures.py`, `*_layout.py`, `*_support.py`,
  `source_import_isolation.py`, `telegram_admission_guard.py`), `.cjs`
  browser assets, `wheel_runtime_smoke.py`.
- A new suite lands in the domain dir of the app code it tests
  (`tests/<domain>/test_<topic>.py`); one concern per file; follow the
  closest existing suite as the template.

## Import rules (enforced by the regroup)

- Never bare-import a test module that lives in another domain dir
  (`from test_other import x`) — pytest prepends only each collected file's
  own directory. Cross-group helpers load by path with the repo's
  importlib spec-loader idiom (`_load_dep`, see
  `ai/test_partial_analysis_coverage.py`); the loader prepends the loaded
  module's directory to `sys.path`, so chains stay resolvable.
- Shared root helpers are consumed as `from source_import_isolation import
  load_isolated_source` or via `run_path("tests/<name>.py")`. They must
  stay in root; root-relative paths in them are load-bearing.
- `tests/conftest.py` is the single bootstrap: it resolves the DB redirect
  (`DB_TEST_SCHEMA`) and owns the session event loop. DB suites must run
  through it; DB acceptance suites (`test_admin_model_billing_acceptance.py`,
  `test_digest_send_*_acceptance.py`) carry STOP guards that reject
  `--noconftest` and any non-localhost `social_manager/test_schema` target.
- Run `pytest --co` after enum/import changes (R2).

## DB vs no-DB classification

- A suite that touches `app.core.database`, ORM sessions, or the test
  schema is DB-backed: strictly sequential through the primary
  test-runner route (R8). Conventional suffixes `_db` and `_acceptance`
  mark such suites; the decisive fact is resource use, not the name.
- Everything else is no-DB and may run in parallel only if the task file
  proves independence.
- `pytest.mark.tenancy` suites disable the platform-owner bypass; scope
  context and permissions must come from the suite itself.

## Creating / editing suites

- Derive assertions from current app behavior; never weaken a contract to
  make a run pass. Preserve and strengthen; ambiguous behavior change
  goes back to the parent, not into the test.
- Reuse managers, existing fixtures, and doubles; do not duplicate DB
  access paths outside what managers/services already provide.
- File ownership is exactly what the task lists. Never edit `app/` code,
  migrations, config, or docs unless the parent explicitly assigned them.
- Style: Black + isort (profile `black`, line 120), type hints, English
  comments; no phase/PRD numbering in names or comments.
- Test files belong to the repo; `tests/logs/` content, caches, and
  `.DS_Store` never get committed.

## Running checks

- Primary route only: OpenCode test-runner per R10
  (`.agents/skills/test-runner/SKILL.md` for the exact command template).
- Scoped isolated run: `.venv/bin/python -m pytest --noconftest -o
  addopts= -q --tb=short --no-header <paths>` — no-DB suites only.
- Shared-DB suites: run through the root conftest with the resolved
  `DB_TEST_SCHEMA`; sequential; never combined with another DB scope.
- Collection first (`pytest --co`) when imports/enums changed or the
  layout moved; never start a suite on an unknown or dirty schema state
  (R4).

## Reporting back (parent token budget)

Evidence lives in files, not in the reply. The parent does not open
`.agents/tmp/test-report.md`.

- Test creation/edit tasks — final reply, ≤120 tokens:

  ```
  TEST EDIT <APPLIED|BLOCKED> files=<k> verify=<collect-only ok | N passed / M failed | not run>
  - <one-line blocker>            (only when BLOCKED)
  ```

- Test runs — the standard `TEST RUN` block from the test-runner contract
  (verdict, counters, one-line failure reasons; `TEST RUN BLOCKED
  reason=...` on any safeguard stop).

- Shared tasks write their working files under `.agents/tmp/` (task
  prompts, reports, scratch) and delete them at close; `.agents/tmp/` is
  local-only and never committed. No secrets, no raw logs, no
  environment dumps in evidence or replies.
- Subagents never commit; OpenCode commits validated task-scoped work
  per `docs/COLLABORATION.md`.
