# AGENTS.md — AI Assistant

## Entry points and task tracking
- Read before work: `docs/RULES.md` (environment/DB/delegation constraints R1–R10) and `docs/COLLABORATION.md` (handoff, commits, closeout). Instructions here supersede older procedural rules on conflict.
- Task truth: root `ROADMAP.md` is the live registry of open tasks/questions (ID + link only); `docs/phases/README.md` is the global phase list, one task file per phase (`P<n>-T<nn>`, statuses TODO/IN_PROGRESS/BLOCKED/READY/DONE/DROPPED). A phase closes only when all its tasks are DONE and Codex signs the phase approval block.
- Claim a task in ROADMAP.md and its phase file before work; on resolution update the phase file to READY with evidence, and only the coordinating Codex then reviews the diff/evidence at the exact SHA, marks the task DONE and deletes its ROADMAP row. No subagent removes resolved ROADMAP rows or self-accepts as DONE. New questions that need separate elaboration go to ROADMAP.md `## Questions` (Q-<n>) and are deleted when resolved.
- Docs: `docs/DOCS_INDEX.md` (index), `docs/PRODUCT_PLAN.md` (product), `docs/BUSINESS_PRODUCTION_READINESS.md` (gates), `docs/IMPLEMENTATION_STATUS.md` (delivered), `docs/design/assistant-shell.md` (shell contract).
- Agent roles: Codex = main coordinator and approver (accepts READY tasks, closes phases; does not run pytest or commit directly). OpenCode = executor, primary pytest runner (`.agents/skills/test-runner/`), and the only local committer. Kilo = secondary test route (only when OpenCode is BLOCKED). Remote agents = GitHub PR lanes (`.agents/skills/remote/`), local relay via `.agents/skills/remote-agent-helper/`.
- Subagents/test delegation: `.codex/agents/test-runner.toml` (OpenCode `QwenPlan/deepseek-v4.1-flash-1017`, primary), `.codex/agents/kilo-test-runner.toml` (Kilo `minimax-m3.1-flash`, secondary). Evidence in `.agents/tmp/` is human-audit only — never main-context input. All test-file creation, editing and validation is delegated wholesale to test subagents working under `tests/AGENTS.md` (directory contract; compact `TEST EDIT`/`TEST RUN` replies, evidence stays in files).
- `.agents/tmp/` working files: agents may create, read, edit, and remove their own task files and temporary working artifacts under `.agents/tmp/` without asking the owner for per-file approval.
- Treat those files as transient: after the task or delegated run completes, delete each own file that is no longer needed (task prompts, reports, scratch notes, logs). Retain files only while an active task/agent still needs them or when the owner explicitly requests preservation; coordinate before removing another active agent's artifact.
- `.agents/tmp/` is never a location for durable task truth or checked-in deliverables: put durable outcomes in canonical docs/PRs and keep `.agents/tmp/` ignored/uncommitted.
- Local IDE/CLI: `$local`; remote GitHub: `$remote`; same registry, phases and PR handoff.
- Resume from the current handoff after verifying its SHA. Git and PRs hold detailed history; generated Codex memories are optional recall, never project policy or task truth.

## Collaboration workflow
- `dev` is the integration branch. Complete authorized tasks autonomously, without routine confirmations.
- Shared documentation: synchronize `ROADMAP.md` and owned `docs/` edits with fresh `dev` and other agents' relevant active work before claiming IDs, editing and publishing. Preserve foreign ownership, evidence and statuses; recheck unique task/question IDs and links after merging concurrent changes. Follow the shared-document synchronization procedure in `docs/COLLABORATION.md`; never publish stale full-file copies.
- Default: work in the current `dev` checkout when one sequential task owns its changes; inspect status first, preserve unrelated edits. Separate branch/worktree only for parallel tasks, PR review, risky experiments, conflicts, or an explicit owner request — never as routine ceremony.
- Independent implementation lanes require separate worktrees and exclusive files; one sequential delegated task may use the current `dev` checkout with explicit file ownership. Shared-DB tests always remain sequential. Never bypass HEAD, permission or ownership blockers.
- On completion in `dev`: follow commit and closeout rules in `docs/COLLABORATION.md`. Prepare and review a scoped staged diff, then delegate every local `git commit` to OpenCode; the coordinating Codex does not commit directly. No PR for ordinary sequential work. Never push `dev`, merge, force-push, deploy, or touch production data without explicit authorization.
- Tests: assign every pytest scope per `docs/RULES.md` R10 — primary OpenCode `test-runner`, shared test schema sequential (R8), Codex never runs pytest; remote agents use the no-DB exception in `.agents/skills/remote/`. Test-file work inside `tests/` follows the scoped contract in `tests/AGENTS.md`.
- Keep context small: lazy reads (rg, line ranges); read only the selected phase file, subsystem doc and call sites; reuse completed evidence at its exact revision; one bounded task per session/PR; summaries in chat, evidence in files/PRs.

## Understand common requests
- “Audit completed work”, “check what is done”, or “find gaps” means compare `docs/IMPLEMENTATION_STATUS.md` with current code and PR evidence. Report verified findings and a prioritized fix plan; do not change code unless asked to fix or implement.
- For one named feature, task, or PR, inspect only that slice. Keep architectural decisions and acceptance with Codex.
- “Take the next active task” means select one unclaimed TODO row from `ROADMAP.md` (respecting phase priority in `docs/phases/README.md`), verify current code and ownership, claim it, then implement that item. It does not mean execute the whole board.
- “Implement/fix the audit findings” means the accepted items already live in the phase files; implement dependency-ready items, verify each before READY, and make per-item commits when the request or active Goal asks for them.
- Run focused checks appropriate to changed behavior. Request broad suites only when warranted; all pytest commands go through the primary OpenCode test route. Docs-only edits do not need pytest.

## Project rules
- Extensible assistant on the existing modular monolith: read `README.md`, `docs/DOCS_INDEX.md`, then relevant `docs/*.md` before changing behavior.
- Verify current branch/code before acting on plans or boards; docs may describe proposed work — never assume a planned feature exists.
- Active runtime (PostgreSQL schedules/jobs, worker, Telegram/MAX long-poll listener) is one process: `.venv/bin/python -m app.runtime`; do not start duplicate owners. No Celery/Redis in the active path; `app/celery/` and Streamlit are frozen without an explicit task.

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

## Style and closeout triggers
- `pyproject.toml`: Black + isort, line 120; format only changed/new files; preserve legacy style in old code.
- Code/docs in English; user-facing responses in Russian, brief: outcome, checks actually completed, remaining issues. No milestone/PRD numbering in comments, docstrings, fields, labels, filenames.
- Never commit `.env*`, credentials, sessions, generated data, local DB files, `.agents/tmp/`.
- Completion summary: behavior changed, key files, exact tests run, migration implications, remaining risks; no silent scope expansion.
- Closeout phrases — explicit authorization to commit validated task-scoped work in `dev` and run the `docs/COLLABORATION.md` handoff protocol: `закрываю чат`, `чат закрываем`, `переходим в новый чат`, `завершаем чат`, `задача завершена`, `задача с подзадачами завершена`, `подготовь новый чат`. A closeout is not permission to mark unfinished work complete.
