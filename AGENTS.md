# AGENTS.md — AI Assistant

## Agent entry points and continuity
- Before work read `.agent/rules.md` (worktree/DB constraints) and `.agent/workflow.md` (session handoff); instructions here supersede older procedural rules on conflict.
- Docs: `docs/PRODUCT_PLAN.md` (product), `docs/BUSINESS_PRODUCTION_READINESS.md` (gates), `docs/design/README.md` (tasks/ownership), `docs/IMPLEMENTATION_STATUS.md` (delivered), `docs/DOCS_INDEX.md` (index).
- Subagents: `.codex/agents/` (Codex), `.kilo/agents/` (Kilo). `.agent/test-report.md` is human-audit evidence only — never main-context input.
- Local IDE/CLI: `$develop-social-media-ai-local`; remote GitHub: `$develop-social-media-ai`; same task board and PR handoff.
- Resume from the current PR handoff after verifying its SHA; old `.agent/tasks.md` / `.agent/state.json` entries are not fresh assignments.

## Collaboration workflow
- `dev` is the integration branch. Complete authorized tasks autonomously, without routine confirmations.
- Default: work in the current `dev` checkout when one sequential task owns its changes; inspect status first, preserve unrelated edits. Separate branch/worktree only for parallel tasks, PR review, risky experiments, conflicts, or an explicit owner request — never as routine ceremony.
- On completion in `dev`: show the proposed commit split/messages and offer to commit directly; a closeout phrase (see Style and closeout triggers) authorizes those commits without re-asking. No PR for ordinary sequential work. Never push `dev`, merge, force-push, deploy, or touch production data without explicit authorization.
- Tests: every pytest run is delegated through Kilo's `test-orchestrator` to its `test-runner` child agent, which uses the connected `pare-test` MCP runner. Neither coordinator may run pytest directly. Closeout and handoff protocol: `docs/COLLABORATION.md`.

## Project rules
- Extensible assistant on the existing modular monolith: read `README.md`, `docs/DOCS_INDEX.md`, then relevant `docs/*.md` before changing behavior.
- Verify current branch/code before acting on plans or boards; docs may describe proposed work — never assume a planned feature exists.
- Active runtime (PostgreSQL schedules/jobs, worker, Telegram/MAX long-poll listener) is one process: `.venv/bin/python -m app.runtime`; do not start duplicate owners. No Celery/Redis in the active path; `app/celery/` and Streamlit are frozen without an explicit task.

## Python environment
- Repository root + project virtualenv only (`.venv/bin/python -m ...`, `.venv/bin/*`); no system Python, bare `pytest`/`pip`/`alembic`, no global installs.
- Every pytest invocation carries `-q --tb=short --no-header` (small output = small context).
- Commands:
  - Runtime: `.venv/bin/python -m app.runtime`
  - CLI: `.venv/bin/python -m cli.main ...`
  - Migration: `.venv/bin/python -m alembic upgrade head`
- All pytest runs: only via Kilo's `test-orchestrator` delegating to its `test-runner` child agent and `pare-test` MCP tool — never from either coordinator's shell.

## Data access and migrations
- Reuse ORM models, managers (`app/models/managers/`), service methods before adding DB access; extend a manager/service instead of duplicating queries across API/CLI/web/agent tools.
- No ad-hoc `engine`/`Session`/`AsyncSession`/`sessionmaker`; follow existing session and transaction conventions; respect workspace scoping, authorization, validation, manager-level rules; `settings.DB_SCHEMA` only, never hardcoded schemas.
- Alembic is the only migration path: no runtime table creation, no edits to applied migrations; review generated migrations; `alembic check` when warranted.

## Architecture and safety
- Small changes consistent with nearby code; reuse over new abstractions/dependencies/frameworks; no repo-wide formatting or unrelated refactors.
- Distinct concepts: `AgentScenario` = methodology; `AgentTask` = scheduled execution (sources/targets); multi-step business workflow = separate concept.
- Interactive access fails closed (unknown identity, missing membership/permission, revoked ⇒ deny); agent tools need explicit permissions and argument validation, re-checked at execution.
- External writes (send/publish/update/delete) are never a side effect of unrelated work; preserve dry-run defaults and approval gates; scope recipients/credentials to the owning workspace; never expose tokens, sessions, `.env` values, secrets in logs/tests/prompts/commits.
- Existing LLM clients and DB-managed provider/model config only; preserve usage/cost tracking and fallbacks; tests mock external APIs/LLMs — no real paid or destructive calls.
- Optional integrations must not block startup when unconfigured; per-source access limits stay explicit; a delivery channel does not imply content collection.

## Tests and verification
- All tests: Codex hands the task to Kilo's `test-orchestrator`; it delegates to its `test-runner` child, which calls the connected `pare-test` MCP tool with the requested paths and quiet pytest arguments.
- The runner uses the project `.venv`, passes `-o addopts=''`, and serializes work under R8.
- Trust only the delegate's final summary: `TEST RUN <PASS|FAIL|BLOCKED> commit=<hash> passed=<n> failed=<n> skipped=<n>` + one line per failure; never read `.agent/test-report.md` or raw logs into the main context.
- If Kilo or its MCP runner is unavailable, report BLOCKED and repair the connection; never fall back to running pytest in the coordinating agent.
- BLOCKED result ⇒ resolve the blocker or ask the owner; never silently re-run the suite.

## Style and closeout triggers
- `pyproject.toml`: Black + isort, line 120; format only changed/new files; preserve legacy style in old code.
- Code/docs in English; user-facing responses in Russian, brief: outcome, checks actually completed, remaining issues. No milestone/PRD numbering in comments, docstrings, fields, labels, filenames.
- Never commit `.env*`, credentials, sessions, generated data, local DB files.
- Completion summary: behavior changed, key files, exact tests run, migration implications, remaining risks; no silent scope expansion.
- Closeout phrases — explicit authorization to commit validated task-scoped work in `dev` and run the `docs/COLLABORATION.md` handoff protocol: `закрываю чат`, `чат закрываем`, `переходим в новый чат`, `завершаем чат`, `задача завершена`, `задача с подзадачами завершена`, `подготовь новый чат`. A closeout is not permission to mark unfinished work complete.
