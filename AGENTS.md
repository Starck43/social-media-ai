# AGENTS.md — AI Assistant

## Agent entry points and continuity
- Read `.agent/rules.md` and `.agent/workflow.md` before work; they define local worktree/test restrictions and session handoff.
- Product direction: `docs/PRODUCT_PLAN.md`; engineering gates: `docs/BUSINESS_PRODUCTION_READINESS.md`; current tasks/ownership: `docs/design/README.md`; delivered foundations: `docs/IMPLEMENTATION_STATUS.md`.
- Local IDE/CLI: use `$develop-social-media-ai-local`. Remote GitHub work: use `$develop-social-media-ai`. Both use the same task board and PR handoff.
- Resume from the current PR handoff after verifying its SHA. Do not treat old `.agent/tasks.md` or `.agent/state.json` entries as fresh assignments.

## Collaboration workflow

- The owner is the sole developer; development is local and `dev` is the integration branch.
- Complete authorized tasks autonomously without repeated routine confirmations.
  Work directly in the current `dev` checkout by default when one sequential task
  owns its changes and no unrelated work would be mixed. Always inspect status and
  preserve unrelated changes.
- Use a separate branch/worktree for parallel tasks, PR review, risky experiments,
  conflicting owner changes, or when the owner explicitly requests a PR. Do not
  create one only as routine ceremony for each local task.
- After a coherent task is complete and validated in `dev`, show the proposed
  commit split/messages and offer to create those commits directly in `dev`.
  A closeout phrase listed below is explicit authorization to create the appropriate
  local commits without asking again. Do not require a PR for ordinary sequential
  local work. Do not push `dev`, merge, force-push, deploy, or modify production
  data without explicit authorization.
- Run focused tests only when needed for new features or a concrete correctness concern.
  Do not routinely run the full suite. Delegate broad testing to local OpenCode or Kilo;
  this delegation is authorized. See `docs/COLLABORATION.md` for the handoff protocol.
- Read `.agent/rules.md` for shared database and worktree constraints. New workflow
  instructions here supersede older procedural rules where they conflict.
- Report results briefly in Russian: outcome, checks actually completed, and remaining issues.
- After each completed substantial task, offer a new chat and provide a compact copyable
  handoff with directory, branch/commit/PR, completed work, validation, and next action.
  Do not claim to close or create a chat automatically.
- Treat the user's phrases `закрываю чат`, `чат закрываем`, `закрываем чат`, `завершаем чат`,
  `задача завершена`, `задача с подзадачами завершена`, and `подготовь новый чат`
  as an explicit closeout request. Before the final response, record the actual
  state in the existing board/ledger/PR when it changed. Commit each completed,
  validated and clearly scoped part of the current task locally in `dev`; do not
  include unrelated, incomplete or unvalidated changes. Then provide a copyable
  new-chat prompt with the project path, branch/revision, completed and unfinished
  work, checks, blockers, and the first next action. If work is unfinished, label
  it accurately; a closeout request is not permission to mark the task complete.

## Project rules
- The active product is an extensible assistant built on the existing modular monolith. Read `README.md` and `docs/DOCS_INDEX.md`, then the relevant `docs/*.md` before changing behavior.
- Verify the current branch and code before acting on plans or task boards: documentation can describe proposed work. Do not assume a planned feature is implemented.
- Active runtime: PostgreSQL-backed schedules/jobs, worker and Telegram/MAX long-poll listener. Do not add Celery/Redis dependencies to the active path or extend frozen legacy code (`app/celery/`, Streamlit) without an explicit task.
- `python -m app.runtime` already runs the scheduler, worker and channel listener. Do not start duplicate scheduler/listener owners accidentally.

## Python environment
- Run Python tools from the repository root and always use the project virtualenv: `.venv/bin/python -m ...` or executables under `.venv/bin/`.
- Do not use system Python, bare `pytest`, `pip`, or `alembic`; do not install dependencies globally.
- Common commands:
  - Runtime: `.venv/bin/python -m app.runtime`
  - CLI: `.venv/bin/python -m cli.main ...`
  - Migration: `.venv/bin/python -m alembic upgrade head`
  - Focused test: `.venv/bin/python -m pytest -o addopts='' tests/path/to/test_file.py -q`
  - One test: `.venv/bin/python -m pytest -o addopts='' tests/path/to/test_file.py::test_name -q`
  - Full suite, when warranted: `.venv/bin/python -m pytest -q`

## Data access and migrations
- Reuse existing SQLAlchemy ORM models, model managers, and service methods before adding database access. Check `app/models/` and `app/models/managers/` for an existing implementation.
- Do not create an ad-hoc engine, `Session`/`AsyncSession`, or `sessionmaker`. Use the project's existing database/session lifecycle and transaction conventions. If a needed operation is missing, extend the relevant manager/service rather than duplicating queries across API, CLI, web, or agent tools.
- Do not bypass workspace scoping, authorization, validation, or manager-level business rules with direct queries. Use `settings.DB_SCHEMA`; never hardcode a schema name.
- Alembic is the only schema migration path. Do not create tables at runtime or edit old migrations to change their historical behavior. Review generated migrations before applying them; run `alembic check` when model/schema changes warrant it.

## Architecture and safety
- Keep changes small and consistent with nearby code; prefer reuse over new abstractions, dependencies, frameworks, or parallel implementations. Do not perform repo-wide formatting or unrelated refactors.
- Keep these concepts separate: `AgentScenario` defines analysis methodology; `AgentTask` defines scheduled execution and its sources/targets; a multi-step business workflow is a separate concept.
- All interactive access must fail closed: unknown identity, missing membership, missing permission, or revoked access must not grant access. Agent tools need explicit permissions and argument validation; re-check access when executing the operation.
- External writes (send, publish, update, delete) must not be enabled as a side effect of unrelated work. Preserve dry-run/disabled defaults and required approval gates; scope recipients and credentials to the owning workspace. Never expose tokens, session strings, `.env` values, or raw secrets in logs, tests, prompts, or committed files.
- Use the existing LLM clients and DB-managed provider/model configuration. Preserve usage/cost tracking and fallback semantics. Tests must mock external APIs/LLM calls; do not make real paid or destructive calls.
- Optional integrations must not prevent startup when unconfigured. Keep source-specific access limits and capabilities explicit; do not imply that a delivery channel also supports collecting content.

## Tests and verification
- Do not run the entire test suite after every small edit. First run the narrowest relevant test file or test case, disabling default coverage overhead with `-o addopts=''` while iterating.
- Run broader tests for cross-cutting changes to tenancy/permissions, ORM models or migrations, jobs/retries, LLM accounting, delivery, runtime startup, or shared interfaces. Run the full suite before declaring a broad change complete when feasible.
- Reuse existing fixtures, mocks, and test helpers. Add or update regression tests for changed behavior; prefer deterministic tests without network, live credentials, or real database side effects.
- If a test cannot be run because an environment/service is unavailable, state that explicitly and report the exact commands and results. Never claim tests passed unless they were run.

## Style and handoff
- Follow `pyproject.toml`: Black + isort, line length 120. Format only changed/new files; preserve existing local style when editing legacy code.
- Committed code and documentation are in English; user-facing agent responses are in Russian. Keep milestone/PRD numbering out of code comments, docstrings, model fields, admin labels, and filenames.
- Never commit `.env*`, credentials, user sessions, generated data, or local database files.
- At completion, summarize the behavior changed, important files, exact tests run, migration implications, and remaining risks. Do not silently expand scope or rewrite working modules without a demonstrated need.
